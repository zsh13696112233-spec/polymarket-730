from __future__ import annotations

import asyncio
import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from backend.config import Settings
from backend.copy_trading import (
    REDEMPTION_SIZE_TOLERANCE,
    market_worst_price,
    redeemable_position_payout_rate,
)
from backend.db import Database
from backend.keychain import KeychainReference, MacOSKeychain
from backend.models import (
    CopyPosition,
    CopyRedemptionExecution,
    ExecutionAccount,
    WhaleEntry,
    WhaleFill,
    WhaleFollowLedger,
    WhaleFollowPosition,
    WhaleMarket,
    WhaleOrder,
    WhaleRedemption,
    WhaleSettings,
    WhaleTag,
    WhaleTrade,
    WhaleWallet,
)
from backend.monitor import utcnow
from backend.polymarket import PolymarketAPIError, PolymarketClient
from backend.trading import (
    MarketTradeRequest,
    RedemptionSubmissionUnknown,
    TradeFillResult,
    TradeResult,
    TradingUnavailable,
    UnifiedPolymarketTrader,
    normalize_fak_result,
)

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")
SETTLED_PRICE_THRESHOLD = Decimal("0.999")
PROFILE_MISSING_CACHE = timedelta(days=7)
TAG_CACHE = timedelta(hours=24)
MARKET_CACHE = timedelta(seconds=60)


def _decimal(value: Any, default: Decimal = ZERO) -> Decimal:
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except (TypeError, ValueError):
        return default


