from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections import defaultdict
from collections.abc import Awaitable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from time import monotonic
from typing import Any
from urllib.parse import urlparse

import httpx

from backend.whale_requests import current_whale_request_capture

ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{40}")
ZERO = Decimal("0")
SDK_DATA_API_URL = "https://data-api.polymarket.com"
SDK_GAMMA_API_URL = "https://gamma-api.polymarket.com"
PROXY_ENVIRONMENT_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
LOCAL_NO_PROXY = "localhost,127.0.0.1,::1"


def configure_polymarket_proxy(proxy_url: str) -> str:
    normalized = proxy_url.strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("POLYMARKET_PROXY_URL 必须是有效的 HTTP(S) 代理地址")
    for key in PROXY_ENVIRONMENT_KEYS:
        os.environ[key] = normalized
    os.environ["NO_PROXY"] = LOCAL_NO_PROXY
    os.environ["no_proxy"] = LOCAL_NO_PROXY
    return normalized


class PolymarketAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        rate_limited: bool = False,
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.rate_limited = rate_limited


class InvalidWalletInput(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PublicProfile:
    submitted_address: str
    proxy_wallet: str
    label: str


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    asset_id: str
    condition_id: str
    title: str
    outcome: str
    outcome_index: int | None
    icon_url: str | None
    event_slug: str | None
    market_slug: str | None
    size: Decimal
    avg_price: Decimal
    current_price: Decimal
    initial_value: Decimal
    current_value: Decimal
    cash_pnl: Decimal
    percent_pnl: Decimal
    total_bought: Decimal
    realized_pnl: Decimal
    end_date: datetime | None


@dataclass(frozen=True, slots=True)
class ClosedPositionSnapshot:
    asset_id: str
    condition_id: str
    title: str
    outcome: str
    event_slug: str | None
    market_slug: str | None
    avg_price: Decimal
    total_bought: Decimal
    realized_pnl: Decimal
    closed_at: datetime | None


@dataclass(frozen=True, slots=True)
class TradeSnapshot:
    asset_id: str
    condition_id: str
    side: str
    size: Decimal
    price: Decimal
    timestamp: datetime
    transaction_hash: str | None
    title: str | None = None
    outcome: str | None = None
    outcome_index: int | None = None
    event_slug: str | None = None
    market_slug: str | None = None


@dataclass(frozen=True, slots=True)
class LargeTradeSnapshot:
    proxy_wallet: str
    asset_id: str
    condition_id: str
    side: str
    size: Decimal
    price: Decimal
    amount: Decimal
    timestamp: datetime
    title: str
    outcome: str
    outcome_index: int | None
    market_slug: str | None
    event_slug: str | None
    icon_url: str | None
    display_name: str | None
    transaction_hash: str | None


@dataclass(frozen=True, slots=True)
class OfficialTag:
    id: str
    slug: str
    label: str


@dataclass(frozen=True, slots=True)
class WhaleMarketSnapshot:
    condition_id: str
    title: str
    market_slug: str | None
    event_slug: str | None
    icon_url: str | None
    tags: tuple[OfficialTag, ...]
    closed: bool
    active: bool
    accepting_orders: bool
    neg_risk: bool
    end_date: datetime | None
    end_date_is_date_only: bool
    outcomes: tuple[str, ...]
    outcome_prices: tuple[Decimal, ...]
    clob_token_ids: tuple[str, ...]
    liquidity: Decimal
    volume_24h: Decimal
    best_bid: Decimal | None
    best_ask: Decimal | None
    order_min_size: Decimal
    tick_size: Decimal
    fee_rate: Decimal
    fee_exponent: Decimal


@dataclass(frozen=True, slots=True)
class WhaleHolderSnapshot:
    proxy_wallet: str
    asset_id: str
    amount: Decimal
    outcome_index: int
    display_name: str | None
    profile_image_url: str | None
    verified_badge: bool


@dataclass(frozen=True, slots=True)
class WhaleMarketPositionSnapshot:
    proxy_wallet: str
    asset_id: str
    condition_id: str
    outcome: str
    outcome_index: int
    size: Decimal
    total_bought: Decimal
    avg_price: Decimal
    current_price: Decimal
    current_value: Decimal
    display_name: str | None
    profile_image_url: str | None
    verified_badge: bool


@dataclass(frozen=True, slots=True)
class WhalePublicProfile:
    proxy_wallet: str
    display_name: str | None
    name: str | None
    pseudonym: str | None
    profile_image_url: str | None
    created_at: datetime | None
    verified_badge: bool
    taker_tier: int | None
    taker_tier_name: str | None
    weighted_volume: Decimal | None


@dataclass(frozen=True, slots=True)
class RedemptionSnapshot:
    asset_id: str
    condition_id: str
    title: str
    outcome: str
    outcome_index: int | None
    event_slug: str | None
    market_slug: str | None
    size: Decimal
    usdc_size: Decimal
    timestamp: datetime
    transaction_hash: str | None


@dataclass(frozen=True, slots=True)
class SettlementEvidence:
    resolved_condition_ids: frozenset[str]
    redeemable_asset_ids: frozenset[str]
    non_trade_condition_ids: frozenset[str]

    def is_settlement(self, asset_id: str, condition_id: str) -> bool:
        return (
            condition_id in self.resolved_condition_ids
            or asset_id in self.redeemable_asset_ids
            or condition_id in self.non_trade_condition_ids
        )


@dataclass(frozen=True, slots=True)
class MarketResolution:
    condition_id: str
    payout_by_asset_id: dict[str, Decimal]
    resolved_at: datetime


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    price: Decimal
    size: Decimal


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    asset_id: str
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    tick_size: Decimal
    min_order_size: Decimal
    neg_risk: bool

    @property
    def best_bid(self) -> Decimal | None:
        return max((level.price for level in self.bids), default=None)

    @property
    def best_ask(self) -> Decimal | None:
        return min((level.price for level in self.asks), default=None)


@dataclass(frozen=True, slots=True)
class ResolvedMarketOutcome:
    market_url: str
    asset_id: str
    condition_id: str
    title: str
    outcome: str
    outcome_index: int
    neg_risk: bool
    fee_rate_bps: int


@dataclass(frozen=True, slots=True)
class ResolvedMarketURL:
    market_url: str
    event_title: str
    markets: tuple[WhaleMarketSnapshot, ...]


def fingerprint_trades(
    proxy_wallet: str,
    trades: list[TradeSnapshot],
) -> list[tuple[str, TradeSnapshot]]:
    """Create stable fingerprints across overlapping public-trade polls."""

    occurrences: dict[str, int] = defaultdict(int)
    results: list[tuple[str, TradeSnapshot]] = []
    for trade in sorted(
        trades,
        key=lambda item: (
            item.timestamp,
            item.transaction_hash or "",
            item.asset_id,
            item.side,
            item.price,
            item.size,
        ),
    ):
        transaction_key = trade.transaction_hash or f"timestamp:{trade.timestamp.isoformat()}"
        base = "|".join(
            [
                proxy_wallet,
                transaction_key,
                trade.asset_id,
                trade.side,
                str(trade.price),
                str(trade.size),
            ]
        )
        occurrence = occurrences[base]
        occurrences[base] += 1
        fingerprint = hashlib.sha256(f"{base}|{occurrence}".encode()).hexdigest()
        results.append((fingerprint, trade))
    return results


def parse_wallet_input(value: str) -> str:
    candidate = value.strip()
    if ADDRESS_RE.fullmatch(candidate):
        return candidate.lower()

    try:
        parsed = urlparse(candidate)
    except ValueError as error:
        raise InvalidWalletInput("钱包地址或个人页链接格式无效") from error

    host = (parsed.hostname or "").lower()
    if host not in {"polymarket.com", "www.polymarket.com"}:
        raise InvalidWalletInput("仅支持钱包地址或 polymarket.com 个人页链接")
    match = ADDRESS_RE.search(parsed.path)
    if match is None:
        raise InvalidWalletInput("个人页链接中没有可识别的钱包地址")
    return match.group(0).lower()


def to_decimal(value: Any, *, default: Decimal = ZERO) -> Decimal:
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def parse_datetime(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(value, tz=UTC)
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


class PolymarketClient:
    POSITION_PAGE_SIZE = 500
    TRADE_MARKET_BATCH_SIZE = 100
    MARKET_RESOLUTION_BATCH_SIZE = 100
    LARGE_TRADE_REQUEST_INTERVAL_SECONDS = 0.5

    def __init__(
        self,
        *,
        data_api_url: str,
        gamma_api_url: str,
        clob_api_url: str = "https://clob.polymarket.com",
        timeout: float,
        proxy_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        data_api_concurrency: int = 10,
        gamma_api_concurrency: int = 6,
        clob_api_concurrency: int = 10,
    ) -> None:
        self.data_api_url = data_api_url.rstrip("/")
        self.gamma_api_url = gamma_api_url.rstrip("/")
        self.clob_api_url = clob_api_url.rstrip("/")
        if transport is None and proxy_url is None:
            raise ValueError("Polymarket 客户端必须配置代理")
        if transport is not None and proxy_url is not None:
            raise ValueError("测试传输与 Polymarket 代理不能同时配置")
        self.proxy_url = configure_polymarket_proxy(proxy_url) if proxy_url is not None else None
        self._timeout = httpx.Timeout(timeout)
        self._transport = transport
        self._http_reset_lock = asyncio.Lock()
        self._http = self._new_http_client()
        self._retired_http_clients: set[httpx.AsyncClient] = set()
        self._retired_close_tasks: set[asyncio.Task[None]] = set()
        self._large_trade_request_lock = asyncio.Lock()
        self._last_large_trade_request_at: float | None = None
        self._request_semaphores = {
            urlparse(self.data_api_url).netloc: asyncio.Semaphore(max(1, data_api_concurrency)),
            urlparse(self.gamma_api_url).netloc: asyncio.Semaphore(max(1, gamma_api_concurrency)),
            urlparse(self.clob_api_url).netloc: asyncio.Semaphore(max(1, clob_api_concurrency)),
        }
        self._market_end_cache: dict[str, tuple[float, datetime | None]] = {}
        self._closed_positions_cache: dict[
            str, tuple[float, tuple[ClosedPositionSnapshot, ...]]
        ] = {}

    async def close(self) -> None:
        for task in self._retired_close_tasks:
            task.cancel()
        if self._retired_close_tasks:
            await asyncio.gather(*self._retired_close_tasks, return_exceptions=True)
        await self._http.aclose()
        if self._retired_http_clients:
            await asyncio.gather(
                *(client.aclose() for client in self._retired_http_clients),
                return_exceptions=True,
            )
        self._retired_http_clients.clear()

    def _new_http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._timeout,
            proxy=self.proxy_url,
            transport=self._transport,
            trust_env=False,
            headers={"User-Agent": "polymarket-wallet-monitor/0.1"},
        )

    async def _reset_http_client(self, failed_client: httpx.AsyncClient) -> None:
        """Drop a connection pool that may contain a stale proxy tunnel."""

        async with self._http_reset_lock:
            if self._http is not failed_client:
                return
            self._http = self._new_http_client()
            self._retired_http_clients.add(failed_client)
            task = asyncio.create_task(
                self._close_retired_client(failed_client),
                name="polymarket-retired-http-client",
            )
            self._retired_close_tasks.add(task)
            task.add_done_callback(self._retired_close_tasks.discard)

    async def _close_retired_client(self, client: httpx.AsyncClient) -> None:
        try:
            # Let requests already using the old pool finish before closing it.
            await asyncio.sleep(30)
        finally:
            await asyncio.shield(client.aclose())
            self._retired_http_clients.discard(client)

    async def _get_json(
        self,
        url: str,
        *,
        params: dict[str, Any],
        not_found_none: bool = False,
    ) -> Any:
        client = self._http
        request = client.build_request("GET", url, params=params)
        capture = current_whale_request_capture()
        request_record_id: int | None = None
        if capture is not None:
            query_params: dict[str, str | list[str]] = {}
            for key, value in request.url.params.multi_items():
                current = query_params.get(key)
                if current is None:
                    query_params[key] = value
                elif isinstance(current, list):
                    current.append(value)
                else:
                    query_params[key] = [current, value]
            request_record_id = await capture.monitor.begin(
                scan_id=capture.scan_id,
                method=request.method,
                url=str(request.url).partition("?")[0],
                query_params=query_params,
                source="http",
            )
        try:
            semaphore = self._request_semaphores.get(request.url.host or "")
            if semaphore is None:
                response = await client.send(request)
            else:
                async with semaphore:
                    response = await client.send(request)
        except httpx.TransportError as error:
            if capture is not None and request_record_id is not None:
                await capture.monitor.complete(
                    request_record_id,
                    status="failed",
                    error_type=type(error).__name__,
                    error_message=str(error).strip() or type(error).__name__,
                )
            await self._reset_http_client(client)
            reason = type(error).__name__
            host = urlparse(url).netloc or url
            detail = str(error).strip()
            message = f"Polymarket 接口连接失败（{reason}，{host}）"
            if detail:
                message += f"：{detail}"
            raise PolymarketAPIError(message) from error
        except asyncio.CancelledError as error:
            if capture is not None and request_record_id is not None:
                await capture.monitor.complete(
                    request_record_id,
                    status="failed",
                    error_type=type(error).__name__,
                    error_message="请求已取消",
                )
            raise
        except Exception as error:
            if capture is not None and request_record_id is not None:
                await capture.monitor.complete(
                    request_record_id,
                    status="failed",
                    error_type=type(error).__name__,
                    error_message=str(error).strip() or type(error).__name__,
                )
            raise
        if response.status_code == 429:
            retry_after = self._parse_retry_after(response.headers.get("Retry-After"))
            message = "Polymarket 接口请求过于频繁"
            if capture is not None and request_record_id is not None:
                await capture.monitor.complete(
                    request_record_id,
                    status="failed",
                    http_status=response.status_code,
                    error_type="HTTPError",
                    error_message=message,
                    response_excerpt=response.text.strip(),
                )
            raise PolymarketAPIError(
                message,
                retry_after=retry_after,
                rate_limited=True,
            )
        if response.status_code == 404 and not_found_none:
            if capture is not None and request_record_id is not None:
                await capture.monitor.complete(
                    request_record_id,
                    status="success",
                    http_status=response.status_code,
                )
            return None
        if response.status_code >= 400:
            detail = response.text[:300].strip()
            message = f"Polymarket 接口返回 {response.status_code}" + (
                f"：{detail}" if detail else ""
            )
            if capture is not None and request_record_id is not None:
                await capture.monitor.complete(
                    request_record_id,
                    status="failed",
                    http_status=response.status_code,
                    error_type="HTTPError",
                    error_message=message,
                    response_excerpt=response.text.strip(),
                )
            raise PolymarketAPIError(message)
        try:
            payload = response.json()
        except ValueError as error:
            if capture is not None and request_record_id is not None:
                await capture.monitor.complete(
                    request_record_id,
                    status="failed",
                    http_status=response.status_code,
                    error_type=type(error).__name__,
                    error_message="Polymarket 接口返回了无效 JSON",
                    response_excerpt=response.text.strip(),
                )
            raise PolymarketAPIError("Polymarket 接口返回了无效 JSON") from error
        if capture is not None and request_record_id is not None:
            await capture.monitor.complete(
                request_record_id,
                status="success",
                http_status=response.status_code,
            )
        return payload

    async def _monitored_sdk_request(
        self,
        operation: Awaitable[Any],
        *,
        url: str,
        params: dict[str, Any],
        not_found_none: bool = False,
    ) -> Any:
        capture = current_whale_request_capture()
        request_record_id: int | None = None
        if capture is not None:
            query_params = {
                key: [str(item) for item in value]
                if isinstance(value, (list, tuple))
                else str(value)
                for key, value in params.items()
                if value is not None
            }
            request_record_id = await capture.monitor.begin(
                scan_id=capture.scan_id,
                method="GET",
                url=url,
                query_params=query_params,
                source="sdk",
            )
        try:
            result = await operation
        except asyncio.CancelledError:
            if capture is not None and request_record_id is not None:
                await capture.monitor.complete(
                    request_record_id,
                    status="failed",
                    error_type="CancelledError",
                    error_message="请求已取消",
                )
            raise
        except Exception as error:
            if capture is not None and request_record_id is not None:
                status = getattr(error, "status", None)
                if type(error).__name__ == "RateLimitError":
                    status = 429
                await capture.monitor.complete(
                    request_record_id,
                    status="failed",
                    http_status=status if isinstance(status, int) else None,
                    error_type=type(error).__name__,
                    error_message=str(error).strip() or type(error).__name__,
                )
            raise
        if capture is not None and request_record_id is not None:
            await capture.monitor.complete(
                request_record_id,
                status="success",
                http_status=404 if not_found_none and result is None else 200,
            )
        return result

    async def fetch_large_trades(
        self,
        *,
        filter_amount_usdc: Decimal,
        start: datetime,
        end: datetime,
        limit: int = 500,
        offset: int = 0,
        condition_ids: Iterable[str] | None = None,
    ) -> list[LargeTradeSnapshot]:
        """Fetch one descending Data API page for the whale scanner.

        Both time boundaries are sent to the provider. The scanner also checks
        them locally and partitions busy market windows to avoid the offset cap.
        """

        if filter_amount_usdc < ZERO:
            raise ValueError("成交采集金额不能小于 0")
        if not 1 <= limit <= 500:
            raise ValueError("大额成交单页数量必须在 1 到 500 之间")
        if offset < 0:
            raise ValueError("大额成交分页偏移不能为负数")
        if end < start:
            raise ValueError("大额成交查询结束时间不能早于开始时间")
        async with self._large_trade_request_lock:
            if self._last_large_trade_request_at is not None:
                delay = self.LARGE_TRADE_REQUEST_INTERVAL_SECONDS - (
                    monotonic() - self._last_large_trade_request_at
                )
                if delay > 0:
                    await asyncio.sleep(delay)
            self._last_large_trade_request_at = monotonic()
            payload = await self._get_json(
                f"{self.data_api_url}/trades",
                params={
                    "filterType": "CASH",
                    "filterAmount": str(filter_amount_usdc),
                    "limit": limit,
                    "offset": offset,
                    "takerOnly": "false",
                    "side": "BUY",
                    "start": int(start.replace(tzinfo=UTC).timestamp()),
                    "end": int(end.replace(tzinfo=UTC).timestamp()),
                    **({"market": ",".join(dict.fromkeys(condition_ids))} if condition_ids else {}),
                },
            )
        if not isinstance(payload, list):
            raise PolymarketAPIError("大额成交接口返回格式无效")

        trades: list[LargeTradeSnapshot] = []
        for item in payload:
            if not isinstance(item, dict):
                if condition_ids:
                    raise PolymarketAPIError("重点市场成交包含无效记录，无法确认分页完整性")
                continue
            side = str(item.get("side") or "").upper()
            proxy_wallet = str(item.get("proxyWallet") or "").lower()
            asset_id = str(item.get("asset") or "")
            condition_id = str(item.get("conditionId") or "")
            size = to_decimal(item.get("size"))
            price = to_decimal(item.get("price"))
            timestamp = self._trade_datetime(item.get("timestamp"))
            if (
                side not in {"BUY", "SELL"}
                or not proxy_wallet
                or not asset_id
                or not condition_id
                or size <= ZERO
                or price <= ZERO
                or price > Decimal("1")
                or timestamp is None
            ):
                if condition_ids:
                    raise PolymarketAPIError("重点市场成交包含无效记录，无法确认分页完整性")
                continue
            outcome_index = self._optional_int(item.get("outcomeIndex"))
            display_name = self._optional_text(item.get("name")) or self._optional_text(
                item.get("pseudonym")
            )
            trades.append(
                LargeTradeSnapshot(
                    proxy_wallet=proxy_wallet,
                    asset_id=asset_id,
                    condition_id=condition_id,
                    side=side,
                    size=size,
                    price=price,
                    amount=size * price,
                    timestamp=timestamp,
                    title=str(item.get("title") or "未命名市场"),
                    outcome=str(item.get("outcome") or ""),
                    outcome_index=outcome_index,
                    market_slug=self._optional_text(item.get("slug")),
                    event_slug=self._optional_text(item.get("eventSlug")),
                    icon_url=self._optional_text(item.get("icon")),
                    display_name=display_name,
                    transaction_hash=self._optional_text(item.get("transactionHash")),
                )
            )
        return trades

    async def fetch_markets_with_tags(
        self,
        condition_ids: Iterable[str],
    ) -> list[WhaleMarketSnapshot]:
        conditions = list(
            dict.fromkeys(str(condition_id) for condition_id in condition_ids if condition_id)
        )
        markets: list[WhaleMarketSnapshot] = []
        for offset in range(0, len(conditions), self.MARKET_RESOLUTION_BATCH_SIZE):
            batch = conditions[offset : offset + self.MARKET_RESOLUTION_BATCH_SIZE]
            payload = await self._get_json(
                f"{self.gamma_api_url}/markets",
                params={
                    "condition_ids": batch,
                    "include_tag": "true",
                    "limit": len(batch),
                },
            )
            if not isinstance(payload, list):
                raise PolymarketAPIError("市场元数据接口返回格式无效")
            for item in payload:
                if not isinstance(item, dict):
                    continue
                condition_id = str(item.get("conditionId") or "")
                if not condition_id or condition_id not in batch:
                    continue
                markets.append(self._parse_whale_market(item))
        return markets

    async def fetch_closed_markets_with_tags(
        self,
        condition_ids: Iterable[str],
    ) -> list[WhaleMarketSnapshot]:
        """Fetch resolved/closed markets omitted by Gamma's default market query."""

        conditions = list(
            dict.fromkeys(str(condition_id) for condition_id in condition_ids if condition_id)
        )
        markets: list[WhaleMarketSnapshot] = []
        for offset in range(0, len(conditions), self.MARKET_RESOLUTION_BATCH_SIZE):
            batch = conditions[offset : offset + self.MARKET_RESOLUTION_BATCH_SIZE]
            payload = await self._get_json(
                f"{self.gamma_api_url}/markets",
                params={
                    "condition_ids": batch,
                    "closed": "true",
                    "include_tag": "true",
                    "limit": len(batch),
                },
            )
            if not isinstance(payload, list):
                raise PolymarketAPIError("已关闭市场元数据接口返回格式无效")
            for item in payload:
                if not isinstance(item, dict):
                    continue
                condition_id = str(item.get("conditionId") or "")
                if condition_id in batch:
                    markets.append(self._parse_whale_market(item))
        return markets

    async def fetch_discovery_markets(
        self, *, min_liquidity_usdc: Decimal
    ) -> list[WhaleMarketSnapshot]:
        """Bound supplemental discovery to one page of high-volume markets."""
        payload = await self._get_json(
            f"{self.gamma_api_url}/markets",
            params={
                "closed": "false",
                "active": "true",
                "include_tag": "true",
                "liquidity_num_min": str(min_liquidity_usdc),
                "order": "volume24hr",
                "ascending": "false",
                "limit": 100,
            },
        )
        if not isinstance(payload, list):
            raise PolymarketAPIError("重点市场目录返回格式无效")
        return [self._parse_whale_market(item) for item in payload if isinstance(item, dict)]

    async def fetch_active_whale_markets(
        self,
        *,
        min_liquidity_usdc: Decimal,
        min_volume_usdc: Decimal,
        page_size: int = 100,
    ) -> list[WhaleMarketSnapshot]:
        """Fetch every open, sufficiently liquid Gamma market with stable pagination."""

        if min_liquidity_usdc < ZERO:
            raise ValueError("市场最低流动性不能小于 0")
        if min_volume_usdc < ZERO:
            raise ValueError("市场最低成交额不能小于 0")
        if page_size <= 0:
            raise ValueError("市场分页数量必须大于 0")
        markets: list[WhaleMarketSnapshot] = []
        seen: set[str] = set()
        seen_cursors: set[str] = set()
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {
                "closed": "false",
                "liquidity_num_min": str(min_liquidity_usdc),
                "volume_num_min": str(min_volume_usdc),
                "include_tag": "true",
                "order": "id",
                "ascending": "true",
                "limit": page_size,
            }
            if cursor is not None:
                params["after_cursor"] = cursor
            payload = await self._get_json(
                f"{self.gamma_api_url}/markets/keyset",
                params=params,
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("markets"), list):
                raise PolymarketAPIError("活跃市场接口返回格式无效")
            for item in payload["markets"]:
                if not isinstance(item, dict):
                    continue
                market = self._parse_whale_market(item)
                if (
                    not market.condition_id
                    or market.condition_id in seen
                    or market.closed
                    or not market.active
                    or not market.accepting_orders
                ):
                    continue
                seen.add(market.condition_id)
                markets.append(market)
            next_cursor = str(payload.get("next_cursor") or "").strip()
            if not next_cursor or next_cursor in seen_cursors:
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return markets

    async def fetch_top_holders(
        self,
        condition_ids: Iterable[str],
        *,
        min_balance: int = 1,
        limit: int = 20,
    ) -> list[WhaleHolderSnapshot]:
        """Fetch the official top holders, batching market IDs to keep URLs bounded."""

        conditions = list(
            dict.fromkeys(str(condition_id) for condition_id in condition_ids if condition_id)
        )
        if not 0 <= limit <= 20:
            raise ValueError("单个结果的持仓者数量必须在 0 到 20 之间")
        if not 0 <= min_balance <= 999999:
            raise ValueError("最低持仓份额必须在 0 到 999999 之间")
        holders: list[WhaleHolderSnapshot] = []

        async def fetch_batch(batch: list[str]) -> Any:
            for attempt in range(5):
                try:
                    return await self._get_json(
                        f"{self.data_api_url}/holders",
                        params={
                            "market": ",".join(batch),
                            "limit": limit,
                            "minBalance": min_balance,
                        },
                    )
                except PolymarketAPIError as error:
                    if attempt >= 4:
                        raise
                    await asyncio.sleep(max(0.1, float(error.retry_after or (attempt + 1))))
            raise AssertionError("unreachable")

        batches = [conditions[offset : offset + 50] for offset in range(0, len(conditions), 50)]
        for offset in range(0, len(batches), 5):
            payloads = await asyncio.gather(
                *(fetch_batch(batch) for batch in batches[offset : offset + 5])
            )
            for payload in payloads:
                if not isinstance(payload, list):
                    raise PolymarketAPIError("市场大额持仓接口返回格式无效")
                for token_group in payload:
                    if not isinstance(token_group, dict):
                        continue
                    asset_id = str(token_group.get("token") or "")
                    raw_holders = token_group.get("holders")
                    if not asset_id or not isinstance(raw_holders, list):
                        continue
                    for item in raw_holders:
                        if not isinstance(item, dict):
                            continue
                        wallet = str(item.get("proxyWallet") or "").lower()
                        amount = to_decimal(item.get("amount"))
                        if not wallet or amount <= ZERO:
                            continue
                        holders.append(
                            WhaleHolderSnapshot(
                                proxy_wallet=wallet,
                                asset_id=str(item.get("asset") or asset_id),
                                amount=amount,
                                outcome_index=int(item.get("outcomeIndex") or 0),
                                display_name=self._optional_text(item.get("name"))
                                or self._optional_text(item.get("pseudonym")),
                                profile_image_url=self._optional_text(item.get("profileImage"))
                                or self._optional_text(item.get("profileImageOptimized")),
                                verified_badge=self._as_bool(item.get("verified")),
                            )
                        )
        return holders

    async def fetch_market_positions(
        self,
        condition_id: str,
        *,
        limit: int = 20,
    ) -> list[WhaleMarketPositionSnapshot]:
        """Fetch official open positions for one market, largest token balances first."""

        if not 0 < limit <= 500:
            raise ValueError("市场持仓数量必须在 1 到 500 之间")
        try:
            from polymarket import AsyncPublicClient

            async with AsyncPublicClient() as sdk:
                page = await self._monitored_sdk_request(
                    sdk.list_market_positions(
                        market=condition_id,
                        status="OPEN",
                        sort_by="TOKENS",
                        sort_direction="DESC",
                        page_size=limit,
                    ).first_page(),
                    url=f"{SDK_DATA_API_URL}/v1/market-positions",
                    params={
                        "market": condition_id,
                        "status": "OPEN",
                        "sortBy": "TOKENS",
                        "sortDirection": "DESC",
                        "limit": limit,
                    },
                )
        except Exception as error:
            raise self._sdk_api_error("公开 SDK 市场持仓查询失败", error) from error
        positions: list[WhaleMarketPositionSnapshot] = []
        for token_group in page.items:
            group_asset_id = str(token_group.token or "")
            for item in token_group.positions or ():
                wallet = str(item.wallet or "").lower()
                asset_id = str(item.token_id or group_asset_id)
                linked_condition_id = str(item.condition_id or condition_id)
                size = item.size or ZERO
                if (
                    not wallet
                    or not asset_id
                    or linked_condition_id != condition_id
                    or size <= ZERO
                ):
                    continue
                positions.append(
                    WhaleMarketPositionSnapshot(
                        proxy_wallet=wallet,
                        asset_id=asset_id,
                        condition_id=linked_condition_id,
                        outcome=item.outcome or "",
                        outcome_index=item.outcome_index or 0,
                        size=size,
                        total_bought=item.total_bought or ZERO,
                        avg_price=item.avg_price or ZERO,
                        current_price=item.cur_price or ZERO,
                        current_value=item.current_value or ZERO,
                        display_name=self._optional_text(item.name),
                        profile_image_url=self._optional_text(item.profile_image),
                        verified_badge=bool(item.verified),
                    )
                )
        return positions

    async def fetch_public_profile(self, address: str) -> WhalePublicProfile | None:
        normalized_address = address.strip().lower()
        payload = await self._get_json(
            f"{self.gamma_api_url}/public-profile",
            params={"address": normalized_address},
            not_found_none=True,
        )
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise PolymarketAPIError("钱包公开资料接口返回格式无效")
        name = self._optional_text(payload.get("name"))
        pseudonym = self._optional_text(payload.get("pseudonym"))
        return WhalePublicProfile(
            proxy_wallet=str(payload.get("proxyWallet") or normalized_address).lower(),
            display_name=name or pseudonym,
            name=name,
            pseudonym=pseudonym,
            profile_image_url=self._optional_text(payload.get("profileImage")),
            created_at=parse_datetime(payload.get("createdAt")),
            verified_badge=self._as_bool(payload.get("verifiedBadge")),
            taker_tier=self._optional_int(payload.get("takerTier")),
            taker_tier_name=self._optional_text(payload.get("takerTierName")),
            weighted_volume=self._optional_decimal(payload.get("weightedVolume")),
        )

    async def fetch_tags(self, *, limit: int = 200) -> list[OfficialTag]:
        if limit <= 0:
            raise ValueError("标签数量上限必须大于 0")
        try:
            from polymarket import AsyncPublicClient

            async with AsyncPublicClient() as sdk:
                paginator = sdk.list_tags(
                    order="id",
                    ascending=True,
                    page_size=min(limit, 100),
                )

                async def collect_tags() -> list[Any]:
                    items: list[Any] = []
                    async for item in paginator.iter_items():
                        items.append(item)
                        if len(items) >= limit:
                            break
                    return items

                sdk_tags = await self._monitored_sdk_request(
                    collect_tags(),
                    url=f"{SDK_GAMMA_API_URL}/tags",
                    params={"order": "id", "ascending": True, "limit": limit},
                )
        except Exception as error:
            raise self._sdk_api_error("公开 SDK 标签字典查询失败", error) from error
        tags: list[OfficialTag] = []
        for item in sdk_tags:
            tag_id = str(item.id or "").strip()
            slug = str(item.slug or "").strip()
            if tag_id and slug:
                tags.append(
                    OfficialTag(
                        id=tag_id,
                        slug=slug,
                        label=str(item.label or slug).strip() or slug,
                    )
                )
        return tags

    async def fetch_order_book(self, asset_id: str) -> OrderBookSnapshot:
        payload = await self._get_json(
            f"{self.clob_api_url}/book",
            params={"token_id": asset_id},
        )
        if not isinstance(payload, dict):
            raise PolymarketAPIError("订单簿接口返回格式无效")

        def levels(key: str) -> tuple[OrderBookLevel, ...]:
            raw_levels = payload.get(key)
            if not isinstance(raw_levels, list):
                return ()
            parsed: list[OrderBookLevel] = []
            for item in raw_levels:
                if not isinstance(item, dict):
                    continue
                price = to_decimal(item.get("price"))
                size = to_decimal(item.get("size"))
                if ZERO < price < Decimal("1") and size > ZERO:
                    parsed.append(OrderBookLevel(price=price, size=size))
            return tuple(parsed)

        tick_size = to_decimal(payload.get("tick_size"), default=Decimal("0.01"))
        min_order_size = to_decimal(payload.get("min_order_size"), default=Decimal("5"))
        return OrderBookSnapshot(
            asset_id=str(payload.get("asset_id") or payload.get("market") or asset_id),
            bids=levels("bids"),
            asks=levels("asks"),
            tick_size=tick_size if tick_size > ZERO else Decimal("0.01"),
            min_order_size=min_order_size if min_order_size > ZERO else Decimal("5"),
            neg_risk=bool(payload.get("neg_risk", False)),
        )

    async def resolve_market_url(self, market_url: str) -> ResolvedMarketURL:
        value = market_url.strip()
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        segments = [segment for segment in parsed.path.split("/") if segment]
        if parsed.scheme != "https" or host not in {"polymarket.com", "www.polymarket.com"}:
            raise InvalidWalletInput("仅支持 https://polymarket.com 市场链接")
        has_locale_prefix = bool(
            segments and re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", segments[0], re.I)
        )
        routed_segments = segments[1:] if has_locale_prefix else segments
        is_event = len(routed_segments) == 2 and routed_segments[0] == "event"
        is_market = (len(routed_segments) == 3 and routed_segments[0] == "event") or (
            len(routed_segments) == 2 and routed_segments[0] == "market"
        )
        is_sports_event = (
            len(routed_segments) == 3
            and routed_segments[0] == "sports"
            and all(routed_segments[1:])
        )
        if not is_event and not is_market and not is_sports_event:
            raise InvalidWalletInput(
                "市场链接路径无效，请粘贴 Polymarket event、market 或 sports 链接"
            )

        try:
            from polymarket import AsyncPublicClient

            async with AsyncPublicClient() as sdk:
                if is_sports_event:
                    event = await sdk.get_event(slug=routed_segments[-1])
                    event_title = event.title or routed_segments[-1]
                    event_slug = event.slug
                    markets = event.markets
                elif is_event:
                    event = (
                        await sdk.get_event(slug=routed_segments[-1])
                        if has_locale_prefix
                        else await sdk.get_event(url=value)
                    )
                    event_title = event.title or routed_segments[-1]
                    event_slug = event.slug
                    markets = event.markets
                else:
                    market = (
                        await sdk.get_market(slug=routed_segments[-1])
                        if has_locale_prefix
                        else await sdk.get_market(url=value)
                    )
                    first_event = market.events[0] if market.events else None
                    event_title = (
                        first_event.title if first_event and first_event.title else market.question
                    ) or routed_segments[-1]
                    event_slug = first_event.slug if first_event else None
                    markets = (market,)
        except InvalidWalletInput:
            raise
        except Exception as error:
            raise PolymarketAPIError(f"无法通过官方 SDK 解析市场链接：{error}") from error

        snapshots: list[WhaleMarketSnapshot] = []
        for market in markets:
            outcomes = (market.outcomes.yes, market.outcomes.no)
            token_ids = tuple(
                str(item.token_id) if item.token_id is not None else "" for item in outcomes
            )
            if not market.condition_id or any(not token_id for token_id in token_ids):
                continue
            fee_schedule = market.trading.fee_schedule
            market_event = market.events[0] if market.events else None
            snapshots.append(
                WhaleMarketSnapshot(
                    condition_id=str(market.condition_id),
                    title=market.question or market.group_item_title or event_title,
                    market_slug=market.slug,
                    event_slug=(market_event.slug if market_event else None) or event_slug,
                    icon_url=market.icon or market.image,
                    tags=tuple(
                        OfficialTag(id=str(tag.id), slug=tag.slug or "", label=tag.label or "")
                        for tag in market.tags
                        if tag.slug
                    ),
                    closed=bool(market.state.closed),
                    active=market.state.active is not False,
                    accepting_orders=market.state.accepting_orders is True,
                    neg_risk=bool(market.state.neg_risk),
                    end_date=market.state.end_date,
                    end_date_is_date_only=False,
                    outcomes=tuple(item.label for item in outcomes),
                    outcome_prices=tuple(item.price or ZERO for item in outcomes),
                    clob_token_ids=token_ids,
                    liquidity=market.metrics.liquidity or ZERO,
                    volume_24h=market.metrics.volume_24hr or ZERO,
                    best_bid=market.prices.best_bid,
                    best_ask=market.prices.best_ask,
                    order_min_size=market.trading.minimum_order_size or Decimal("5"),
                    tick_size=market.trading.minimum_tick_size or Decimal("0.01"),
                    fee_rate=fee_schedule.rate if fee_schedule is not None else ZERO,
                    fee_exponent=(
                        Decimal(str(fee_schedule.exponent))
                        if fee_schedule is not None
                        else Decimal("1")
                    ),
                )
            )
        if not snapshots:
            raise PolymarketAPIError("该链接中没有可识别的 CLOB outcome")
        return ResolvedMarketURL(
            market_url=value,
            event_title=event_title,
            markets=tuple(snapshots),
        )

    async def resolve_market_outcome(self, market_url: str, outcome: str) -> ResolvedMarketOutcome:
        parsed = urlparse(market_url.strip())
        slug = parsed.path.rstrip("/").split("/")[-1]
        if not slug:
            raise InvalidWalletInput("市场链接无效")

        markets_payload = await self._get_json(
            f"{self.gamma_api_url}/markets", params={"slug": slug, "limit": 10}
        )
        markets = markets_payload if isinstance(markets_payload, list) else []
        if not markets:
            events_payload = await self._get_json(
                f"{self.gamma_api_url}/events", params={"slug": slug, "limit": 10}
            )
            events = events_payload if isinstance(events_payload, list) else []
            for event in events:
                if isinstance(event, dict) and isinstance(event.get("markets"), list):
                    markets.extend(event["markets"])

        def as_list(value: Any) -> list[Any]:
            if isinstance(value, list):
                return value
            if isinstance(value, str):
                try:
                    parsed_value = json.loads(value)
                    return parsed_value if isinstance(parsed_value, list) else []
                except ValueError:
                    return []
            return []

        wanted = outcome.strip().casefold()
        matches: list[tuple[dict[str, Any], list[Any], int]] = []
        for market in markets:
            if not isinstance(market, dict):
                continue
            outcomes = as_list(market.get("outcomes"))
            tokens = as_list(market.get("clobTokenIds"))
            if len(outcomes) != len(tokens):
                continue
            for index, label in enumerate(outcomes):
                if str(label).strip().casefold() != wanted:
                    continue
                closed = str(market.get("closed", "false")).lower() == "true"
                inactive = str(market.get("active", "true")).lower() == "false"
                if closed or inactive:
                    raise PolymarketAPIError("该市场当前不开放交易")
                matches.append((market, tokens, index))
        if not matches:
            raise PolymarketAPIError("没有在该市场中找到指定 outcome")
        if len(matches) > 1:
            raise PolymarketAPIError("该链接包含多个同名 outcome，请使用具体市场链接")
        market, tokens, index = matches[0]
        asset_id = str(tokens[index])
        fee_payload = await self._get_json(
            f"{self.clob_api_url}/fee-rate", params={"token_id": asset_id}
        )
        fee_bps = int(
            (fee_payload.get("base_fee") or fee_payload.get("fee_rate_bps") or 0)
            if isinstance(fee_payload, dict)
            else 0
        )
        outcomes = as_list(market.get("outcomes"))
        return ResolvedMarketOutcome(
            market_url=market_url,
            asset_id=asset_id,
            condition_id=str(market.get("conditionId") or ""),
            title=str(market.get("question") or market.get("title") or slug),
            outcome=str(outcomes[index]),
            outcome_index=index,
            neg_risk=bool(market.get("negRisk", False)),
            fee_rate_bps=fee_bps,
        )

    async def fetch_market_end_date(
        self,
        *,
        market_slug: str | None,
        condition_id: str,
    ) -> datetime | None:
        """Return Gamma's timestamped end date, never Data API's date-only value."""

        cache_key = market_slug or condition_id
        cached = self._market_end_cache.get(cache_key)
        now = monotonic()
        if cached is not None and cached[0] > now:
            return cached[1]
        params: dict[str, Any] = {"limit": 1}
        if market_slug:
            params["slug"] = market_slug
        else:
            params["condition_ids"] = condition_id
        payload = await self._get_json(f"{self.gamma_api_url}/markets", params=params)
        if not isinstance(payload, list):
            raise PolymarketAPIError("市场时间接口返回格式无效")
        item = next((entry for entry in payload if isinstance(entry, dict)), None)
        raw_end_date = item.get("endDate") if item is not None else None
        # A bare YYYY-MM-DD cannot be used as a UTC cutoff. Treat it as unknown
        # rather than silently converting it to midnight and blocking the whole day.
        end_date = (
            None
            if isinstance(raw_end_date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_end_date)
            else parse_datetime(raw_end_date)
        )
        self._market_end_cache[cache_key] = (now + (300 if end_date is not None else 30), end_date)
        return end_date

    async def fetch_market_resolutions(
        self,
        condition_ids: Iterable[str],
    ) -> dict[str, MarketResolution]:
        conditions = list(
            dict.fromkeys(condition_id for condition_id in condition_ids if condition_id)
        )
        detected_at = datetime.now(UTC).replace(tzinfo=None)
        resolutions: dict[str, MarketResolution] = {}
        for offset in range(0, len(conditions), self.MARKET_RESOLUTION_BATCH_SIZE):
            batch = conditions[offset : offset + self.MARKET_RESOLUTION_BATCH_SIZE]
            payload = await self._get_json(
                f"{self.gamma_api_url}/markets",
                params={
                    "condition_ids": batch,
                    "closed": "true",
                    "limit": len(batch),
                },
            )
            if not isinstance(payload, list):
                raise PolymarketAPIError("市场结算接口返回格式无效")
            for item in payload:
                if not isinstance(item, dict) or item.get("closed") is not True:
                    continue
                condition_id = str(item.get("conditionId") or "")
                resolution_status = str(item.get("umaResolutionStatus") or "").lower()
                if condition_id not in batch or resolution_status not in {"resolved", "finalized"}:
                    continue
                asset_ids = self._json_list(item.get("clobTokenIds"))
                raw_payouts = self._json_list(item.get("outcomePrices"))
                if not asset_ids or len(asset_ids) != len(raw_payouts):
                    continue
                payout_by_asset_id: dict[str, Decimal] = {}
                valid = True
                for raw_asset_id, raw_payout in zip(asset_ids, raw_payouts, strict=True):
                    asset_id = str(raw_asset_id or "")
                    payout = to_decimal(raw_payout, default=Decimal("-1"))
                    if not asset_id or payout < ZERO or payout > Decimal("1"):
                        valid = False
                        break
                    payout_by_asset_id[asset_id] = payout
                if not valid or len(payout_by_asset_id) != len(asset_ids):
                    continue
                resolved_at = (
                    parse_datetime(item.get("closedTime"))
                    or parse_datetime(item.get("umaEndDate"))
                    or detected_at
                )
                resolutions[condition_id] = MarketResolution(
                    condition_id=condition_id,
                    payout_by_asset_id=payout_by_asset_id,
                    resolved_at=resolved_at,
                )
        return resolutions

    @classmethod
    def _parse_whale_market(cls, item: dict[str, Any]) -> WhaleMarketSnapshot:
        raw_end_date = item.get("endDate")
        end_date_is_date_only = bool(
            isinstance(raw_end_date, str)
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_end_date.strip())
        )
        outcomes = tuple(str(value) for value in cls._json_list(item.get("outcomes")))
        outcome_prices = tuple(
            to_decimal(value) for value in cls._json_list(item.get("outcomePrices"))
        )
        clob_token_ids = tuple(
            str(value) for value in cls._json_list(item.get("clobTokenIds")) if value is not None
        )
        tags = tuple(
            tag
            for raw_tag in cls._json_list(item.get("tags"))
            if (tag := cls._parse_official_tag(raw_tag)) is not None
        )
        events = cls._json_list(item.get("events"))
        first_event = next((event for event in events if isinstance(event, dict)), None)
        event_slug = cls._optional_text(item.get("eventSlug"))
        if event_slug is None and first_event is not None:
            event_slug = cls._optional_text(first_event.get("slug"))
        fee_schedule = cls._json_dict(item.get("feeSchedule"))
        return WhaleMarketSnapshot(
            condition_id=str(item.get("conditionId") or ""),
            title=str(item.get("question") or item.get("title") or "未命名市场"),
            market_slug=cls._optional_text(item.get("slug")),
            event_slug=event_slug,
            icon_url=cls._optional_text(item.get("icon")) or cls._optional_text(item.get("image")),
            tags=tags,
            closed=cls._as_bool(item.get("closed")),
            active=cls._as_bool(item.get("active")),
            accepting_orders=cls._as_bool(item.get("acceptingOrders")),
            neg_risk=cls._as_bool(item.get("negRisk")),
            end_date=None if end_date_is_date_only else parse_datetime(raw_end_date),
            end_date_is_date_only=end_date_is_date_only,
            outcomes=outcomes,
            outcome_prices=outcome_prices,
            clob_token_ids=clob_token_ids,
            liquidity=to_decimal(item.get("liquidity")),
            volume_24h=to_decimal(item.get("volume24hr") or item.get("volume24Hr")),
            best_bid=cls._optional_decimal(item.get("bestBid")),
            best_ask=cls._optional_decimal(item.get("bestAsk")),
            order_min_size=to_decimal(item.get("orderMinSize")),
            tick_size=to_decimal(item.get("orderPriceMinTickSize"), default=Decimal("0.01")),
            fee_rate=to_decimal(fee_schedule.get("rate")),
            fee_exponent=to_decimal(fee_schedule.get("exponent"), default=Decimal("1")),
        )

    @staticmethod
    def _parse_official_tag(value: Any) -> OfficialTag | None:
        if not isinstance(value, dict):
            return None
        tag_id = str(value.get("id") or "").strip()
        slug = str(value.get("slug") or "").strip()
        if not tag_id or not slug:
            return None
        return OfficialTag(
            id=tag_id,
            slug=slug,
            label=str(value.get("label") or slug).strip() or slug,
        )

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float, Decimal)):
            return value != 0
        return str(value or "").strip().lower() in {"1", "true", "yes"}

    @staticmethod
    def _optional_decimal(value: Any) -> Decimal | None:
        if value is None or value == "":
            return None
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None
        return parsed if parsed.is_finite() else None

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        if value is None:
            return None
        parsed = str(value).strip()
        return parsed or None

    @staticmethod
    def _trade_datetime(value: Any) -> datetime | None:
        if isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?", value.strip()):
            value = float(value)
        if isinstance(value, Decimal):
            value = float(value)
        try:
            return parse_datetime(value)
        except (OSError, OverflowError, ValueError):
            return None

    @staticmethod
    def _json_list(value: Any) -> list[Any]:
        if isinstance(value, list):
            return value
        if not isinstance(value, str):
            return []
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []

    @staticmethod
    def _json_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if not isinstance(value, str):
            return {}
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _parse_retry_after(raw: str | None) -> float | None:
        if not raw:
            return None
        try:
            return max(0.0, float(raw))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(raw)
            except (TypeError, ValueError):
                return None
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())

    @staticmethod
    def _sdk_api_error(message: str, error: Exception) -> PolymarketAPIError:
        retry_after = getattr(error, "retry_after", None)
        status = getattr(error, "status", None)
        detail = str(error).strip()
        return PolymarketAPIError(
            f"{message}：{detail}" if detail else message,
            retry_after=retry_after if isinstance(retry_after, (int, float)) else None,
            rate_limited=status == 429 or type(error).__name__ == "RateLimitError",
        )

    async def resolve_profile(self, raw_input: str, requested_label: str | None) -> PublicProfile:
        submitted = parse_wallet_input(raw_input)
        try:
            from polymarket import AsyncPublicClient

            async with AsyncPublicClient() as sdk:
                profile = await self._monitored_sdk_request(
                    sdk.get_public_profile(submitted),
                    url=f"{SDK_GAMMA_API_URL}/public-profile",
                    params={"address": submitted},
                    not_found_none=True,
                )
        except Exception as error:
            raise self._sdk_api_error("公开 SDK 钱包资料查询失败", error) from error

        proxy_wallet = str(profile.wallet if profile and profile.wallet else submitted).lower()
        if not ADDRESS_RE.fullmatch(proxy_wallet):
            proxy_wallet = submitted
        inferred_label = (
            (profile.name if profile else None)
            or (profile.pseudonym if profile else None)
            or f"{proxy_wallet[:6]}…{proxy_wallet[-4:]}"
        )
        label = (requested_label or "").strip() or str(inferred_label).strip()
        if not label:
            label = f"{proxy_wallet[:6]}…{proxy_wallet[-4:]}"
        return PublicProfile(
            submitted_address=submitted,
            proxy_wallet=proxy_wallet,
            label=label[:100],
        )

    async def fetch_active_positions(
        self,
        user: str,
        *,
        condition_ids: Iterable[str] | None = None,
    ) -> list[PositionSnapshot]:
        # `mergeable` is a filter, not an output toggle. Both complete result sets are
        # required before this method returns a snapshot that is safe for absence checks.
        conditions = list(dict.fromkeys(condition_ids or []))
        non_mergeable, mergeable = await asyncio.gather(
            self._fetch_positions_variant(
                user,
                redeemable=False,
                mergeable=False,
                condition_ids=conditions,
            ),
            self._fetch_positions_variant(
                user,
                redeemable=False,
                mergeable=True,
                condition_ids=conditions,
            ),
        )
        by_asset: dict[str, PositionSnapshot] = {}
        for position in [*non_mergeable, *mergeable]:
            if position.size > ZERO:
                by_asset[position.asset_id] = position
        return list(by_asset.values())

    async def fetch_redeemable_positions(
        self,
        user: str,
        *,
        condition_ids: Iterable[str] | None = None,
    ) -> list[PositionSnapshot]:
        """Return the complete redeemable snapshot for the requested conditions."""
        conditions = list(dict.fromkeys(condition_ids or []))
        non_mergeable, mergeable = await asyncio.gather(
            self._fetch_positions_variant(
                user,
                redeemable=True,
                mergeable=False,
                condition_ids=conditions,
            ),
            self._fetch_positions_variant(
                user,
                redeemable=True,
                mergeable=True,
                condition_ids=conditions,
            ),
        )
        by_asset: dict[str, PositionSnapshot] = {}
        for position in [*non_mergeable, *mergeable]:
            if position.size > ZERO:
                by_asset[position.asset_id] = position
        return list(by_asset.values())

    async def fetch_closed_positions(self, user: str) -> list[ClosedPositionSnapshot]:
        cache_key = user.lower()
        cached = self._closed_positions_cache.get(cache_key)
        now = monotonic()
        if cached is not None and cached[0] > now:
            return list(cached[1])
        offset = 0
        limit = 50
        results: list[ClosedPositionSnapshot] = []
        while True:
            payload = await self._get_json(
                f"{self.data_api_url}/closed-positions",
                params={
                    "user": user,
                    "limit": limit,
                    "offset": offset,
                    "sortBy": "TIMESTAMP",
                    "sortDirection": "DESC",
                },
            )
            if not isinstance(payload, list):
                raise PolymarketAPIError("已结仓接口返回格式无效")
            for item in payload:
                asset_id = str(item.get("asset") or "")
                condition_id = str(item.get("conditionId") or "")
                if not asset_id or not condition_id:
                    continue
                results.append(
                    ClosedPositionSnapshot(
                        asset_id=asset_id,
                        condition_id=condition_id,
                        title=str(item.get("title") or "未命名市场"),
                        outcome=str(item.get("outcome") or ""),
                        event_slug=item.get("eventSlug"),
                        market_slug=item.get("slug"),
                        avg_price=to_decimal(item.get("avgPrice")),
                        total_bought=to_decimal(item.get("totalBought")),
                        realized_pnl=to_decimal(item.get("realizedPnl")),
                        closed_at=parse_datetime(item.get("timestamp")),
                    )
                )
            if len(payload) < limit:
                self._closed_positions_cache[cache_key] = (
                    now + 30,
                    tuple(results),
                )
                return results
            offset += limit
            if offset > 100_000:
                raise PolymarketAPIError("已结仓分页超过官方接口上限")

    async def _fetch_positions_variant(
        self,
        user: str,
        *,
        redeemable: bool,
        mergeable: bool,
        condition_ids: Iterable[str] | None = None,
    ) -> list[PositionSnapshot]:
        offset = 0
        results: list[PositionSnapshot] = []
        market = ",".join(dict.fromkeys(condition_ids or []))
        while True:
            params: dict[str, Any] = {
                "user": user,
                "redeemable": str(redeemable).lower(),
                "mergeable": str(mergeable).lower(),
                "sizeThreshold": 0,
                "limit": self.POSITION_PAGE_SIZE,
                "offset": offset,
                "sortBy": "CURRENT",
                "sortDirection": "DESC",
            }
            if market:
                params["market"] = market
            payload = await self._get_json(
                f"{self.data_api_url}/positions",
                params=params,
            )
            if not isinstance(payload, list):
                raise PolymarketAPIError("持仓接口返回格式无效")
            parsed = [self._parse_position(item) for item in payload]
            results.extend(parsed)
            if len(payload) < self.POSITION_PAGE_SIZE:
                return results
            offset += self.POSITION_PAGE_SIZE
            if offset > 10_000:
                raise PolymarketAPIError("持仓分页超过官方接口上限")

    @staticmethod
    def _parse_position(item: dict[str, Any]) -> PositionSnapshot:
        asset_id = str(item.get("asset") or "")
        condition_id = str(item.get("conditionId") or "")
        if not asset_id or not condition_id:
            raise PolymarketAPIError("持仓条目缺少 asset 或 conditionId")
        return PositionSnapshot(
            asset_id=asset_id,
            condition_id=condition_id,
            title=str(item.get("title") or "未命名市场"),
            outcome=str(item.get("outcome") or ""),
            outcome_index=(
                int(item["outcomeIndex"]) if item.get("outcomeIndex") is not None else None
            ),
            icon_url=item.get("icon"),
            event_slug=item.get("eventSlug"),
            market_slug=item.get("slug"),
            size=to_decimal(item.get("size")),
            avg_price=to_decimal(item.get("avgPrice")),
            current_price=to_decimal(item.get("curPrice")),
            initial_value=to_decimal(item.get("initialValue")),
            current_value=to_decimal(item.get("currentValue")),
            cash_pnl=to_decimal(item.get("cashPnl")),
            percent_pnl=to_decimal(item.get("percentPnl")),
            total_bought=to_decimal(item.get("totalBought")),
            realized_pnl=to_decimal(item.get("realizedPnl")),
            end_date=parse_datetime(item.get("endDate")),
        )

    async def fetch_settlement_evidence(
        self,
        user: str,
        *,
        condition_ids: Iterable[str],
        last_seen_by_condition: dict[str, datetime],
    ) -> SettlementEvidence:
        conditions = list(dict.fromkeys(condition_ids))
        if not conditions:
            return SettlementEvidence(frozenset(), frozenset(), frozenset())

        # These are targeted by condition ID. We intentionally never enumerate all
        # redeemable positions for a wallet; large wallets can have thousands.
        redeemable_false, redeemable_true, markets, activity = await asyncio.gather(
            self._fetch_positions_variant(
                user,
                redeemable=True,
                mergeable=False,
                condition_ids=conditions,
            ),
            self._fetch_positions_variant(
                user,
                redeemable=True,
                mergeable=True,
                condition_ids=conditions,
            ),
            self._get_json(
                f"{self.gamma_api_url}/markets",
                params={
                    "condition_ids": conditions,
                    "closed": "true",
                    "limit": len(conditions),
                },
            ),
            self._fetch_activity(
                user,
                conditions,
                start=(
                    min(last_seen_by_condition.values()) - timedelta(seconds=120)
                    if last_seen_by_condition
                    else None
                ),
            ),
        )
        redeemable_assets = {item.asset_id for item in [*redeemable_false, *redeemable_true]}
        if not isinstance(markets, list):
            raise PolymarketAPIError("市场状态接口返回格式无效")
        resolved_conditions = {
            str(item.get("conditionId"))
            for item in markets
            if item.get("closed") is True
            or str(item.get("umaResolutionStatus") or "").lower() in {"resolved", "finalized"}
        }
        non_trade_conditions: set[str] = set()
        for item in activity:
            condition_id = str(item.get("conditionId") or "")
            timestamp = parse_datetime(item.get("timestamp"))
            activity_type = str(item.get("type") or "").upper()
            last_seen = last_seen_by_condition.get(condition_id)
            if (
                condition_id
                and activity_type in {"REDEEM", "MERGE", "SPLIT"}
                and timestamp is not None
                and (last_seen is None or timestamp >= last_seen - timedelta(seconds=120))
            ):
                non_trade_conditions.add(condition_id)
        return SettlementEvidence(
            resolved_condition_ids=frozenset(resolved_conditions),
            redeemable_asset_ids=frozenset(redeemable_assets),
            non_trade_condition_ids=frozenset(non_trade_conditions),
        )

    async def _fetch_activity(
        self,
        user: str,
        condition_ids: Iterable[str],
        *,
        start: datetime | None,
    ) -> list[dict[str, Any]]:
        offset = 0
        limit = 500
        results: list[dict[str, Any]] = []
        while True:
            params: dict[str, Any] = {
                "user": user,
                "market": ",".join(condition_ids),
                "type": "SPLIT,MERGE,REDEEM",
                "limit": limit,
                "offset": offset,
                "sortDirection": "DESC",
            }
            if start is not None:
                params["start"] = int(start.replace(tzinfo=UTC).timestamp())
            payload = await self._get_json(
                f"{self.data_api_url}/activity",
                params=params,
            )
            if not isinstance(payload, list):
                raise PolymarketAPIError("活动接口返回格式无效")
            results.extend(payload)
            if len(payload) < limit:
                return results
            offset += limit
            if offset > 5_000:
                raise PolymarketAPIError("活动分页超过官方接口上限")

    async def fetch_redemptions(
        self,
        user: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[RedemptionSnapshot]:
        offset = 0
        limit = 500
        results: list[RedemptionSnapshot] = []
        while True:
            params: dict[str, Any] = {
                "user": user,
                "type": "REDEEM",
                "limit": limit,
                "offset": offset,
                "sortDirection": "DESC",
            }
            if start is not None:
                params["start"] = int(start.replace(tzinfo=UTC).timestamp())
            if end is not None:
                params["end"] = int(end.replace(tzinfo=UTC).timestamp())
            payload = await self._get_json(
                f"{self.data_api_url}/activity",
                params=params,
            )
            if not isinstance(payload, list):
                raise PolymarketAPIError("赎回活动接口返回格式无效")
            for item in payload:
                if str(item.get("type") or "").upper() != "REDEEM":
                    continue
                condition_id = str(item.get("conditionId") or "")
                timestamp = parse_datetime(item.get("timestamp"))
                size = to_decimal(item.get("size"))
                usdc_size = to_decimal(item.get("usdcSize"))
                if not condition_id or timestamp is None or size <= ZERO:
                    continue
                if (start is not None and timestamp < start) or (
                    end is not None and timestamp > end
                ):
                    continue
                results.append(
                    RedemptionSnapshot(
                        asset_id=str(item.get("asset") or ""),
                        condition_id=condition_id,
                        title=str(item.get("title") or "未命名市场"),
                        outcome=str(item.get("outcome") or ""),
                        outcome_index=(
                            int(item["outcomeIndex"])
                            if item.get("outcomeIndex") is not None
                            else None
                        ),
                        event_slug=item.get("eventSlug"),
                        market_slug=item.get("slug"),
                        size=size,
                        usdc_size=usdc_size,
                        timestamp=timestamp,
                        transaction_hash=item.get("transactionHash"),
                    )
                )
            if len(payload) < limit:
                return results
            offset += limit
            if offset > 5_000:
                raise PolymarketAPIError("赎回活动分页超过官方接口上限")

    async def fetch_trades(
        self,
        user: str,
        *,
        condition_ids: Iterable[str] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[TradeSnapshot]:
        conditions = list(dict.fromkeys(condition_ids or []))
        limit = 500
        results: list[TradeSnapshot] = []
        condition_batches: list[list[str] | None] = (
            [
                conditions[offset : offset + self.TRADE_MARKET_BATCH_SIZE]
                for offset in range(0, len(conditions), self.TRADE_MARKET_BATCH_SIZE)
            ]
            if conditions
            else [None]
        )
        for condition_batch in condition_batches:
            offset = 0
            while True:
                params: dict[str, Any] = {
                    "user": user,
                    "takerOnly": "false",
                    "limit": limit,
                    "offset": offset,
                }
                if condition_batch:
                    params["market"] = ",".join(condition_batch)
                if start is not None:
                    params["start"] = int(start.replace(tzinfo=UTC).timestamp())
                if end is not None:
                    params["end"] = int(end.replace(tzinfo=UTC).timestamp())
                payload = await self._get_json(
                    f"{self.data_api_url}/trades",
                    params=params,
                )
                if not isinstance(payload, list):
                    raise PolymarketAPIError("成交接口返回格式无效")
                page_timestamps: list[datetime] = []
                for item in payload:
                    timestamp = parse_datetime(item.get("timestamp"))
                    side = str(item.get("side") or "").upper()
                    if timestamp is None or side not in {"BUY", "SELL"}:
                        continue
                    page_timestamps.append(timestamp)
                    if (start is not None and timestamp < start) or (
                        end is not None and timestamp > end
                    ):
                        continue
                    results.append(
                        TradeSnapshot(
                            asset_id=str(item.get("asset") or ""),
                            condition_id=str(item.get("conditionId") or ""),
                            side=side,
                            size=to_decimal(item.get("size")),
                            price=to_decimal(item.get("price")),
                            timestamp=timestamp,
                            transaction_hash=item.get("transactionHash"),
                            title=str(item.get("title") or "") or None,
                            outcome=str(item.get("outcome") or "") or None,
                            outcome_index=(
                                int(item["outcomeIndex"])
                                if item.get("outcomeIndex") is not None
                                else None
                            ),
                            event_slug=item.get("eventSlug"),
                            market_slug=item.get("slug"),
                        )
                    )
                if len(payload) < limit or (
                    start is not None and page_timestamps and min(page_timestamps) < start
                ):
                    break
                offset += limit
                if offset > 10_000:
                    raise PolymarketAPIError("成交分页超过官方接口上限")
        return results
