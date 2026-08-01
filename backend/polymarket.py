from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import httpx

ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{40}")
ZERO = Decimal("0")


class PolymarketAPIError(RuntimeError):
    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


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
class TradeSnapshot:
    asset_id: str
    condition_id: str
    side: str
    size: Decimal
    price: Decimal
    timestamp: datetime
    transaction_hash: str | None


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

    def __init__(
        self,
        *,
        data_api_url: str,
        gamma_api_url: str,
        clob_api_url: str = "https://clob.polymarket.com",
        timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.data_api_url = data_api_url.rstrip("/")
        self.gamma_api_url = gamma_api_url.rstrip("/")
        self.clob_api_url = clob_api_url.rstrip("/")
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            transport=transport,
            headers={"User-Agent": "polymarket-wallet-monitor/0.1"},
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def _get_json(
        self,
        url: str,
        *,
        params: dict[str, Any],
    ) -> Any:
        try:
            response = await self._http.get(url, params=params)
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            raise PolymarketAPIError(f"Polymarket 接口连接失败：{error}") from error
        if response.status_code == 429:
            retry_after = self._parse_retry_after(response.headers.get("Retry-After"))
            raise PolymarketAPIError("Polymarket 接口请求过于频繁", retry_after=retry_after)
        if response.status_code >= 400:
            detail = response.text[:300].strip()
            raise PolymarketAPIError(
                f"Polymarket 接口返回 {response.status_code}" + (f"：{detail}" if detail else "")
            )
        try:
            return response.json()
        except ValueError as error:
            raise PolymarketAPIError("Polymarket 接口返回了无效 JSON") from error

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

    async def resolve_profile(self, raw_input: str, requested_label: str | None) -> PublicProfile:
        submitted = parse_wallet_input(raw_input)
        try:
            payload = await self._get_json(
                f"{self.gamma_api_url}/public-profile",
                params={"address": submitted},
            )
        except PolymarketAPIError as error:
            if "返回 404" not in str(error):
                raise
            payload = {}

        proxy_wallet = str(payload.get("proxyWallet") or submitted).lower()
        if not ADDRESS_RE.fullmatch(proxy_wallet):
            proxy_wallet = submitted
        inferred_label = (
            payload.get("name")
            or payload.get("pseudonym")
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

    async def fetch_active_positions(self, user: str) -> list[PositionSnapshot]:
        # `mergeable` is a filter, not an output toggle. Both complete result sets are
        # required before this method returns a snapshot that is safe for absence checks.
        non_mergeable, mergeable = await asyncio.gather(
            self._fetch_positions_variant(user, redeemable=False, mergeable=False),
            self._fetch_positions_variant(user, redeemable=False, mergeable=True),
        )
        by_asset: dict[str, PositionSnapshot] = {}
        for position in [*non_mergeable, *mergeable]:
            if position.size > ZERO:
                by_asset[position.asset_id] = position
        return list(by_asset.values())

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
                    "condition_ids": ",".join(conditions),
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
        condition_ids: Iterable[str],
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[TradeSnapshot]:
        conditions = list(dict.fromkeys(condition_ids))
        if not conditions:
            return []
        limit = 500
        results: list[TradeSnapshot] = []
        for condition_offset in range(
            0,
            len(conditions),
            self.TRADE_MARKET_BATCH_SIZE,
        ):
            condition_batch = conditions[
                condition_offset : condition_offset + self.TRADE_MARKET_BATCH_SIZE
            ]
            offset = 0
            while True:
                params: dict[str, Any] = {
                    "user": user,
                    "market": ",".join(condition_batch),
                    "takerOnly": "false",
                    "limit": limit,
                    "offset": offset,
                }
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