def _json_list(value: str | list[Any] | tuple[Any, ...] | None) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _attribute(item: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if hasattr(item, name):
            value = getattr(item, name)
            if value is not None:
                return value
        if isinstance(item, dict) and item.get(name) is not None:
            return item[name]
    return default


def _display_name(trade: Any) -> str:
    explicit = str(_attribute(trade, "display_name", "name", default="") or "").strip()
    if explicit:
        return explicit[:200]
    pseudonym = str(_attribute(trade, "pseudonym", default="") or "").strip()
    if pseudonym:
        return pseudonym[:200]
    wallet = str(_attribute(trade, "proxy_wallet", default="") or "")
    return f"{wallet[:6]}…{wallet[-4:]}" if len(wallet) > 12 else wallet


def fingerprint_large_trades(trades: Iterable[Any]) -> list[tuple[str, Any]]:
    """Build stable fingerprints after all pages have been collected.

    Occurrence numbers are deliberately scoped to a complete collection round.  This
    preserves two indistinguishable fills in the same transaction while keeping the
    same fills stable across overlapping rounds.
    """

    ordered = sorted(
        trades,
        key=lambda item: (
            _attribute(item, "timestamp") or datetime.min,
            str(_attribute(item, "transaction_hash", default="") or ""),
            str(_attribute(item, "asset_id", "asset", default="") or ""),
            str(_attribute(item, "side", default="") or ""),
            str(_attribute(item, "price", default="") or ""),
            str(_attribute(item, "size", default="") or ""),
        ),
    )
    occurrences: dict[str, int] = defaultdict(int)
    result: list[tuple[str, Any]] = []
    for trade in ordered:
        base = "|".join(
            [
                str(_attribute(trade, "proxy_wallet", default="") or "").lower(),
                str(_attribute(trade, "transaction_hash", default="") or ""),
                str(_attribute(trade, "asset_id", "asset", default="") or ""),
                str(_attribute(trade, "side", default="") or "").upper(),
                str(_decimal(_attribute(trade, "price"))),
                str(_decimal(_attribute(trade, "size"))),
            ]
        )
        occurrence = occurrences[base]
        occurrences[base] += 1
        digest = hashlib.sha256(f"{base}|{occurrence}".encode()).hexdigest()
        result.append((digest, trade))
    return result


@dataclass(frozen=True, slots=True)
class WhaleAggregate:
    proxy_wallet: str
    asset_id: str
    condition_id: str
    outcome: str
    outcome_index: int
    gross_buy_usdc: Decimal
    gross_buy_size: Decimal
    sold_size: Decimal
    sold_usdc: Decimal
    net_size: Decimal
    net_ratio_percent: Decimal
    avg_buy_price: Decimal
    max_single_usdc: Decimal
    trade_count: int
    first_buy_at: datetime
    last_buy_at: datetime
    status: str
    hedged: bool = False


def aggregate_whale_trades(
    trades: Iterable[Any],
    *,
    single_trade_threshold_usdc: Decimal,
    cumulative_threshold_usdc: Decimal,
    holding_ratio_threshold: Decimal = Decimal("80"),
    exited_ratio_threshold: Decimal = Decimal("20"),
) -> list[WhaleAggregate]:
    """Aggregate one rolling window into directional wallet/outcome entries."""

    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for trade in trades:
        wallet = str(_attribute(trade, "proxy_wallet", default="") or "").lower()
        asset_id = str(_attribute(trade, "asset_id", default="") or "")
        side = str(_attribute(trade, "side", default="") or "").upper()
        if wallet and asset_id and side in {"BUY", "SELL"}:
            groups[(wallet, asset_id)].append(trade)

    aggregates: list[WhaleAggregate] = []
    for (wallet, asset_id), items in groups.items():
        buys = [item for item in items if str(_attribute(item, "side")).upper() == "BUY"]
        if not buys:
            continue
        gross_size = sum((_decimal(_attribute(item, "size")) for item in buys), ZERO)
        gross_usdc = sum(
            (
                _decimal(_attribute(item, "amount"))
                or _decimal(_attribute(item, "size")) * _decimal(_attribute(item, "price"))
                for item in buys
            ),
            ZERO,
        )
        if gross_size <= ZERO:
            continue
        max_single = max(
            (
                _decimal(_attribute(item, "amount"))
                or _decimal(_attribute(item, "size")) * _decimal(_attribute(item, "price"))
                for item in buys
            ),
            default=ZERO,
        )
        if max_single < single_trade_threshold_usdc and gross_usdc < cumulative_threshold_usdc:
            continue
        sells = [item for item in items if str(_attribute(item, "side")).upper() == "SELL"]
        sold_size = sum((_decimal(_attribute(item, "size")) for item in sells), ZERO)
        sold_usdc = sum(
            (
                _decimal(_attribute(item, "amount"))
                or _decimal(_attribute(item, "size")) * _decimal(_attribute(item, "price"))
                for item in sells
            ),
            ZERO,
        )
        net_size = gross_size - sold_size
        # SELL can include inventory bought before the rolling window.  Preserve
        # net_size for diagnostics but clamp the ratio to its stored 0..100 domain.
        net_ratio = max(ZERO, min(HUNDRED, net_size / gross_size * HUNDRED))
        if net_ratio > holding_ratio_threshold:
            status = "holding"
        elif net_ratio > exited_ratio_threshold:
            status = "reduced"
        else:
            status = "exited"
        timestamps = [
            value
            for value in (_attribute(item, "timestamp") for item in buys)
            if isinstance(value, datetime)
        ]
        if not timestamps:
            continue
        first = buys[0]
        aggregates.append(
            WhaleAggregate(
                proxy_wallet=wallet,
                asset_id=asset_id,
                condition_id=str(_attribute(first, "condition_id", default="") or ""),
                outcome=str(_attribute(first, "outcome", default="") or ""),
                outcome_index=int(_attribute(first, "outcome_index", default=0) or 0),
                gross_buy_usdc=gross_usdc,
                gross_buy_size=gross_size,
                sold_size=sold_size,
                sold_usdc=sold_usdc,
                net_size=net_size,
                net_ratio_percent=net_ratio,
                avg_buy_price=gross_usdc / gross_size,
                max_single_usdc=max_single,
                trade_count=len(buys),
                first_buy_at=min(timestamps),
                last_buy_at=max(timestamps),
                status=status,
            )
        )

    assets_by_wallet_market: dict[tuple[str, str], set[str]] = defaultdict(set)
    for aggregate in aggregates:
        assets_by_wallet_market[(aggregate.proxy_wallet, aggregate.condition_id)].add(
            aggregate.asset_id
        )
    return [
        replace(
            aggregate,
            hedged=(
                len(assets_by_wallet_market[(aggregate.proxy_wallet, aggregate.condition_id)]) >= 2
            ),
        )
        for aggregate in aggregates
    ]


def market_price_is_settled(market: WhaleMarket) -> bool:
    """任一结果报价已经贴到 1 说明胜负已分。

    赛果已定的市场在接口上往往仍是 `closed=false`，只是报价停在 0.9995 这类
    「买一 0.999 / 卖一 1.000」的中间价上。剩余空间已经小于最小报价单位，跟进
    只会亏手续费，所以按 `SETTLED_PRICE_THRESHOLD` 而不是严格等于 1 来判定。
    """

    prices = [_decimal(value) for value in _json_list(market.outcome_prices_json)]
    return any(price >= SETTLED_PRICE_THRESHOLD for price in prices)


def market_is_eligible(
    market: WhaleMarket,
    *,
    now: datetime,
    min_liquidity_usdc: Decimal,
    min_remaining_minutes: int,
) -> bool:
    if market.closed or not market.active or not market.accepting_orders:
        return False
    if market_price_is_settled(market):
        return False
    if market.liquidity < min_liquidity_usdc:
        return False
    if (
        market.end_date is not None
        and not market.end_date_is_date_only
        and market.end_date <= now + timedelta(minutes=min_remaining_minutes)
    ):
        return False
    return True


class WhaleDiscoveryScanner:
    """Isolated rolling-window collector and aggregator."""

    def __init__(
        self,
        *,
        database: Database,
        client: PolymarketClient,
        settings: Settings,
        executor: WhaleFollowExecutor | None = None,
    ) -> None:
        self.database = database
        self.client = client
        self.settings = settings
        self.executor = executor
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()
        self._last_trade_timestamp: datetime | None = None
        self._running_config_at: datetime | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="whale-discovery")

    def wake(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                # tick persists its own health state.  This last boundary keeps the
                # scanner from ever taking down the wallet monitor or copy engine.
                pass
            async with self.database.sessions() as session:
                whale_settings = await session.get(WhaleSettings, 1)
                configured = (
                    whale_settings.scan_interval_seconds
                    if whale_settings is not None
                    else self.settings.whale_scan_interval_seconds
                )
                failures = whale_settings.consecutive_failures if whale_settings else 0
            delay = min(
                self.settings.max_backoff_seconds,
                max(1.0, float(configured)) * (2 ** min(failures, 6)),
            )
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except TimeoutError:
                pass

    async def scan_now(self) -> bool:
        """手动扫描：返回时保证已经有一轮读到最新配置的扫描跑完。

        一轮扫描要几十秒，而后台循环大半时间都在跑，直接跳过会让「立即扫描」看上去
        毫无反应；无条件排队又会让刚改完设置的用户白等两轮。所以正在跑的那轮只要已经
        读到当前配置就等它收尾，否则再补一轮。
        """

        async with self.database.sessions() as session:
            row = await session.get(WhaleSettings, 1)
            if row is None or not row.enabled:
                return False
            config_at = row.updated_at
        running_config_at = self._running_config_at
        if self._lock.locked() and running_config_at is not None and running_config_at >= config_at:
            async with self._lock:
                return True
        return await self.tick(wait=True)

    async def tick(self, *, wait: bool = False) -> bool:
        if self._lock.locked() and not wait:
            return False
        async with self._lock:
            async with self.database.sessions() as session:
                whale_settings = await session.get(WhaleSettings, 1)
                if whale_settings is None or not whale_settings.enabled:
                    return False
                values = {
                    column.name: getattr(whale_settings, column.name)
                    for column in WhaleSettings.__table__.columns
                }
                self._running_config_at = whale_settings.updated_at
            try:
                warning = await self._scan(values)
                if self.executor is not None:
                    try:
                        await self.executor.process_redeemable_positions()
                    except Exception as error:
                        warning = warning or f"自动赎回检查失败：{error}"
                async with self.database.sessions() as session:
                    row = await session.get(WhaleSettings, 1)
                    if row is not None:
                        row.last_scan_at = utcnow()
                        row.last_scan_error = warning
                        row.consecutive_failures = 0
                        row.updated_at = utcnow()
                        await session.commit()
                return True
            except asyncio.CancelledError:
                raise
            except Exception as error:
                async with self.database.sessions() as session:
                    row = await session.get(WhaleSettings, 1)
                    if row is not None:
                        row.last_scan_error = str(error)[:2000]
                        row.consecutive_failures += 1
                        row.updated_at = utcnow()
                        await session.commit()
                return False
            finally:
                self._running_config_at = None

    async def _scan(self, config: dict[str, Any]) -> str | None:
        now = utcnow()
        window_start = now - timedelta(hours=int(config["window_hours"]))
        incremental_start = window_start
        if self._last_trade_timestamp is not None:
            incremental_start = max(
                window_start, self._last_trade_timestamp - timedelta(seconds=120)
            )
        trades, hit_page_limit = await self._collect_trades(
            start=incremental_start,
            amount=_decimal(config["collect_filter_amount_usdc"]),
        )
        await self._persist_trades(trades)
        if trades:
            self._last_trade_timestamp = max(_attribute(item, "timestamp") for item in trades)

        async with self.database.sessions() as session:
            window_trades = list(
                (
                    await session.scalars(
                        select(WhaleTrade)
                        .where(WhaleTrade.timestamp >= window_start)
                        .order_by(WhaleTrade.timestamp.asc(), WhaleTrade.id.asc())
                    )
                ).all()
            )
        aggregates = aggregate_whale_trades(
            window_trades,
            single_trade_threshold_usdc=_decimal(config["single_trade_threshold_usdc"]),
            cumulative_threshold_usdc=_decimal(config["cumulative_threshold_usdc"]),
            holding_ratio_threshold=_decimal(config["holding_ratio_threshold"]),
            exited_ratio_threshold=_decimal(config["exited_ratio_threshold"]),
        )
        condition_ids = list(
            dict.fromkeys(item.condition_id for item in aggregates if item.condition_id)
        )
        await self._refresh_markets(condition_ids, now=now)
        await self._refresh_wallets(
            list(dict.fromkeys(item.proxy_wallet for item in aggregates)),
            now=now,
            cache_hours=int(config["profile_cache_hours"]),
        )
        await self._refresh_tags(now=now)
        await self._replace_entries(
            aggregates,
            config=config,
            now=now,
            window_start=window_start,
        )
        await self._update_tag_counts(now=now)
        async with self.database.sessions() as session:
            await session.execute(
                delete(WhaleTrade).where(
                    WhaleTrade.timestamp
                    < now - timedelta(hours=int(config["trade_retention_hours"]))
                )
            )
            await session.commit()
        return "扫描达到分页上限，结果可能不完整" if hit_page_limit else None

    async def _collect_trades(
        self,
        *,
        start: datetime,
        amount: Decimal,
    ) -> tuple[list[Any], bool]:
        limit = 500
        results: list[Any] = []
        for page in range(self.settings.whale_max_scan_pages):
            offset = page * limit
            payload = await self._retry(
                self.client.fetch_large_trades,
                filter_amount_usdc=amount,
                start=start,
                limit=limit,
                offset=offset,
            )
            page_items = list(payload)
            results.extend(page_items)
            timestamps = [
                _attribute(item, "timestamp")
                for item in page_items
                if isinstance(_attribute(item, "timestamp"), datetime)
            ]
            if len(page_items) < limit or (timestamps and min(timestamps) < start):
                return results, False
        return results, True

    async def _retry(self, operation: Any, /, **kwargs: Any) -> Any:
        for attempt in range(3):
            try:
                return await operation(**kwargs)
            except PolymarketAPIError as error:
                if attempt >= 2:
                    raise
                await asyncio.sleep(max(0.05, float(error.retry_after or (attempt + 1))))
        raise AssertionError("unreachable")

    async def _persist_trades(self, trades: Iterable[Any]) -> None:
        fingerprinted = fingerprint_large_trades(trades)
        for offset in range(0, len(fingerprinted), 200):
            batch = fingerprinted[offset : offset + 200]
            async with self.database.sessions() as session:
                known = set(
                    (
                        await session.scalars(
                            select(WhaleTrade.fingerprint).where(
                                WhaleTrade.fingerprint.in_([item[0] for item in batch])
                            )
                        )
                    ).all()
                )
                now = utcnow()
                for fingerprint, trade in batch:
                    if fingerprint in known:
                        continue
                    size = _decimal(_attribute(trade, "size"))
                    price = _decimal(_attribute(trade, "price"))
                    session.add(
                        WhaleTrade(
                            fingerprint=fingerprint,
                            proxy_wallet=str(_attribute(trade, "proxy_wallet") or "").lower(),
                            asset_id=str(_attribute(trade, "asset_id", "asset") or ""),
                            condition_id=str(_attribute(trade, "condition_id") or ""),
                            side=str(_attribute(trade, "side") or "").upper(),
                            size=size,
                            price=price,
                            amount=_decimal(_attribute(trade, "amount")) or size * price,
                            outcome=str(_attribute(trade, "outcome") or ""),
                            outcome_index=int(_attribute(trade, "outcome_index", default=0) or 0),
                            title=str(_attribute(trade, "title") or ""),
                            market_slug=str(_attribute(trade, "market_slug", "slug") or ""),
                            event_slug=str(_attribute(trade, "event_slug") or ""),
                            icon_url=_attribute(trade, "icon_url", "icon"),
                            display_name=_display_name(trade),
                            transaction_hash=_attribute(trade, "transaction_hash"),
                            timestamp=_attribute(trade, "timestamp"),
                            imported_at=now,
                        )
                    )
                try:
                    await session.commit()
                except IntegrityError:
                    # Another manual scan may have won the unique-key race.
                    await session.rollback()

    async def _refresh_markets(self, condition_ids: list[str], *, now: datetime) -> None:
        if not condition_ids:
            return
        async with self.database.sessions() as session:
            fresh = set(
                (
                    await session.scalars(
                        select(WhaleMarket.condition_id).where(
                            WhaleMarket.condition_id.in_(condition_ids),
                            WhaleMarket.refreshed_at >= now - MARKET_CACHE,
                        )
                    )
                ).all()
            )
        wanted = [condition_id for condition_id in condition_ids if condition_id not in fresh]
        if not wanted:
            return
        markets = await self._retry(self.client.fetch_markets_with_tags, condition_ids=wanted)
        async with self.database.sessions() as session:
            existing = {
                item.condition_id: item
                for item in (
                    await session.scalars(
                        select(WhaleMarket).where(WhaleMarket.condition_id.in_(wanted))
                    )
                ).all()
            }
            for item in markets:
                condition_id = str(_attribute(item, "condition_id") or "")
                if not condition_id:
                    continue
                row = existing.get(condition_id)
                if row is None:
                    row = WhaleMarket(condition_id=condition_id)
                    session.add(row)
                tags = _attribute(item, "tags", default=[]) or []
                row.title = str(_attribute(item, "title", "question") or "未命名市场")
                row.market_slug = str(_attribute(item, "market_slug", "slug") or "")
                row.event_slug = str(_attribute(item, "event_slug") or "")
                row.icon_url = _attribute(item, "icon_url", "icon", "image")
                row.outcomes_json = _json_dump(_attribute(item, "outcomes", default=[]) or [])
                row.outcome_prices_json = _json_dump(
                    [str(value) for value in (_attribute(item, "outcome_prices", default=[]) or [])]
                )
                row.clob_token_ids_json = _json_dump(
                    [str(value) for value in (_attribute(item, "clob_token_ids", default=[]) or [])]
                )
                row.tags_json = _json_dump(
                    [
                        {
                            "id": str(_attribute(tag, "id", default="") or ""),
                            "label": str(_attribute(tag, "label", default="") or ""),
                            "slug": str(_attribute(tag, "slug", default="") or ""),
                        }
                        for tag in tags
                    ]
                )
                row.closed = bool(_attribute(item, "closed", default=False))
                row.active = bool(_attribute(item, "active", default=True))
                row.accepting_orders = bool(_attribute(item, "accepting_orders", default=False))
                row.neg_risk = bool(_attribute(item, "neg_risk", default=False))
                row.end_date = _attribute(item, "end_date")
                row.end_date_is_date_only = bool(
                    _attribute(item, "end_date_is_date_only", default=False)
                )
                row.liquidity = _decimal(_attribute(item, "liquidity"))
                row.volume_24h = _decimal(_attribute(item, "volume_24h"))
                row.best_bid = _attribute(item, "best_bid")
                row.best_ask = _attribute(item, "best_ask")
                row.order_min_size = _decimal(_attribute(item, "order_min_size"), Decimal("5"))
                row.tick_size = _decimal(_attribute(item, "tick_size"), Decimal("0.01"))
                row.fee_rate = _decimal(_attribute(item, "fee_rate"))
                row.fee_exponent = _decimal(_attribute(item, "fee_exponent"), ONE)
                row.refreshed_at = now
            await session.commit()

    async def _refresh_wallets(
        self,
        wallets: list[str],
        *,
        now: datetime,
        cache_hours: int,
    ) -> None:
        if not wallets:
            return
        async with self.database.sessions() as session:
            cached = {
                row.proxy_wallet: row
                for row in (
                    await session.scalars(
                        select(WhaleWallet).where(WhaleWallet.proxy_wallet.in_(wallets))
                    )
                ).all()
            }
        wanted = [
            wallet
            for wallet in wallets
            if wallet not in cached
            or (
                cached[wallet].refreshed_at
                < now
                - (
                    PROFILE_MISSING_CACHE
                    if cached[wallet].profile_missing
                    else timedelta(hours=cache_hours)
                )
            )
        ][: self.settings.whale_profile_batch_limit]
        for wallet in wanted:
            profile = await self._retry(self.client.fetch_public_profile, address=wallet)
            async with self.database.sessions() as session:
                row = await session.get(WhaleWallet, wallet)
                if row is None:
                    row = WhaleWallet(proxy_wallet=wallet)
                    session.add(row)
                if profile is None:
                    row.display_name = row.display_name or f"{wallet[:6]}…{wallet[-4:]}"
                    row.profile_missing = True
                else:
                    row.display_name = str(
                        _attribute(profile, "display_name", "name", "pseudonym", default="")
                        or f"{wallet[:6]}…{wallet[-4:]}"
                    )[:200]
                    row.pseudonym = str(_attribute(profile, "pseudonym", default="") or "")
                    row.profile_created_at = _attribute(profile, "created_at", "profile_created_at")
                    row.verified_badge = bool(_attribute(profile, "verified_badge", default=False))
                    row.taker_tier = _attribute(profile, "taker_tier")
                    row.taker_tier_name = _attribute(profile, "taker_tier_name")
                    row.weighted_volume = _attribute(profile, "weighted_volume")
                    row.profile_missing = False
                row.refreshed_at = now
                await session.commit()

    async def _refresh_tags(self, *, now: datetime) -> None:
        async with self.database.sessions() as session:
            latest = await session.scalar(select(func.max(WhaleTag.refreshed_at)))
        if latest is not None and latest >= now - TAG_CACHE:
            return
        tags = await self._retry(self.client.fetch_tags)
        async with self.database.sessions() as session:
            existing = {row.id: row for row in (await session.scalars(select(WhaleTag))).all()}
            for item in tags:
                tag_id = str(_attribute(item, "id", default="") or "")
                slug = str(_attribute(item, "slug", default="") or "")
                if not tag_id or not slug:
                    continue
                row = existing.get(tag_id)
                if row is None:
                    row = WhaleTag(id=tag_id, market_count=0)
                    session.add(row)
                row.slug = slug
                row.label = str(_attribute(item, "label", default=slug) or slug)
                row.refreshed_at = now
            await session.commit()

    async def _replace_entries(
        self,
        aggregates: list[WhaleAggregate],
        *,
        config: dict[str, Any],
        now: datetime,
        window_start: datetime,
    ) -> None:
        condition_ids = list(dict.fromkeys(item.condition_id for item in aggregates))
        async with self.database.sessions() as session:
            markets = {
                row.condition_id: row
                for row in (
                    await session.scalars(
                        select(WhaleMarket).where(WhaleMarket.condition_id.in_(condition_ids))
                    )
                ).all()
            }
            existing = {
                (row.proxy_wallet, row.asset_id): row
                for row in (await session.scalars(select(WhaleEntry))).all()
            }
            seen: set[tuple[str, str]] = set()
            for aggregate in aggregates:
                market = markets.get(aggregate.condition_id)
                if market is None or not market_is_eligible(
                    market,
                    now=now,
                    min_liquidity_usdc=_decimal(config["min_liquidity_usdc"]),
                    min_remaining_minutes=int(config["min_remaining_minutes"]),
                ):
                    continue
                key = (aggregate.proxy_wallet, aggregate.asset_id)
                seen.add(key)
                row = existing.get(key)
                if row is None:
                    row = WhaleEntry(
                        proxy_wallet=aggregate.proxy_wallet,
                        asset_id=aggregate.asset_id,
                    )
                    session.add(row)
                row.condition_id = aggregate.condition_id
                row.outcome = aggregate.outcome
                row.outcome_index = aggregate.outcome_index
                row.gross_buy_usdc = aggregate.gross_buy_usdc
                row.gross_buy_size = aggregate.gross_buy_size
                row.sold_size = aggregate.sold_size
                row.sold_usdc = aggregate.sold_usdc
                row.net_size = aggregate.net_size
                row.net_ratio = aggregate.net_ratio_percent
                row.avg_buy_price = aggregate.avg_buy_price
                row.max_single_usdc = aggregate.max_single_usdc
                row.trade_count = aggregate.trade_count
                row.first_buy_at = aggregate.first_buy_at
                row.last_buy_at = aggregate.last_buy_at
                row.status = aggregate.status
                row.hedged = aggregate.hedged
                row.window_start = window_start
                row.computed_at = now
            stale_ids = [row.id for key, row in existing.items() if key not in seen]
            if stale_ids:
                await session.execute(delete(WhaleEntry).where(WhaleEntry.id.in_(stale_ids)))
            await session.commit()

    async def _update_tag_counts(self, *, now: datetime) -> None:
        async with self.database.sessions() as session:
            market_ids = set(
                (await session.scalars(select(WhaleEntry.condition_id).distinct())).all()
            )
            markets = list(
                (
                    await session.scalars(
                        select(WhaleMarket).where(WhaleMarket.condition_id.in_(market_ids))
                    )
                ).all()
            )
            counts: dict[str, int] = defaultdict(int)
            for market in markets:
                for tag in _json_list(market.tags_json):
                    if isinstance(tag, dict) and tag.get("id"):
                        counts[str(tag["id"])] += 1
            for tag in (await session.scalars(select(WhaleTag))).all():
                tag.market_count = counts.get(tag.id, 0)
                if tag.refreshed_at is None:
                    tag.refreshed_at = now
            await session.commit()


@dataclass(frozen=True, slots=True)
class WhaleFollowQuote:
    entry_id: int | None
    source_wallet: str | None
    asset_id: str
    condition_id: str
    title: str
    outcome: str
    outcome_index: int | None
    neg_risk: bool
    market_slug: str | None
    event_slug: str | None
    icon_url: str | None
    amount_usdc: Decimal
    best_ask: Decimal
    worst_price: Decimal
    tick_size: Decimal
    minimum_order_usdc: Decimal
    estimated_shares: Decimal
    estimated_fee_usdc: Decimal
    total_cost_usdc: Decimal
    profit_ratio_percent: Decimal
    max_loss_usdc: Decimal
    whale_avg_price: Decimal | None
    whale_profit_ratio_percent: Decimal | None
    profit_ratio_gap_percent: Decimal | None
    price_delta_cents: Decimal | None
    price_delta_warning: bool
    reserve_warning: bool
    available_balance_usdc: Decimal


@dataclass(frozen=True, slots=True)
class WhaleSellQuote:
    position_id: int
    asset_id: str
    condition_id: str
    title: str
    outcome: str
    outcome_index: int | None
    neg_risk: bool
    size: Decimal
    best_bid: Decimal
    worst_price: Decimal
    tick_size: Decimal
    minimum_order_size: Decimal
    estimated_proceeds_usdc: Decimal
    estimated_fee_usdc: Decimal
    cost_basis_usdc: Decimal
    estimated_pnl_usdc: Decimal
    estimated_pnl_percent: Decimal


def estimated_market_fee(
    shares: Decimal,
    price: Decimal,
    fee_rate: Decimal,
    fee_exponent: Decimal,
) -> Decimal:
    if shares <= ZERO or price <= ZERO or price >= ONE or fee_rate <= ZERO:
        return ZERO
    try:
        curve = (price * (ONE - price)) ** fee_exponent
    except (TypeError, ValueError):
        curve = (price * (ONE - price)) ** int(fee_exponent)
    return shares * fee_rate * curve


def settlement_profit_ratio(
    amount_usdc: Decimal,
    price: Decimal,
    fee_rate: Decimal,
    fee_exponent: Decimal,
) -> Decimal:
    if amount_usdc <= ZERO or price <= ZERO:
        return ZERO
    shares = amount_usdc / price
    total_cost = amount_usdc + estimated_market_fee(shares, price, fee_rate, fee_exponent)
    return (shares - total_cost) / total_cost * HUNDRED if total_cost > ZERO else ZERO


class WhaleFollowExecutor:
    """Manual whale-follow execution with an independent durable ledger."""

    def __init__(
        self,
        *,
        database: Database,
        client: PolymarketClient,
        settings: Settings,
        keychain: MacOSKeychain,
        execution_lock: asyncio.Lock | None = None,
    ) -> None:
        self.database = database
        self.client = client
        self.settings = settings
        self.keychain = keychain
        self._lock = execution_lock or asyncio.Lock()
        self._trader_cache: UnifiedPolymarketTrader | None = None
        self._trader_cache_key: tuple[Any, ...] | None = None

    async def close(self) -> None:
        trader, self._trader_cache = self._trader_cache, None
        self._trader_cache_key = None
        if trader is not None:
            await trader.close()

    async def _account(self) -> ExecutionAccount:
        async with self.database.sessions() as session:
            account = await session.get(ExecutionAccount, 1)
            if (
                account is None
                or account.status not in {"ready", "insufficient_balance"}
                or account.signature_type not in {1, 3}
                or not account.keychain_service
                or not account.keychain_account
                or not account.funder_address
            ):
                raise ValueError("V2 执行钱包当前不可用")
            return account

    async def _trader(self, account: ExecutionAccount | None = None) -> UnifiedPolymarketTrader:
        account = account or await self._account()
        key = (
            account.keychain_service,
            account.keychain_account,
            account.signature_type,
            account.funder_address,
        )
        if self._trader_cache is not None and self._trader_cache_key == key:
            return self._trader_cache
        if self._trader_cache is not None:
            await self._trader_cache.close()
        assert account.keychain_service is not None
        assert account.keychain_account is not None
        trader = UnifiedPolymarketTrader(
            host=self.settings.clob_api_url,
            keychain=self.keychain,
            key_reference=KeychainReference(
                service=account.keychain_service,
                account=account.keychain_account,
            ),
            signature_type=account.signature_type,
            funder_address=account.funder_address,
            relayer_url=self.settings.relayer_api_url,
            rpc_url=self.settings.polygon_rpc_url,
        )
        self._trader_cache = trader
        self._trader_cache_key = key
        return trader

    async def _live_balance(self, account: ExecutionAccount) -> Decimal:
        try:
            balance = await (await self._trader(account)).collateral_balance()
        except Exception as error:
            async with self.database.sessions() as session:
                current = await session.get(ExecutionAccount, 1)
                if current is not None:
                    current.last_error = f"巨鲸跟单余额刷新失败：{error}"[:1000]
                    current.updated_at = utcnow()
                    await session.commit()
            raise
        async with self.database.sessions() as session:
            current = await session.get(ExecutionAccount, 1)
            if current is not None:
                current.collateral_balance = balance
                current.last_balance_at = utcnow()
                current.last_error = None
                current.updated_at = utcnow()
                # Deliberately do not mutate status: cash reserve is a warning for
                # manual whale orders, while CopyTradingEngine owns its status rule.
                await session.commit()
        return balance

    async def _market_for_asset(self, asset_id: str) -> tuple[WhaleMarket, int, str]:
        async with self.database.sessions() as session:
            markets = list((await session.scalars(select(WhaleMarket))).all())
        for market in markets:
            tokens = [str(value) for value in _json_list(market.clob_token_ids_json)]
            if asset_id not in tokens:
                continue
            index = tokens.index(asset_id)
            outcomes = [str(value) for value in _json_list(market.outcomes_json)]
            outcome = outcomes[index] if index < len(outcomes) else f"Outcome {index + 1}"
            return market, index, outcome
        raise ValueError("巨鲸市场资料不存在或已经失效")

    @staticmethod
    def _ensure_market_open(market: WhaleMarket) -> None:
        if market.closed or not market.active or not market.accepting_orders:
            raise ValueError("市场当前已不再开放交易")
        if (
            market.end_date is not None
            and not market.end_date_is_date_only
            and market.end_date <= utcnow()
        ):
            raise ValueError("市场已经到达结束时间")

    async def quote_follow(
        self,
        *,
        asset_id: str,
        amount_usdc: Decimal,
        entry_id: int | None,
    ) -> WhaleFollowQuote:
        if not self.settings.live_copy_enabled:
            raise ValueError("自动实盘已被系统紧急停用")
        account = await self._account()
        async with self.database.sessions() as session:
            whale_settings = await session.get(WhaleSettings, 1)
            if whale_settings is None:
                raise ValueError("巨鲸模块尚未初始化")
            if amount_usdc <= ZERO:
                raise ValueError("跟单金额必须大于 0")
            if amount_usdc > whale_settings.max_follow_amount_usdc:
                raise ValueError(
                    f"单笔跟单金额不能超过 {whale_settings.max_follow_amount_usdc} USDC"
                )
            entry = await session.get(WhaleEntry, entry_id) if entry_id is not None else None
            if entry_id is not None and (entry is None or entry.asset_id != asset_id):
                raise ValueError("巨鲸投入记录与所选 outcome 不匹配")
            slippage = whale_settings.follow_slippage_cents
            warning_delta = whale_settings.max_price_delta_cents
            source_wallet = entry.proxy_wallet if entry is not None else None
            whale_avg_price = entry.avg_buy_price if entry is not None else None
        market, outcome_index, outcome = await self._market_for_asset(asset_id)
        self._ensure_market_open(market)
        if market_price_is_settled(market):
            raise ValueError("市场结果已经确定，不再接受跟单")
        book = await self.client.fetch_order_book(asset_id)
        if book.best_ask is None:
            raise ValueError("市场当前没有可成交卖盘")
        worst_price = market_worst_price(book.best_ask, book.tick_size, slippage, side="BUY")
        minimum_order_usdc = book.min_order_size * worst_price
        if amount_usdc < minimum_order_usdc:
            raise ValueError(f"跟单金额不能低于最小下单额 {minimum_order_usdc} USDC")
        shares = amount_usdc / worst_price
        fee = estimated_market_fee(shares, worst_price, market.fee_rate, market.fee_exponent)
        total_cost = amount_usdc + fee
        balance = await self._live_balance(account)
        if total_cost > balance:
            raise ValueError("执行钱包可用余额不足以支付买入金额与手续费")
        profit_ratio = (shares - total_cost) / total_cost * HUNDRED
        whale_profit = (
            settlement_profit_ratio(
                amount_usdc,
                whale_avg_price,
                market.fee_rate,
                market.fee_exponent,
            )
            if whale_avg_price is not None and whale_avg_price > ZERO
            else None
        )
        delta = (book.best_ask - whale_avg_price) * HUNDRED if whale_avg_price is not None else None
        return WhaleFollowQuote(
            entry_id=entry_id,
            source_wallet=source_wallet,
            asset_id=asset_id,
            condition_id=market.condition_id,
            title=market.title,
            outcome=outcome,
            outcome_index=outcome_index,
            neg_risk=market.neg_risk,
            market_slug=market.market_slug,
            event_slug=market.event_slug,
            icon_url=market.icon_url,
            amount_usdc=amount_usdc,
            best_ask=book.best_ask,
            worst_price=worst_price,
            tick_size=book.tick_size,
            minimum_order_usdc=minimum_order_usdc,
            estimated_shares=shares,
            estimated_fee_usdc=fee,
            total_cost_usdc=total_cost,
            profit_ratio_percent=profit_ratio,
            max_loss_usdc=total_cost,
            whale_avg_price=whale_avg_price,
            whale_profit_ratio_percent=whale_profit,
            profit_ratio_gap_percent=(
                whale_profit - profit_ratio if whale_profit is not None else None
            ),
            price_delta_cents=delta,
            price_delta_warning=delta is not None and delta > warning_delta,
            reserve_warning=balance - total_cost < account.cash_reserve_usdc,
            available_balance_usdc=balance,
        )

    async def execute_follow(self, quote: WhaleFollowQuote, confirmation_id: str) -> int:
        async with self._lock:
            if not self.settings.live_copy_enabled:
                raise ValueError("自动实盘已被系统紧急停用")
            book = await self.client.fetch_order_book(quote.asset_id)
            if book.best_ask is None or book.best_ask > quote.worst_price:
                raise ValueError("市场价格已变动，请重新预览")
            if book.tick_size != quote.tick_size:
                raise ValueError("市场价格步进已变化，请重新预览")
            account = await self._account()
            trader = await self._trader(account)
            order_id = await self._create_order(
                quote=quote,
                confirmation_id=confirmation_id,
                side="BUY",
            )
            request = MarketTradeRequest(
                asset_id=quote.asset_id,
                side="BUY",
                amount=quote.amount_usdc,
                worst_price=quote.worst_price,
                neg_risk=quote.neg_risk,
            )
            try:
                prepared = await trader.prepare_market(request)
            except TradingUnavailable as error:
                await self._mark_order(order_id, "blocked", str(error))
                raise
            async with self.database.sessions() as session:
                order = await session.get(WhaleOrder, order_id)
                assert order is not None
                order.signed_order_hash = prepared.signed_order_hash
                order.status = "signed"
                order.updated_at = utcnow()
                await session.commit()
            try:
                result = await trader.submit_prepared_market(prepared)
            except TradingUnavailable as error:
                await self._mark_order(order_id, "reconciliation_pending", str(error))
                raise TradingUnavailable(
                    "跟单提交结果待确认；系统不会自动重试，请到跟单记录核对"
                ) from error
            result = await self._hydrate_fee(trader, result)
            await self.apply_result(order_id, result)
            return order_id

    async def _create_order(
        self,
        *,
        quote: WhaleFollowQuote,
        confirmation_id: str,
        side: str,
    ) -> int:
        now = utcnow()
        async with self.database.sessions() as session:
            position = await session.scalar(
                select(WhaleFollowPosition)
                .where(
                    WhaleFollowPosition.asset_id == quote.asset_id,
                    WhaleFollowPosition.status.in_(["opening", "open"]),
                )
                .order_by(WhaleFollowPosition.cycle_no.desc())
                .limit(1)
            )
            if position is None:
                last_cycle = await session.scalar(
                    select(func.max(WhaleFollowPosition.cycle_no)).where(
                        WhaleFollowPosition.asset_id == quote.asset_id
                    )
                )
                position = WhaleFollowPosition(
                    asset_id=quote.asset_id,
                    condition_id=quote.condition_id,
                    title=quote.title,
                    outcome=quote.outcome,
                    outcome_index=quote.outcome_index,
                    neg_risk=quote.neg_risk,
                    market_slug=quote.market_slug,
                    event_slug=quote.event_slug,
                    icon_url=quote.icon_url,
                    source_wallet=quote.source_wallet,
                    source_whale_avg_price=quote.whale_avg_price,
                    cycle_no=int(last_cycle or 0) + 1,
                    size=ZERO,
                    cost_usdc=ZERO,
                    lifetime_bought_size=ZERO,
                    lifetime_bought_usdc=ZERO,
                    lifetime_sold_size=ZERO,
                    lifetime_sold_usdc=ZERO,
                    lifetime_fee_usdc=ZERO,
                    realized_pnl=ZERO,
                    status="opening",
                    opened_at=None,
                    closed_at=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(position)
                await session.flush()
            order = WhaleOrder(
                position_id=position.id,
                entry_id=quote.entry_id,
                idempotency_key=f"whale:{confirmation_id}",
                source_wallet=quote.source_wallet,
                asset_id=quote.asset_id,
                condition_id=quote.condition_id,
                title=quote.title,
                outcome=quote.outcome,
                outcome_index=quote.outcome_index,
                neg_risk=quote.neg_risk,
                side=side,
                requested_size=quote.estimated_shares,
                requested_usdc=quote.amount_usdc,
                limit_price=quote.worst_price,
                reference_price=quote.best_ask,
                whale_avg_price=quote.whale_avg_price,
                filled_size=ZERO,
                filled_usdc=ZERO,
                fee_usdc=ZERO,
                status="planned",
                reason=None,
                signed_order_hash=None,
                execution_provider="unified_sdk",
                external_order_id=None,
                external_trade_id=None,
                created_at=now,
                updated_at=now,
            )
            session.add(order)
            await session.commit()
            return order.id

    async def _mark_order(self, order_id: int, status: str, reason: str | None) -> None:
        async with self.database.sessions() as session:
            order = await session.get(WhaleOrder, order_id)
            if order is None:
                return
            order.status = status
            order.reason = (reason or "")[:1000] or None
            order.updated_at = utcnow()
            position = (
                await session.get(WhaleFollowPosition, order.position_id)
                if order.position_id is not None
                else None
            )
            if position is not None and position.size <= ZERO and status == "blocked":
                position.status = "closed"
                position.closed_at = utcnow()
                position.updated_at = utcnow()
            await session.commit()

    async def _hydrate_fee(
        self, trader: UnifiedPolymarketTrader, result: TradeResult
    ) -> TradeResult:
        if not result.external_trade_id or result.fee_usdc > ZERO:
            return result
        for attempt in range(3):
            fee = await trader.trade_fee(result.external_trade_id)
            if fee > ZERO:
                return replace(result, fee_usdc=fee)
            if attempt < 2:
                await asyncio.sleep(1)
        return result

    async def apply_result(self, order_id: int, result: TradeResult) -> None:
        """Persist only newly observed fills, making result reconciliation idempotent."""

        async with self.database.sessions() as session:
            order = await session.get(WhaleOrder, order_id)
            if order is None:
                raise ValueError("巨鲸订单不存在")
            request = MarketTradeRequest(
                asset_id=order.asset_id,
                side=order.side,
                amount=order.requested_usdc if order.side == "BUY" else order.requested_size,
                worst_price=order.limit_price,
                neg_risk=order.neg_risk,
            )
        result = normalize_fak_result(request, result)
        now = utcnow()
        raw_fills = list(result.fills)
        if not raw_fills and result.filled_size > ZERO:
            average = result.average_price or (
                result.filled_usdc / result.filled_size
                if result.filled_size > ZERO
                else order.limit_price
            )
            raw_fills = [
                TradeFillResult(
                    external_trade_id=result.external_trade_id or "",
                    size=result.filled_size,
                    price=average,
                    amount=result.filled_usdc,
                    fee_usdc=result.fee_usdc,
                    transaction_hash=None,
                    bucket_index=None,
                    settlement_status="confirmed",
                )
            ]

        async with self.database.sessions() as session:
            order = await session.get(WhaleOrder, order_id)
            assert order is not None
            position = (
                await session.get(WhaleFollowPosition, order.position_id)
                if order.position_id is not None
                else None
            )
            known = set(
                (
                    await session.scalars(
                        select(WhaleFill.fingerprint).where(WhaleFill.order_id == order_id)
                    )
                ).all()
            )
            new_fills: list[TradeFillResult] = []
            for fill in raw_fills:
                fingerprint = hashlib.sha256(
                    (
                        f"{order_id}|{fill.external_trade_id}|{fill.size}|"
                        f"{fill.price}|{fill.bucket_index}"
                    ).encode()
                ).hexdigest()
                if fingerprint in known:
                    continue
                session.add(
                    WhaleFill(
                        order_id=order_id,
                        fingerprint=fingerprint,
                        external_trade_id=fill.external_trade_id or None,
                        transaction_hash=fill.transaction_hash,
                        bucket_index=fill.bucket_index,
                        settlement_status=fill.settlement_status,
                        size=fill.size,
                        price=fill.price,
                        amount=fill.amount,
                        fee_usdc=fill.fee_usdc,
                        timestamp=now,
                    )
                )
                known.add(fingerprint)
                new_fills.append(fill)

            order.filled_size = result.filled_size
            order.filled_usdc = result.filled_usdc
            order.fee_usdc = result.fee_usdc
            order.status = result.status
            order.reason = result.reason
            order.external_order_id = result.external_order_id
            order.external_trade_id = result.external_trade_id
            if result.signed_order_hash:
                order.signed_order_hash = result.signed_order_hash
            order.updated_at = now

            new_size = sum((fill.size for fill in new_fills), ZERO)
            new_amount = sum((fill.amount for fill in new_fills), ZERO)
            new_fee = sum((fill.fee_usdc for fill in new_fills), ZERO)
            if new_fills and new_fee <= ZERO and len(new_fills) == len(raw_fills):
                new_fee = result.fee_usdc
            if position is not None and new_size > ZERO:
                if order.side == "BUY":
                    cost = new_amount + new_fee
                    position.size += new_size
                    position.cost_usdc += cost
                    position.lifetime_bought_size += new_size
                    position.lifetime_bought_usdc += new_amount
                    position.lifetime_fee_usdc += new_fee
                    position.status = "open"
                    position.opened_at = position.opened_at or now
                    position.closed_at = None
                    session.add(
                        WhaleFollowLedger(
                            position_id=position.id,
                            order_id=order.id,
                            type="buy",
                            size=new_size,
                            price=new_amount / new_size,
                            amount_usdc=cost,
                            fee_usdc=new_fee,
                            realized_pnl=ZERO,
                            transaction_hash=next(
                                (
                                    fill.transaction_hash
                                    for fill in new_fills
                                    if fill.transaction_hash
                                ),
                                None,
                            ),
                            detail="人工跟随巨鲸买入",
                            timestamp=now,
                        )
                    )
                else:
                    before_size = position.size
                    sold = min(before_size, new_size)
                    cost = position.cost_usdc * sold / before_size if before_size > ZERO else ZERO
                    gross_for_sold = new_amount * sold / new_size
                    fee_for_sold = new_fee * sold / new_size
                    proceeds = gross_for_sold - fee_for_sold
                    pnl = proceeds - cost
                    position.size = max(ZERO, before_size - sold)
                    position.cost_usdc = max(ZERO, position.cost_usdc - cost)
                    position.lifetime_sold_size += sold
                    position.lifetime_sold_usdc += gross_for_sold
                    position.lifetime_fee_usdc += fee_for_sold
                    position.realized_pnl += pnl
                    position.status = "closed" if position.size <= ZERO else "open"
                    if position.status == "closed":
                        position.closed_at = now
                    session.add(
                        WhaleFollowLedger(
                            position_id=position.id,
                            order_id=order.id,
                            type="sell",
                            size=sold,
                            price=gross_for_sold / sold if sold > ZERO else None,
                            amount_usdc=proceeds,
                            fee_usdc=fee_for_sold,
                            realized_pnl=pnl,
                            transaction_hash=next(
                                (
                                    fill.transaction_hash
                                    for fill in new_fills
                                    if fill.transaction_hash
                                ),
                                None,
                            ),
                            detail="人工卖出巨鲸跟单持仓",
                            timestamp=now,
                        )
                    )
                position.updated_at = now
            elif position is not None and order.side == "BUY" and position.size <= ZERO:
                position.status = "closed"
                position.closed_at = now
                position.updated_at = now
            await session.commit()

    async def quote_sell(
        self,
        *,
        position_id: int,
        size: Decimal | None,
        sell_all: bool,
    ) -> WhaleSellQuote:
        if not self.settings.live_copy_enabled:
            raise ValueError("自动实盘已被系统紧急停用")
        await self._account()
        async with self.database.sessions() as session:
            position = await session.get(WhaleFollowPosition, position_id)
            whale_settings = await session.get(WhaleSettings, 1)
            if position is None or position.status != "open" or position.size <= ZERO:
                raise ValueError("该巨鲸持仓当前不可卖出")
            if whale_settings is None:
                raise ValueError("巨鲸模块尚未初始化")
            selected_size = position.size if sell_all else _decimal(size)
            if selected_size <= ZERO or selected_size > position.size:
                raise ValueError("卖出份额必须大于 0 且不能超过当前持仓")
            slippage = whale_settings.sell_slippage_cents
            cost_basis = position.cost_usdc * selected_size / position.size
            snapshot = {
                "asset_id": position.asset_id,
                "condition_id": position.condition_id,
                "title": position.title,
                "outcome": position.outcome,
                "outcome_index": position.outcome_index,
                "neg_risk": bool(position.neg_risk),
            }
        market, _, _ = await self._market_for_asset(snapshot["asset_id"])
        self._ensure_market_open(market)
        book = await self.client.fetch_order_book(snapshot["asset_id"])
        if book.best_bid is None:
            raise ValueError("市场当前没有可成交买盘")
        if selected_size < book.min_order_size:
            raise ValueError(f"卖出份额不能低于市场最小下单量 {book.min_order_size}")
        worst_price = market_worst_price(book.best_bid, book.tick_size, slippage, side="SELL")
        gross = selected_size * worst_price
        fee = estimated_market_fee(selected_size, worst_price, market.fee_rate, market.fee_exponent)
        pnl = gross - fee - cost_basis
        return WhaleSellQuote(
            position_id=position_id,
            asset_id=snapshot["asset_id"],
            condition_id=snapshot["condition_id"],
            title=snapshot["title"],
            outcome=snapshot["outcome"],
            outcome_index=snapshot["outcome_index"],
            neg_risk=snapshot["neg_risk"],
            size=selected_size,
            best_bid=book.best_bid,
            worst_price=worst_price,
            tick_size=book.tick_size,
            minimum_order_size=book.min_order_size,
            estimated_proceeds_usdc=gross,
            estimated_fee_usdc=fee,
            cost_basis_usdc=cost_basis,
            estimated_pnl_usdc=pnl,
            estimated_pnl_percent=(pnl / cost_basis * HUNDRED if cost_basis > ZERO else ZERO),
        )

    async def execute_sell(self, quote: WhaleSellQuote, confirmation_id: str) -> int:
        async with self._lock:
            book = await self.client.fetch_order_book(quote.asset_id)
            if book.best_bid is None or book.best_bid < quote.worst_price:
                raise ValueError("市场价格已变动，请重新预览")
            if book.tick_size != quote.tick_size or book.min_order_size != quote.minimum_order_size:
                raise ValueError("市场下单规则已变化，请重新预览")
            async with self.database.sessions() as session:
                position = await session.get(WhaleFollowPosition, quote.position_id)
                if position is None or position.status != "open" or position.size < quote.size:
                    raise ValueError("持仓状态或份额已经变化，请重新预览")
                now = utcnow()
                order = WhaleOrder(
                    position_id=position.id,
                    entry_id=None,
                    idempotency_key=f"whale:{confirmation_id}",
                    source_wallet=position.source_wallet,
                    asset_id=position.asset_id,
                    condition_id=position.condition_id,
                    title=position.title,
                    outcome=position.outcome,
                    outcome_index=position.outcome_index,
                    neg_risk=bool(position.neg_risk),
                    side="SELL",
                    requested_size=quote.size,
                    requested_usdc=quote.estimated_proceeds_usdc,
                    limit_price=quote.worst_price,
                    reference_price=quote.best_bid,
                    whale_avg_price=position.source_whale_avg_price,
                    filled_size=ZERO,
                    filled_usdc=ZERO,
                    fee_usdc=ZERO,
                    status="planned",
                    reason=None,
                    signed_order_hash=None,
                    execution_provider="unified_sdk",
                    external_order_id=None,
                    external_trade_id=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(order)
                position.status = "closing"
                position.updated_at = now
                await session.commit()
                order_id = order.id
            request = MarketTradeRequest(
                asset_id=quote.asset_id,
                side="SELL",
                amount=quote.size,
                worst_price=quote.worst_price,
                neg_risk=quote.neg_risk,
            )
            trader = await self._trader()
            try:
                prepared = await trader.prepare_market(request)
            except TradingUnavailable as error:
                await self._restore_sell_position(order_id, "blocked", str(error))
                raise
            async with self.database.sessions() as session:
                order = await session.get(WhaleOrder, order_id)
                assert order is not None
                order.signed_order_hash = prepared.signed_order_hash
                order.status = "signed"
                order.updated_at = utcnow()
                await session.commit()
            try:
                result = await trader.submit_prepared_market(prepared)
            except TradingUnavailable as error:
                await self._restore_sell_position(
                    order_id, "reconciliation_pending", str(error), restore=False
                )
                raise TradingUnavailable(
                    "卖出提交结果待确认；系统不会自动重试，请到跟单记录核对"
                ) from error
            result = await self._hydrate_fee(trader, result)
            await self.apply_result(order_id, result)
            async with self.database.sessions() as session:
                position = await session.get(WhaleFollowPosition, quote.position_id)
                if position is not None and position.status == "closing":
                    position.status = "open" if position.size > ZERO else "closed"
                    position.updated_at = utcnow()
                    await session.commit()
            return order_id

    async def _restore_sell_position(
        self,
        order_id: int,
        status: str,
        reason: str,
        *,
        restore: bool = True,
    ) -> None:
        async with self.database.sessions() as session:
            order = await session.get(WhaleOrder, order_id)
            if order is None:
                return
            order.status = status
            order.reason = reason[:1000]
            order.updated_at = utcnow()
            position = (
                await session.get(WhaleFollowPosition, order.position_id)
                if order.position_id is not None
                else None
            )
            if restore and position is not None and position.size > ZERO:
                position.status = "open"
                position.updated_at = utcnow()
            await session.commit()

    async def process_redeemable_positions(self) -> None:
        """Safely redeem condition-wide balances only when every token is attributed.

        The SDK operation is condition-wide.  A separate SQL table does not isolate
        on-chain balances, so any copy-trading position, in-flight copy redemption,
        or manually held token turns the operation into manual review.
        """

        async with self.database.sessions() as session:
            whale_settings = await session.get(WhaleSettings, 1)
            account = await session.get(ExecutionAccount, 1)
            if (
                whale_settings is None
                or not whale_settings.auto_redeem
                or account is None
                or account.status not in {"ready", "insufficient_balance"}
                or not account.funder_address
                or account.signature_type not in {1, 3}
                or not account.keychain_service
                or not account.keychain_account
            ):
                return
            positions = list(
                (
                    await session.scalars(
                        select(WhaleFollowPosition).where(
                            WhaleFollowPosition.size > ZERO,
                            WhaleFollowPosition.status.in_(["open", "redeeming"]),
                        )
                    )
                ).all()
            )
            funder_address = account.funder_address
        if not positions:
            return
        condition_ids = list(dict.fromkeys(position.condition_id for position in positions))
        redeemable = await self.client.fetch_redeemable_positions(
            funder_address, condition_ids=condition_ids
        )
        redeemable_by_condition: dict[str, list[Any]] = defaultdict(list)
        for snapshot in redeemable:
            redeemable_by_condition[snapshot.condition_id].append(snapshot)
        trader = await self._trader(account)
        for condition_id, snapshots in redeemable_by_condition.items():
            condition_positions = [
                position for position in positions if position.condition_id == condition_id
            ]
            if not condition_positions:
                continue
            try:
                await self._process_condition_redemption(
                    trader,
                    account=account,
                    condition_id=condition_id,
                    positions=condition_positions,
                    redeemable=snapshots,
                )
            except Exception as error:
                await self._mark_redemption_review(
                    [position.id for position in condition_positions],
                    f"自动赎回检查失败：{error}",
                )

    async def _process_condition_redemption(
        self,
        trader: UnifiedPolymarketTrader,
        *,
        account: ExecutionAccount,
        condition_id: str,
        positions: list[WhaleFollowPosition],
        redeemable: list[Any],
    ) -> None:
        rates: dict[str, Decimal] = {
            item.asset_id: redeemable_position_payout_rate(item) for item in redeemable
        }
        for position in positions:
            if position.asset_id in rates and rates[position.asset_id] > ZERO:
                continue
            chain_rate = await trader.onchain_redemption_payout_rate(
                position.condition_id,
                position.outcome_index,
                bool(position.neg_risk),
            )
            if chain_rate is not None:
                rates[position.asset_id] = chain_rate

        attributed: dict[str, Decimal] = defaultdict(Decimal)
        for position in positions:
            attributed[position.asset_id] += position.size
        observed_assets = {item.asset_id for item in redeemable}
        async with self.database.sessions() as session:
            market = await session.get(WhaleMarket, condition_id)
        known_market_assets = (
            {str(asset_id) for asset_id in _json_list(market.clob_token_ids_json) if str(asset_id)}
            if market is not None
            else set()
        )
        all_assets = set(attributed) | observed_assets | known_market_assets
        balances = {
            asset_id: await trader.onchain_outcome_balance(asset_id) for asset_id in all_assets
        }

        if all(balance <= REDEMPTION_SIZE_TOLERANCE for balance in balances.values()):
            for position in positions:
                payout_rate = rates.get(position.asset_id)
                if payout_rate is not None:
                    await self._record_redeemed_position(
                        position.id,
                        payout_usdc=position.size * max(ZERO, min(ONE, payout_rate)),
                        transaction_hash=None,
                        detail="链上 outcome token 已归零后的外部赎回对账",
                    )
            return

        copy_collision = ZERO
        active_execution: CopyRedemptionExecution | None = None
        async with self.database.sessions() as session:
            copy_collision = _decimal(
                await session.scalar(
                    select(func.sum(CopyPosition.attributed_size)).where(
                        CopyPosition.condition_id == condition_id,
                        CopyPosition.attributed_size > ZERO,
                    )
                )
            )
            active_execution = await session.scalar(
                select(CopyRedemptionExecution)
                .where(
                    CopyRedemptionExecution.wallet_address == (account.funder_address or ""),
                    CopyRedemptionExecution.condition_id == condition_id,
                    CopyRedemptionExecution.status.in_(
                        ["pending", "submitting", "submitted", "manual_review", "completed"]
                    ),
                )
                .order_by(CopyRedemptionExecution.id.desc())
                .limit(1)
            )
        if copy_collision > ZERO or active_execution is not None:
            await self._mark_redemption_review(
                [position.id for position in positions],
                "同 condition 存在自动策略归因仓位或赎回执行，已阻止整市场赎回",
            )
            return

        unattributed = [
            asset_id
            for asset_id in all_assets - set(attributed)
            if balances.get(asset_id, ZERO) > REDEMPTION_SIZE_TOLERANCE
        ]
        mismatched = [
            asset_id
            for asset_id, size in attributed.items()
            if abs(balances.get(asset_id, ZERO) - size) > REDEMPTION_SIZE_TOLERANCE
        ]
        if unattributed or mismatched:
            await self._mark_redemption_review(
                [position.id for position in positions],
                "执行钱包含无法归因的同 condition outcome token，已阻止整市场赎回",
            )
            return

        unresolved = [position for position in positions if rates.get(position.asset_id) is None]
        if unresolved:
            return
        await self._prepare_redemptions(positions)
        try:
            prepared = await trader.start_redemption(
                condition_id=condition_id,
                neg_risk=any(bool(position.neg_risk) for position in positions),
            )
            transaction_hash = await trader.wait_redemption(prepared)
        except RedemptionSubmissionUnknown as error:
            await self._mark_redemption_review(
                [position.id for position in positions],
                str(error),
                transaction_hash=error.transaction_hash,
            )
            return
        except TradingUnavailable as error:
            await self._mark_redemption_review([position.id for position in positions], str(error))
            return
        for position in positions:
            rate = max(ZERO, min(ONE, rates.get(position.asset_id, ZERO)))
            await self._record_redeemed_position(
                position.id,
                payout_usdc=position.size * rate,
                transaction_hash=transaction_hash,
                detail="巨鲸跟单持仓结算后自动赎回",
            )

    async def _prepare_redemptions(self, positions: list[WhaleFollowPosition]) -> None:
        async with self.database.sessions() as session:
            for snapshot in positions:
                position = await session.get(WhaleFollowPosition, snapshot.id)
                if position is None or position.size <= ZERO:
                    continue
                redemption = await session.scalar(
                    select(WhaleRedemption).where(WhaleRedemption.position_id == position.id)
                )
                if redemption is None:
                    redemption = WhaleRedemption(
                        position_id=position.id,
                        status="pending",
                        size=position.size,
                        payout_usdc=None,
                        transaction_hash=None,
                        attempts=0,
                        last_error=None,
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                    session.add(redemption)
                redemption.status = "submitted"
                redemption.attempts += 1
                redemption.updated_at = utcnow()
                position.status = "redeeming"
                position.updated_at = utcnow()
            await session.commit()

    async def _mark_redemption_review(
        self,
        position_ids: list[int],
        reason: str,
        *,
        transaction_hash: str | None = None,
    ) -> None:
        async with self.database.sessions() as session:
            for position_id in position_ids:
                position = await session.get(WhaleFollowPosition, position_id)
                if position is None or position.size <= ZERO:
                    continue
                redemption = await session.scalar(
                    select(WhaleRedemption).where(WhaleRedemption.position_id == position_id)
                )
                if redemption is None:
                    redemption = WhaleRedemption(
                        position_id=position_id,
                        size=position.size,
                        payout_usdc=None,
                        attempts=0,
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                    session.add(redemption)
                redemption.status = "manual_review"
                redemption.last_error = reason[:2000]
                redemption.transaction_hash = transaction_hash
                redemption.updated_at = utcnow()
                position.status = "open"
                position.updated_at = utcnow()
            await session.commit()

    async def _record_redeemed_position(
        self,
        position_id: int,
        *,
        payout_usdc: Decimal,
        transaction_hash: str | None,
        detail: str,
    ) -> None:
        async with self.database.sessions() as session:
            position = await session.get(WhaleFollowPosition, position_id)
            if position is None or position.size <= ZERO:
                return
            redemption = await session.scalar(
                select(WhaleRedemption).where(WhaleRedemption.position_id == position_id)
            )
            if redemption is None:
                redemption = WhaleRedemption(
                    position_id=position.id,
                    size=position.size,
                    attempts=0,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
                session.add(redemption)
            cost = position.cost_usdc
            size = position.size
            pnl = payout_usdc - cost
            kind = "resolved_loss" if payout_usdc <= ZERO else "redeem"
            session.add(
                WhaleFollowLedger(
                    position_id=position.id,
                    order_id=None,
                    type=kind,
                    size=size,
                    price=None,
                    amount_usdc=payout_usdc,
                    fee_usdc=ZERO,
                    realized_pnl=pnl,
                    transaction_hash=transaction_hash,
                    detail=detail,
                    timestamp=utcnow(),
                )
            )
            position.realized_pnl += pnl
            position.size = ZERO
            position.cost_usdc = ZERO
            position.status = "resolved_loss" if payout_usdc <= ZERO else "redeemed"
            position.closed_at = utcnow()
            position.updated_at = utcnow()
            redemption.status = "completed"
            redemption.size = size
            redemption.payout_usdc = payout_usdc
            redemption.transaction_hash = transaction_hash
            redemption.last_error = None
            redemption.updated_at = utcnow()
            await session.commit()


def _market_url(market: WhaleMarket) -> str:
    if market.event_slug:
        return f"https://polymarket.com/event/{market.event_slug}"
    if market.market_slug:
        return f"https://polymarket.com/market/{market.market_slug}"
    return "https://polymarket.com"


def _wallet_age_days(created_at: datetime | None, *, now: datetime) -> int | None:
    if created_at is None:
        return None
    return max(0, int((now - created_at).total_seconds() // 86400))


async def whale_settings_read(database: Database) -> dict[str, Any]:
    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        if settings is None:
            raise ValueError("巨鲸模块尚未初始化")
        values = {
            column.name: getattr(settings, column.name)
            for column in WhaleSettings.__table__.columns
        }
        values.update(
            {
                "tracked_trade_count": int(
                    await session.scalar(select(func.count(WhaleTrade.id))) or 0
                ),
                "entry_count": int(await session.scalar(select(func.count(WhaleEntry.id))) or 0),
                "market_count": int(
                    await session.scalar(select(func.count(func.distinct(WhaleEntry.condition_id))))
                    or 0
                ),
            }
        )
        return values


async def list_whale_markets(
    database: Database,
    *,
    tag_slug: str | None = None,
    min_amount_usdc: Decimal | None = None,
    min_remaining_minutes: int | None = None,
    max_price_delta_cents: Decimal | None = None,
    include_exited: bool = False,
    include_hedged: bool = True,
    sort: str = "default",
    limit: int = 50,
    offset: int = 0,
    condition_id: str | None = None,
    include_trades: bool = False,
) -> dict[str, Any]:
    now = utcnow()
    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        if settings is None:
            raise ValueError("巨鲸模块尚未初始化")
        entry_query = select(WhaleEntry)
        if condition_id:
            entry_query = entry_query.where(WhaleEntry.condition_id == condition_id)
        entries = list((await session.scalars(entry_query)).all())
        if not include_exited:
            entries = [entry for entry in entries if entry.status != "exited"]
        if not include_hedged:
            entries = [entry for entry in entries if not entry.hedged]
        condition_ids = list(dict.fromkeys(entry.condition_id for entry in entries))
        markets = {
            market.condition_id: market
            for market in (
                await session.scalars(
                    select(WhaleMarket).where(WhaleMarket.condition_id.in_(condition_ids))
                )
            ).all()
        }
        wallet_addresses = list(dict.fromkeys(entry.proxy_wallet for entry in entries))
        wallets = {
            wallet.proxy_wallet: wallet
            for wallet in (
                await session.scalars(
                    select(WhaleWallet).where(WhaleWallet.proxy_wallet.in_(wallet_addresses))
                )
            ).all()
        }
        raw_trades = (
            list(
                (
                    await session.scalars(
                        select(WhaleTrade)
                        .where(
                            WhaleTrade.timestamp >= now - timedelta(hours=settings.window_hours),
                            WhaleTrade.condition_id.in_(condition_ids),
                        )
                        .order_by(WhaleTrade.timestamp.desc(), WhaleTrade.id.desc())
                    )
                ).all()
            )
            if include_trades
            else []
        )

    by_condition: dict[str, list[WhaleEntry]] = defaultdict(list)
    for entry in entries:
        by_condition[entry.condition_id].append(entry)
    trades_by_entry: dict[tuple[str, str], list[WhaleTrade]] = defaultdict(list)
    for trade in raw_trades:
        trades_by_entry[(trade.proxy_wallet, trade.asset_id)].append(trade)

    cards: list[dict[str, Any]] = []
    minimum_remaining = (
        settings.min_remaining_minutes if min_remaining_minutes is None else min_remaining_minutes
    )
    for linked_condition_id, market_entries in by_condition.items():
        market = markets.get(linked_condition_id)
        if market is None or market_price_is_settled(market):
            continue
        tags = [tag for tag in _json_list(market.tags_json) if isinstance(tag, dict)]
        if tag_slug and not any(str(tag.get("slug")) == tag_slug for tag in tags):
            continue
        remaining_seconds = (
            max(0, int((market.end_date - now).total_seconds()))
            if market.end_date is not None and not market.end_date_is_date_only
            else None
        )
        if remaining_seconds is not None and remaining_seconds < minimum_remaining * 60:
            continue
        outcomes = [str(value) for value in _json_list(market.outcomes_json)]
        tokens = [str(value) for value in _json_list(market.clob_token_ids_json)]
        prices = [_decimal(value) for value in _json_list(market.outcome_prices_json)]
        side_entries: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for entry in market_entries:
            current_price = (
                prices[entry.outcome_index] if 0 <= entry.outcome_index < len(prices) else ZERO
            )
            delta = (current_price - entry.avg_buy_price) * HUNDRED
            if max_price_delta_cents is not None and delta > max_price_delta_cents:
                continue
            profile = wallets.get(entry.proxy_wallet)
            detail_trades = trades_by_entry.get((entry.proxy_wallet, entry.asset_id), [])
            side_entries[entry.outcome_index].append(
                {
                    "entry_id": entry.id,
                    "proxy_wallet": entry.proxy_wallet,
                    "display_name": (
                        profile.display_name
                        if profile is not None and profile.display_name
                        else f"{entry.proxy_wallet[:6]}…{entry.proxy_wallet[-4:]}"
                    ),
                    "profile_url": f"https://polymarket.com/profile/{entry.proxy_wallet}",
                    "wallet_created_at": (
                        profile.profile_created_at if profile is not None else None
                    ),
                    "wallet_age_days": _wallet_age_days(
                        profile.profile_created_at if profile is not None else None,
                        now=now,
                    ),
                    "verified_badge": bool(profile and profile.verified_badge),
                    "taker_tier_name": profile.taker_tier_name if profile else None,
                    "gross_buy_usdc": entry.gross_buy_usdc,
                    "gross_buy_size": entry.gross_buy_size,
                    "avg_buy_price": entry.avg_buy_price,
                    "max_single_usdc": entry.max_single_usdc,
                    "trade_count": entry.trade_count,
                    "first_buy_at": entry.first_buy_at,
                    "last_buy_at": entry.last_buy_at,
                    "status": entry.status,
                    "net_ratio": entry.net_ratio,
                    "hedged": entry.hedged,
                    "price_delta_cents": delta,
                    "price_delta_percent": (
                        delta / entry.avg_buy_price if entry.avg_buy_price > ZERO else ZERO
                    ),
                    "trades": [
                        {
                            "id": trade.id,
                            "proxy_wallet": trade.proxy_wallet,
                            "asset_id": trade.asset_id,
                            "condition_id": trade.condition_id,
                            "side": trade.side,
                            "size": trade.size,
                            "price": trade.price,
                            "amount": trade.amount,
                            "outcome": trade.outcome,
                            "outcome_index": trade.outcome_index,
                            "title": trade.title,
                            "market_slug": trade.market_slug,
                            "event_slug": trade.event_slug,
                            "icon_url": trade.icon_url,
                            "display_name": trade.display_name,
                            "timestamp": trade.timestamp,
                            "transaction_hash": trade.transaction_hash,
                        }
                        for trade in detail_trades
                    ],
                }
            )
        if not side_entries:
            continue
        sides: list[dict[str, Any]] = []
        for outcome_index, linked_entries in sorted(side_entries.items()):
            linked_entries.sort(
                key=lambda item: (
                    bool(item["hedged"]),
                    item["status"] != "holding",
                    -_decimal(item["gross_buy_usdc"]),
                    -item["last_buy_at"].timestamp(),
                )
            )
            side_total = sum((_decimal(item["gross_buy_usdc"]) for item in linked_entries), ZERO)
            sides.append(
                {
                    "outcome_index": outcome_index,
                    "outcome": (
                        outcomes[outcome_index]
                        if 0 <= outcome_index < len(outcomes)
                        else linked_entries[0].get("outcome", f"Outcome {outcome_index + 1}")
                    ),
                    "asset_id": tokens[outcome_index] if outcome_index < len(tokens) else "",
                    "current_price": (
                        prices[outcome_index] if outcome_index < len(prices) else None
                    ),
                    # Gamma's market-level best bid/ask is not copied to every
                    # outcome.  Execution always reads the outcome-specific CLOB.
                    "best_bid": market.best_bid if outcome_index == 0 else None,
                    "best_ask": market.best_ask if outcome_index == 0 else None,
                    "side_total_usdc": side_total,
                    "side_wallet_count": len(
                        {str(item["proxy_wallet"]) for item in linked_entries}
                    ),
                    "entries": linked_entries,
                }
            )
        total = sum((_decimal(side["side_total_usdc"]) for side in sides), ZERO)
        if min_amount_usdc is not None and total < min_amount_usdc:
            continue
        dominant = max(sides, key=lambda side: _decimal(side["side_total_usdc"]))
        cards.append(
            {
                "condition_id": market.condition_id,
                "title": market.title,
                "icon_url": market.icon_url,
                "market_slug": market.market_slug,
                "event_slug": market.event_slug,
                "polymarket_url": _market_url(market),
                "tags": [
                    {
                        "id": str(tag.get("id") or ""),
                        "slug": str(tag.get("slug") or ""),
                        "label": str(tag.get("label") or tag.get("slug") or ""),
                        "market_count": 0,
                    }
                    for tag in tags
                ],
                "end_date": market.end_date,
                "remaining_seconds": remaining_seconds,
                "end_date_is_date_only": market.end_date_is_date_only,
                "liquidity": market.liquidity,
                "volume_24h": market.volume_24h,
                "total_whale_usdc": total,
                "whale_wallet_count": len({entry.proxy_wallet for entry in market_entries}),
                "both_sides": len(sides) >= 2,
                "dominant_outcome_index": int(dominant["outcome_index"]),
                "side_imbalance_ratio": (
                    _decimal(dominant["side_total_usdc"]) / total if total > ZERO else ZERO
                ),
                "sides": sides,
            }
        )

    if sort == "ending_soon":
        cards.sort(
            key=lambda item: (
                item["remaining_seconds"] is None,
                item["remaining_seconds"] or 10**20,
            )
        )
    elif sort == "max_entry":
        cards.sort(
            key=lambda item: (
                -max(
                    _decimal(entry["gross_buy_usdc"])
                    for side in item["sides"]
                    for entry in side["entries"]
                )
            )
        )
    elif sort == "least_delta":
        cards.sort(
            key=lambda item: min(
                _decimal(entry["price_delta_cents"])
                for side in item["sides"]
                for entry in side["entries"]
            )
        )
    else:
        cards.sort(
            key=lambda item: (
                all(bool(entry["hedged"]) for side in item["sides"] for entry in side["entries"]),
                -_decimal(item["total_whale_usdc"]),
            )
        )
    total_cards = len(cards)
    stale = settings.last_scan_at is None or now - settings.last_scan_at > timedelta(
        seconds=settings.scan_interval_seconds * 3
    )
    return {
        "generated_at": now,
        "window_start": now - timedelta(hours=settings.window_hours),
        "stale": stale,
        "total": total_cards,
        "items": cards[offset : offset + limit],
    }


def whale_order_payload(order: WhaleOrder) -> dict[str, Any]:
    fills = list(order.fills)
    filled_size = sum((fill.size for fill in fills), ZERO)
    filled_amount = sum((fill.amount for fill in fills), ZERO)
    return {
        "id": order.id,
        "position_id": order.position_id,
        "entry_id": order.entry_id,
        "source_wallet": order.source_wallet,
        "asset_id": order.asset_id,
        "condition_id": order.condition_id,
        "title": order.title,
        "outcome": order.outcome,
        "outcome_index": order.outcome_index,
        "neg_risk": order.neg_risk,
        "side": order.side,
        "requested_size": order.requested_size,
        "requested_usdc": order.requested_usdc,
        "limit_price": order.limit_price,
        "reference_price": order.reference_price,
        "whale_avg_price": order.whale_avg_price,
        "filled_size": order.filled_size,
        "filled_usdc": order.filled_usdc,
        "fee_usdc": order.fee_usdc,
        "average_fill_price": (
            filled_amount / filled_size
            if filled_size > ZERO
            else (order.filled_usdc / order.filled_size if order.filled_size > ZERO else None)
        ),
        "status": order.status,
        "reason": order.reason,
        "signed_order_hash": order.signed_order_hash,
        "execution_provider": order.execution_provider,
        "external_order_id": order.external_order_id,
        "external_trade_id": order.external_trade_id,
        "fills": fills,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
    }


async def _position_marks(
    client: PolymarketClient,
    positions: Iterable[WhaleFollowPosition],
) -> dict[int, tuple[Decimal | None, str]]:
    semaphore = asyncio.Semaphore(5)

    async def one(position: WhaleFollowPosition) -> tuple[int, Decimal | None, str]:
        if position.size <= ZERO or position.status in {
            "closed",
            "redeemed",
            "resolved_loss",
        }:
            return position.id, None, "not_applicable"
        try:
            async with semaphore:
                book = await client.fetch_order_book(position.asset_id)
        except Exception:
            return position.id, None, "unavailable"
        if book.best_bid is None:
            return position.id, None, "unavailable"
        return position.id, book.best_bid, "ok"

    rows = await asyncio.gather(*(one(position) for position in positions))
    return {position_id: (price, status) for position_id, price, status in rows}


def _position_payload(
    position: WhaleFollowPosition,
    *,
    current_price: Decimal | None,
    valuation_status: str,
) -> dict[str, Any]:
    market_value = position.size * current_price if current_price is not None else None
    unrealized = market_value - position.cost_usdc if market_value is not None else None
    return {
        "id": position.id,
        "asset_id": position.asset_id,
        "condition_id": position.condition_id,
        "title": position.title,
        "outcome": position.outcome,
        "outcome_index": position.outcome_index,
        "neg_risk": position.neg_risk,
        "market_slug": position.market_slug,
        "event_slug": position.event_slug,
        "icon_url": position.icon_url,
        "source_wallet": position.source_wallet,
        "source_whale_avg_price": position.source_whale_avg_price,
        "cycle_no": position.cycle_no,
        "size": position.size,
        "avg_cost_price": (position.cost_usdc / position.size if position.size > ZERO else None),
        "cost_usdc": position.cost_usdc,
        "current_price": current_price,
        "market_value_usdc": market_value,
        "unrealized_pnl": unrealized,
        "realized_pnl": position.realized_pnl,
        "total_pnl": (position.realized_pnl + unrealized if unrealized is not None else None),
        "lifetime_bought_size": position.lifetime_bought_size,
        "lifetime_bought_usdc": position.lifetime_bought_usdc,
        "lifetime_sold_size": position.lifetime_sold_size,
        "lifetime_sold_usdc": position.lifetime_sold_usdc,
        "lifetime_fee_usdc": position.lifetime_fee_usdc,
        "status": position.status,
        "valuation_status": valuation_status,
        "opened_at": position.opened_at,
        "closed_at": position.closed_at,
        "created_at": position.created_at,
        "updated_at": position.updated_at,
    }


async def list_whale_positions(
    database: Database,
    client: PolymarketClient,
    *,
    status: str = "all",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    async with database.sessions() as session:
        query = select(WhaleFollowPosition)
        if status == "open":
            query = query.where(
                WhaleFollowPosition.status.in_(["opening", "open", "closing", "redeeming"])
            )
        elif status == "closed":
            query = query.where(
                WhaleFollowPosition.status.in_(["closed", "redeemed", "resolved_loss"])
            )
        all_positions = list(
            (
                await session.scalars(
                    query.order_by(
                        WhaleFollowPosition.updated_at.desc(),
                        WhaleFollowPosition.id.desc(),
                    )
                )
            ).all()
        )
    marks = await _position_marks(client, all_positions[offset : offset + limit])
    return {
        "items": [
            _position_payload(
                position,
                current_price=marks.get(position.id, (None, "unavailable"))[0],
                valuation_status=marks.get(position.id, (None, "unavailable"))[1],
            )
            for position in all_positions[offset : offset + limit]
        ],
        "total": len(all_positions),
    }


async def whale_position_detail(
    database: Database,
    client: PolymarketClient,
    position_id: int,
) -> dict[str, Any] | None:
    async with database.sessions() as session:
        position = await session.get(WhaleFollowPosition, position_id)
        if position is None:
            return None
        orders = list(
            (
                await session.scalars(
                    select(WhaleOrder)
                    .where(WhaleOrder.position_id == position_id)
                    .order_by(WhaleOrder.created_at.asc(), WhaleOrder.id.asc())
                )
            ).all()
        )
        ledger = list(
            (
                await session.scalars(
                    select(WhaleFollowLedger)
                    .where(WhaleFollowLedger.position_id == position_id)
                    .order_by(WhaleFollowLedger.timestamp.asc(), WhaleFollowLedger.id.asc())
                )
            ).all()
        )
        redemption = await session.scalar(
            select(WhaleRedemption).where(WhaleRedemption.position_id == position_id)
        )
    marks = await _position_marks(client, [position])
    price, mark_status = marks[position.id]
    payload = _position_payload(position, current_price=price, valuation_status=mark_status)
    payload.update(
        {
            "orders": [whale_order_payload(order) for order in orders],
            "ledger": ledger,
            "redemption": redemption,
        }
    )
    return payload


async def list_whale_records(
    database: Database,
    client: PolymarketClient,
    *,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    async with database.sessions() as session:
        query = select(WhaleFollowLedger)
        if start_date is not None:
            query = query.where(WhaleFollowLedger.timestamp >= start_date)
        if end_date is not None:
            query = query.where(WhaleFollowLedger.timestamp <= end_date)
        ledger = list(
            (
                await session.scalars(
                    query.order_by(
                        WhaleFollowLedger.timestamp.desc(),
                        WhaleFollowLedger.id.desc(),
                    )
                )
            ).all()
        )
        positions = list((await session.scalars(select(WhaleFollowPosition))).all())
        positions_by_id = {position.id: position for position in positions}
        order_ids = list(dict.fromkeys(row.order_id for row in ledger if row.order_id is not None))
        orders_by_id = {
            order.id: order
            for order in (
                await session.scalars(select(WhaleOrder).where(WhaleOrder.id.in_(order_ids)))
            ).all()
        }
    selected = ledger[offset : offset + limit]
    items: list[dict[str, Any]] = []
    for row in selected:
        position = positions_by_id[row.position_id]
        order = orders_by_id.get(row.order_id) if row.order_id is not None else None
        items.append(
            {
                "id": row.id,
                "position_id": row.position_id,
                "order_id": row.order_id,
                "type": row.type,
                "size": row.size,
                "price": row.price,
                "amount_usdc": row.amount_usdc,
                "fee_usdc": row.fee_usdc,
                "realized_pnl": row.realized_pnl,
                "transaction_hash": row.transaction_hash,
                "detail": row.detail,
                "timestamp": row.timestamp,
                "title": position.title,
                "outcome": position.outcome,
                "asset_id": position.asset_id,
                "condition_id": position.condition_id,
                "market_slug": position.market_slug,
                "event_slug": position.event_slug,
                "source_wallet": position.source_wallet,
                "order_side": order.side if order is not None else None,
                "order_status": order.status if order is not None else None,
            }
        )

    marks = await _position_marks(client, positions)
    open_positions = [
        position
        for position in positions
        if position.size > ZERO and position.status in {"opening", "open", "closing", "redeeming"}
    ]
    unavailable = any(marks[position.id][1] == "unavailable" for position in open_positions)
    unrealized = sum(
        (
            position.size * marks[position.id][0] - position.cost_usdc
            for position in open_positions
            if marks[position.id][0] is not None
        ),
        ZERO,
    )
    finished = [
        position
        for position in positions
        if position.status in {"closed", "redeemed", "resolved_loss"}
    ]
    wins = sum(1 for position in finished if position.realized_pnl > ZERO)
    losses = sum(1 for position in finished if position.realized_pnl < ZERO)
    ratios = [
        position.realized_pnl / position.lifetime_bought_usdc * HUNDRED
        for position in finished
        if position.lifetime_bought_usdc > ZERO
    ]
    total_invested = sum((row.amount_usdc for row in ledger if row.type == "buy"), ZERO)
    total_proceeds = sum(
        (row.amount_usdc for row in ledger if row.type in {"sell", "redeem"}), ZERO
    )
    total_fees = sum((row.fee_usdc for row in ledger), ZERO)
    realized = sum((row.realized_pnl for row in ledger), ZERO)
    return {
        "items": items,
        "summary": {
            "total_invested_usdc": total_invested,
            "total_proceeds_usdc": total_proceeds,
            "total_fee_usdc": total_fees,
            "realized_pnl": realized,
            "unrealized_pnl": None if unavailable else unrealized,
            "total_pnl": None if unavailable else realized + unrealized,
            "open_position_count": len(open_positions),
            "closed_position_count": len(finished),
            "win_count": wins,
            "loss_count": losses,
            "win_rate_percent": (
                Decimal(wins) / Decimal(len(finished)) * HUNDRED if finished else None
            ),
            "average_profit_ratio_percent": (
                sum(ratios, ZERO) / Decimal(len(ratios)) if ratios else None
            ),
        },
        "total": len(ledger),
    }
