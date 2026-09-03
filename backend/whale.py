from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, DecimalException
from time import monotonic
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from backend.config import Settings
from backend.db import Database
from backend.keychain import KeychainReference, MacOSKeychain
from backend.models import (
    EmailSettings,
    ExecutionAccount,
    RedemptionExecution,
    WhaleAutoFollowDecision,
    WhaleAutoMarketLock,
    WhaleEntry,
    WhaleEntryRuleState,
    WhaleExclusion,
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
from backend.polymarket import (
    PolymarketAPIError,
    PolymarketClient,
    WhaleMarketPositionSnapshot,
    WhaleMarketSnapshot,
)
from backend.time_utils import utcnow
from backend.trading import (
    FAK_IGNORABLE_REMAINDER_USDC,
    REDEMPTION_SIZE_TOLERANCE,
    MarketTradeRequest,
    RedemptionSubmissionUnknown,
    TradeFillResult,
    TradeResult,
    TradingUnavailable,
    UnifiedPolymarketTrader,
    market_worst_price,
    normalize_fak_result,
    redeemable_position_payout_rate,
)
from backend.whale_email import (
    WhaleEmailCandidate,
    WhaleEmailNotifier,
    enqueue_whale_email_deliveries,
)
from backend.whale_requests import (
    WhaleRequestMonitor,
    capture_whale_requests,
    current_whale_request_capture,
)

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")
SETTLED_PRICE_THRESHOLD = Decimal("0.999")
AUTO_FOLLOW_PRICE_QUANTUM = Decimal("0.01")
MARKET_PRICE_QUANTUM = Decimal("0.0001")
PROFILE_MISSING_CACHE = timedelta(days=7)
TAG_CACHE = timedelta(hours=24)
MARKET_CACHE = timedelta(seconds=120)
ACTIVE_POSITION_CACHE_SECONDS = 300.0
WHALE_TRADE_WINDOW = timedelta(hours=24)
WHALE_MANUAL_ADOPTION_LOOKBACK = timedelta(hours=24)
WHALE_EXTERNAL_RECONCILIATION_DELAY = timedelta(seconds=15)
WHALE_HISTORY_REFRESH = timedelta(hours=1)
NEW_ACCOUNT_RULE = "new_account"
LARGE_AMOUNT_RULE = "large_amount"
WHALE_RULES = (NEW_ACCOUNT_RULE, LARGE_AMOUNT_RULE)
WHALE_RULE_PRIORITY = {
    NEW_ACCOUNT_RULE: 1,
    LARGE_AMOUNT_RULE: 2,
}
WHALE_STATISTICS_RANGES = {
    "all": None,
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}
WHALE_STATISTICS_AMOUNT_BANDS = (
    ("lt_100k", "< 10万", ZERO, Decimal("100000")),
    ("100k_500k", "10万–50万", Decimal("100000"), Decimal("500000")),
    ("500k_1m", "50万–100万", Decimal("500000"), Decimal("1000000")),
    ("gte_1m", "≥ 100万", Decimal("1000000"), None),
)
WHALE_STATISTICS_CATEGORIES = (
    ("esports", "电竞"),
    ("sports", "传统体育"),
    ("politics", "政治"),
    ("crypto", "加密"),
    ("science_tech", "科学与科技"),
    ("entertainment", "娱乐"),
    ("other", "其他"),
)
WHALE_STATISTICS_CATEGORY_LABELS = dict(WHALE_STATISTICS_CATEGORIES)
AUTO_FOLLOW_RESERVED_ORDER_STATUSES = {
    "planned",
    "signed",
    "submitted",
    "reconciliation_pending",
    "live",
    "matched",
}


def _strongest_whale_rule(rules: Iterable[str]) -> str | None:
    strongest: str | None = None
    strongest_priority = 0
    for rule in rules:
        priority = WHALE_RULE_PRIORITY.get(rule, 0)
        if priority > strongest_priority:
            strongest = rule
            strongest_priority = priority
    return strongest


def _decision_requires_conflict_exit(
    decision: WhaleAutoFollowDecision,
    market_lock: WhaleAutoMarketLock | None,
) -> bool:
    if market_lock is None:
        return False
    if decision.status in {"pending", "exit_pending"}:
        return True
    return bool(
        market_lock.trigger_asset_id is None or decision.asset_id != market_lock.trigger_asset_id
    )


@dataclass(frozen=True, slots=True)
class AutoConflictResolution:
    blocked_asset_ids: frozenset[str]
    exit_asset_ids: frozenset[str]
    trigger_entry: WhaleEntry | None
    lock_required: bool


WHALE_STATISTICS_SCIENCE_TECH_TAGS = frozenset(
    {
        "science",
        "spacex",
        "space-exploration",
        "software-updates",
        "climate",
        "weather",
    }
)
WHALE_STATISTICS_ENTERTAINMENT_TAGS = frozenset(
    {
        "movies",
        "music",
        "awards",
        "box-office",
        "celebrity-events",
        "new-releases",
        "gaming",
        "video-games",
    }
)
WHALE_STATISTICS_SUBCATEGORIES = {
    "esports": (
        ("dota-2", "Dota 2", frozenset({"dota-2"})),
        ("counter-strike-2", "CS2", frozenset({"counter-strike-2"})),
        ("league-of-legends", "英雄联盟", frozenset({"league-of-legends"})),
        ("other-esports", "其他电竞", frozenset()),
    ),
    "sports": (
        ("soccer", "足球", frozenset({"soccer"})),
        ("tennis", "网球", frozenset({"tennis"})),
        ("basketball", "篮球", frozenset({"basketball"})),
        ("baseball", "棒球", frozenset({"baseball"})),
        ("football", "美式橄榄球", frozenset({"football"})),
        ("hockey", "冰球", frozenset({"hockey"})),
        ("mma", "综合格斗", frozenset({"mma"})),
        ("boxing", "拳击", frozenset({"boxing"})),
        ("cricket", "板球", frozenset({"cricket"})),
        ("golf", "高尔夫", frozenset({"golf"})),
        ("motorsports", "赛车", frozenset({"formula-1", "f1"})),
        ("other-sports", "其他体育", frozenset()),
    ),
}
BEIJING_TIMEZONE = ZoneInfo("Asia/Shanghai")
LOGGER = logging.getLogger(__name__)
AUTO_FOLLOW_PRICE_REASON_PATTERN = re.compile(r"(实际买价|策略最低价|策略最高价) (-?\d+(?:\.\d+)?)")


class AutoFollowQuoteRejected(ValueError):
    """Auto-follow quote rejection that preserves the observed live ask."""

    def __init__(self, message: str, *, observed_best_ask: Decimal | None = None) -> None:
        super().__init__(message)
        self.observed_best_ask = observed_best_ask


def _not_excluded_wallet(column: Any) -> Any:
    return func.lower(column).not_in(select(WhaleExclusion.proxy_wallet))


def _decimal(value: Any, default: Decimal = ZERO) -> Decimal:
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except (TypeError, ValueError):
        return default


def _decimal_display(value: Decimal) -> str:
    """Render database decimals without scientific notation or insignificant zeroes."""
    if value == ZERO:
        return "0"
    return format(value.normalize(), "f")


def _auto_follow_price(value: Any) -> Decimal:
    """Normalize strategy prices after SQLite NUMERIC values are read back as floats."""
    return _decimal(value).quantize(AUTO_FOLLOW_PRICE_QUANTUM)


def _select_auto_follow_amount(
    *,
    base_amount: Decimal,
    best_ask: Decimal,
    low_price_max_price: Decimal | None,
    low_price_amount: Decimal | None,
) -> tuple[Decimal, bool]:
    if low_price_max_price is None and low_price_amount is None:
        return base_amount, False
    if low_price_max_price is None or low_price_amount is None:
        raise ValueError("低价分界与低价金额配置不完整")
    if low_price_amount <= ZERO or low_price_amount >= base_amount:
        raise ValueError("低价金额必须大于 0 且小于基础单笔金额")
    if best_ask < low_price_max_price:
        return low_price_amount, True
    return base_amount, False


def _auto_follow_price_band_changed(
    *, best_ask: Decimal, low_price_max_price: Decimal | None, selected_low: bool
) -> bool:
    if low_price_max_price is None:
        return False
    return (selected_low and best_ask >= low_price_max_price) or (
        not selected_low and best_ask < low_price_max_price
    )


def _auto_follow_market_usage(orders: Iterable[WhaleOrder]) -> tuple[int, Decimal]:
    used_count = 0
    used_amount = ZERO
    for order in orders:
        if order.filled_usdc > ZERO:
            used_count += 1
            used_amount += order.filled_usdc
        elif order.status in AUTO_FOLLOW_RESERVED_ORDER_STATUSES:
            used_count += 1
            used_amount += order.requested_usdc
    return used_count, used_amount


def _ensure_auto_follow_market_capacity(
    *,
    used_count: int,
    used_amount: Decimal,
    requested_amount: Decimal,
    count_cap: int,
    amount_cap: Decimal,
) -> None:
    if used_count + 1 > count_cap:
        raise ValueError(f"同一市场自动跟单最多购买 {count_cap} 次，已经停止继续买入")
    if used_amount + requested_amount > amount_cap:
        raise ValueError(
            "同一市场自动跟单累计金额不能超过 "
            f"{_decimal_display(amount_cap)} USDC，已经停止继续买入"
        )


def _market_price(value: Any) -> Decimal:
    """Normalize displayed market prices while preserving supported sub-cent ticks."""
    return _decimal(value).quantize(MARKET_PRICE_QUANTUM)


def _auto_follow_reason_display(reason: str | None) -> str | None:
    """Normalize prices embedded in both new and already-persisted decision reasons."""
    if not reason:
        return reason

    def replace_price(match: re.Match[str]) -> str:
        normalizer = _market_price if match.group(1) == "实际买价" else _auto_follow_price
        return f"{match.group(1)} {_decimal_display(normalizer(match.group(2)))}"

    return AUTO_FOLLOW_PRICE_REASON_PATTERN.sub(replace_price, reason)


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


def _apply_market_snapshot(row: WhaleMarket, item: Any, *, now: datetime) -> None:
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
    row.end_date_is_date_only = bool(_attribute(item, "end_date_is_date_only", default=False))
    row.liquidity = _decimal(_attribute(item, "liquidity"))
    row.volume_24h = _decimal(_attribute(item, "volume_24h"))
    row.best_bid = _attribute(item, "best_bid")
    row.best_ask = _attribute(item, "best_ask")
    row.order_min_size = _decimal(_attribute(item, "order_min_size"), Decimal("5"))
    row.tick_size = _decimal(_attribute(item, "tick_size"), Decimal("0.01"))
    row.fee_rate = _decimal(_attribute(item, "fee_rate"))
    row.fee_exponent = _decimal(_attribute(item, "fee_exponent"), ONE)
    row.refreshed_at = now


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
        timestamp = _attribute(trade, "timestamp")
        transaction_hash = str(_attribute(trade, "transaction_hash", default="") or "")
        transaction_key = transaction_hash or f"timestamp:{timestamp}"
        base = "|".join(
            [
                str(_attribute(trade, "proxy_wallet", default="") or "").lower(),
                transaction_key,
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
    source: str = "trades"


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


def aggregate_whale_positions(
    positions: Iterable[WhaleMarketPositionSnapshot],
    *,
    position_threshold_usdc: Decimal,
    observed_at: datetime,
) -> list[WhaleAggregate]:
    """Convert official current-position snapshots into the existing entry shape."""

    aggregates: list[WhaleAggregate] = []
    for position in positions:
        # Data API can expose large legacy/redeemable token balances with a
        # synthetic avgPrice even though the address never bought the outcome.
        # They have totalBought=0 and no trader profile/activity, so treating
        # size*avgPrice as invested capital creates enormous ghost whales.
        if position.total_bought <= ZERO:
            continue
        remaining_cost = position.size * position.avg_price
        if remaining_cost < position_threshold_usdc:
            continue
        aggregates.append(
            WhaleAggregate(
                proxy_wallet=position.proxy_wallet,
                asset_id=position.asset_id,
                condition_id=position.condition_id,
                outcome=position.outcome,
                outcome_index=position.outcome_index,
                # These fields now describe the current open position. Historical
                # fills remain available separately when the trade feed has them.
                gross_buy_usdc=(
                    remaining_cost if remaining_cost > ZERO else position.current_value
                ),
                gross_buy_size=position.size,
                sold_size=ZERO,
                sold_usdc=ZERO,
                net_size=position.size,
                net_ratio_percent=HUNDRED,
                avg_buy_price=position.avg_price,
                max_single_usdc=(
                    remaining_cost if remaining_cost > ZERO else position.current_value
                ),
                trade_count=0,
                first_buy_at=observed_at,
                last_buy_at=observed_at,
                status="holding",
                source="positions",
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


def market_price_is_settled(market: Any) -> bool:
    """任一结果报价已经贴到 1 说明胜负已分。

    赛果已定的市场在接口上往往仍是 `closed=false`，只是报价停在 0.9995 这类
    「买一 0.999 / 卖一 1.000」的中间价上。剩余空间已经小于最小报价单位，跟进
    只会亏手续费，所以按 `SETTLED_PRICE_THRESHOLD` 而不是严格等于 1 来判定。
    """

    prices = [
        _decimal(value)
        for value in _json_list(
            _attribute(market, "outcome_prices_json", "outcome_prices", default=[])
        )
    ]
    return any(price >= SETTLED_PRICE_THRESHOLD for price in prices)


def _market_outcome_index(
    market: Any,
    *,
    asset_id: str,
    fallback: int | None,
) -> int | None:
    """Resolve an outcome from the market token mapping before trusting feed metadata.

    Polymarket's public trade feed sometimes returns ``outcomeIndex=999`` even
    though the asset token still identifies the outcome unambiguously.  The
    market's token ordering is the canonical mapping used by settlement prices.
    """

    token_ids = [
        str(value)
        for value in _json_list(
            _attribute(market, "clob_token_ids_json", "clob_token_ids", default=[])
        )
    ]
    normalized_asset_id = str(asset_id or "")
    if normalized_asset_id:
        try:
            return token_ids.index(normalized_asset_id)
        except ValueError:
            pass

    outcome_count = len(_json_list(_attribute(market, "outcomes_json", "outcomes", default=[])))
    if fallback is not None and 0 <= fallback < outcome_count:
        return fallback
    return None


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
    # Gamma uses endDate as gameStartTime for sports markets.  Treating it as
    # the market close time incorrectly blocks otherwise-open pre-match books.
    # Keep these arguments for API compatibility; actual tradability is
    # determined by active/closed/acceptingOrders and the settled-price guard.
    _ = now, min_remaining_minutes
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
        request_monitor: WhaleRequestMonitor | None = None,
        email_notifier: WhaleEmailNotifier | None = None,
    ) -> None:
        self.database = database
        self.client = client
        self.settings = settings
        self.executor = executor
        self.request_monitor = request_monitor
        self.email_notifier = email_notifier
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()
        self._last_trade_cursor_at: datetime | None = None
        self._position_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._last_history_refresh_at: datetime | None = None
        self._running_config_at: datetime | None = None
        self._auto_pending_recovered = False

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

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
            cycle_started = monotonic()
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
            delay = max(0.05, delay - (monotonic() - cycle_started))
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
                scan_started = monotonic()
                pending_order_warning: str | None = None
                if not self._auto_pending_recovered:
                    await self._recover_interrupted_auto_decisions()
                    self._auto_pending_recovered = True
                if self.executor is not None:
                    await self.executor.reconcile_terminal_sell_remainders()
                    pending_order_warning = await self.executor.reconcile_pending_orders()
                with capture_whale_requests(self.request_monitor, uuid4().hex):
                    scan_warning = await self._scan(values)
                    warning = pending_order_warning or scan_warning
                if self.executor is not None:
                    try:
                        reconciliation_warning = (
                            await self.executor.reconcile_external_wallet_activity()
                        )
                        warning = warning or reconciliation_warning
                        await self.executor.process_redeemable_positions()
                    except Exception as error:
                        warning = warning or f"执行钱包持仓对账失败：{error}"
                async with self.database.sessions() as session:
                    row = await session.get(WhaleSettings, 1)
                    if row is not None:
                        row.last_scan_at = utcnow()
                        row.last_scan_error = warning
                        row.consecutive_failures = 0
                        row.updated_at = utcnow()
                        await session.commit()
                LOGGER.debug(
                    "Whale scan completed duration_ms=%s warning=%s",
                    round((monotonic() - scan_started) * 1000),
                    warning,
                )
                if self.email_notifier is not None:
                    self.email_notifier.wake()
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

    async def _recover_interrupted_auto_decisions(self) -> None:
        """Finalize decisions left pending by a prior process without replaying the signal."""

        now = utcnow()
        async with self.database.sessions() as session:
            decisions = list(
                (
                    await session.scalars(
                        select(WhaleAutoFollowDecision).where(
                            WhaleAutoFollowDecision.status == "pending"
                        )
                    )
                ).all()
            )
            for decision in decisions:
                order = await session.scalar(
                    select(WhaleOrder).where(
                        WhaleOrder.idempotency_key == f"whale:auto:{decision.id}"
                    )
                )
                if order is not None:
                    decision.buy_order_id = order.id
                if order is not None and order.filled_size > ZERO:
                    market_lock = await session.get(WhaleAutoMarketLock, decision.condition_id)
                    requires_exit = _decision_requires_conflict_exit(decision, market_lock)
                    decision.status = "exit_pending" if requires_exit else "bought"
                    decision.reason = (
                        "恢复中断决策：买入已成交，等待分歧风控退出"
                        if requires_exit
                        else "恢复中断决策：自动买入已经成交"
                    )
                    if requires_exit and market_lock is not None:
                        market_lock.exit_status = "pending"
                        market_lock.updated_at = now
                else:
                    decision.status = "failed"
                    decision.reason = (
                        f"服务重启前自动买入未完成，订单状态：{order.status}"
                        if order is not None
                        else "服务重启前尚未提交自动买入，不补买"
                    )
                decision.processed_at = now
                decision.updated_at = now
            if decisions:
                await session.commit()

    async def _scan(self, config: dict[str, Any]) -> str | None:
        now = utcnow()
        window_start = now - timedelta(hours=max(1, int(config["window_hours"])))
        incremental_start = window_start
        if self._last_trade_cursor_at is None:
            async with self.database.sessions() as session:
                settings_row = await session.get(WhaleSettings, 1)
                self._last_trade_cursor_at = (
                    settings_row.last_trade_cursor_at if settings_row is not None else None
                )
                if self._last_trade_cursor_at is None:
                    self._last_trade_cursor_at = await session.scalar(
                        select(func.max(WhaleTrade.timestamp))
                    )
        if self._last_trade_cursor_at is not None:
            incremental_start = min(
                now,
                max(
                    window_start,
                    self._last_trade_cursor_at - timedelta(seconds=120),
                ),
            )
        trades, hit_page_limit = await self._collect_trades(
            start=incremental_start,
            end=now,
            amount=_decimal(config["collect_filter_amount_usdc"]),
        )
        await self._persist_trades(trades)
        if not hit_page_limit:
            async with self.database.sessions() as session:
                settings_row = await session.get(WhaleSettings, 1)
                if settings_row is not None:
                    settings_row.last_trade_cursor_at = now
                    await session.commit()
            self._last_trade_cursor_at = now

        async with self.database.sessions() as session:
            window_trades = list(
                (
                    await session.scalars(
                        select(WhaleTrade)
                        .where(
                            WhaleTrade.timestamp >= window_start,
                            WhaleTrade.side == "BUY",
                            _not_excluded_wallet(WhaleTrade.proxy_wallet),
                        )
                        .order_by(WhaleTrade.timestamp.asc(), WhaleTrade.id.asc())
                    )
                ).all()
            )
        new_threshold = _decimal(config["new_account_threshold_usdc"])
        large_threshold = _decimal(config["large_amount_threshold_usdc"])
        collection_threshold = min(new_threshold, large_threshold)
        aggregates = aggregate_whale_trades(
            window_trades,
            single_trade_threshold_usdc=collection_threshold,
            cumulative_threshold_usdc=collection_threshold,
            holding_ratio_threshold=_decimal(config["holding_ratio_threshold"]),
            exited_ratio_threshold=_decimal(config["exited_ratio_threshold"]),
        )
        unresolved_conditions: list[str] = []
        if (
            self._last_history_refresh_at is None
            or self._last_history_refresh_at <= now - WHALE_HISTORY_REFRESH
        ):
            async with self.database.sessions() as session:
                unresolved_conditions = list(
                    (
                        await session.scalars(
                            select(WhaleEntry.condition_id)
                            .where(
                                WhaleEntry.settlement_price.is_(None),
                                _not_excluded_wallet(WhaleEntry.proxy_wallet),
                            )
                            .distinct()
                        )
                    ).all()
                )
            self._last_history_refresh_at = now
        await self._refresh_markets(
            list(
                dict.fromkeys(
                    [item.condition_id for item in aggregates if item.condition_id]
                    + unresolved_conditions
                )
            ),
            now=now,
        )
        await self._refresh_wallets(
            list(dict.fromkeys(item.proxy_wallet for item in aggregates)),
            now=now,
            cache_hours=int(config["profile_cache_hours"]),
        )
        registration_days = int(config["registration_window_days"])
        registration_cutoff = now - timedelta(days=registration_days)
        async with self.database.sessions() as session:
            profiles = {
                row.proxy_wallet: row
                for row in (
                    await session.scalars(
                        select(WhaleWallet).where(
                            WhaleWallet.proxy_wallet.in_(
                                list(dict.fromkeys(item.proxy_wallet for item in aggregates))
                            )
                        )
                    )
                ).all()
            }
        rule_matches: dict[tuple[str, str], set[str]] = {}
        qualified: list[WhaleAggregate] = []
        for aggregate in aggregates:
            matches: set[str] = set()
            profile = profiles.get(aggregate.proxy_wallet)
            if (
                aggregate.gross_buy_usdc >= new_threshold
                and profile is not None
                and profile.profile_created_at is not None
                and registration_cutoff <= profile.profile_created_at <= now
            ):
                matches.add(NEW_ACCOUNT_RULE)
            if aggregate.gross_buy_usdc >= large_threshold:
                matches.add(LARGE_AMOUNT_RULE)
            if matches:
                key = (aggregate.proxy_wallet, aggregate.asset_id)
                rule_matches[key] = matches
                qualified.append(aggregate)
        async with self.database.sessions() as session:
            active_rows = list(
                (
                    await session.execute(
                        select(WhaleEntry.proxy_wallet, WhaleEntry.condition_id)
                        .join(
                            WhaleEntryRuleState,
                            WhaleEntryRuleState.entry_id == WhaleEntry.id,
                        )
                        .where(WhaleEntryRuleState.active.is_(True))
                        .where(_not_excluded_wallet(WhaleEntry.proxy_wallet))
                        .distinct()
                    )
                ).all()
            )
        active_targets: dict[str, set[str]] = defaultdict(set)
        for wallet, condition_id in active_rows:
            if condition_id:
                active_targets[wallet].add(condition_id)
        positions_by_wallet, failed_wallets = await self._fetch_current_positions(
            qualified,
            additional_targets=active_targets,
        )
        auto_decision_ids = await self._persist_entries(
            qualified,
            rule_matches=rule_matches,
            positions_by_wallet=positions_by_wallet,
            failed_wallets=failed_wallets,
            config=config,
            now=now,
            window_start=window_start,
        )
        if self.executor is not None:
            await self._process_auto_decisions(auto_decision_ids)
            await self._process_conflict_exits()
        await self._update_entry_settlements(now=now)
        async with self.database.sessions() as session:
            await session.execute(
                delete(WhaleTrade).where(
                    WhaleTrade.timestamp
                    < now - timedelta(hours=int(config["trade_retention_hours"]))
                )
            )
            await session.commit()
        warnings: list[str] = []
        if hit_page_limit:
            warnings.append("成交回溯达到官方分页上限，冷启动的24小时窗口可能尚未完整")
        if failed_wallets:
            warnings.append(f"{len(failed_wallets)} 个候选钱包的当前持仓核验失败，已保留上次状态")
        return "；".join(warnings) or None

    async def _update_entry_settlements(self, *, now: datetime) -> None:
        async with self.database.sessions() as session:
            entries = list(
                (
                    await session.scalars(
                        select(WhaleEntry).where(
                            WhaleEntry.settlement_price.is_(None),
                            _not_excluded_wallet(WhaleEntry.proxy_wallet),
                        )
                    )
                ).all()
            )
            if not entries:
                return
            market_ids = list(dict.fromkeys(row.condition_id for row in entries))
            markets = {
                row.condition_id: row
                for row in (
                    await session.scalars(
                        select(WhaleMarket).where(WhaleMarket.condition_id.in_(market_ids))
                    )
                ).all()
            }
            entry_ids: list[int] = []
            for row in entries:
                market = markets.get(row.condition_id)
                if market is None or not market_price_is_settled(market):
                    continue
                prices = [_decimal(value) for value in _json_list(market.outcome_prices_json)]
                outcome_index = _market_outcome_index(
                    market,
                    asset_id=row.asset_id,
                    fallback=row.outcome_index,
                )
                if outcome_index is None or not (0 <= outcome_index < len(prices)):
                    continue
                row.outcome_index = outcome_index
                row.settlement_price = prices[outcome_index]
                row.settled_at = now
                row.status = "exited"
                row.follow_eligible = False
                row.follow_ineligible_reason = "market_closed"
                entry_ids.append(row.id)
            if entry_ids:
                states = list(
                    (
                        await session.scalars(
                            select(WhaleEntryRuleState).where(
                                WhaleEntryRuleState.entry_id.in_(entry_ids),
                                WhaleEntryRuleState.active.is_(True),
                            )
                        )
                    ).all()
                )
                for state in states:
                    state.active = False
                    state.inactive_at = now
                    state.inactive_reason = "market_closed"
            await session.commit()

    async def _fetch_current_positions(
        self,
        aggregates: list[WhaleAggregate],
        *,
        additional_targets: dict[str, set[str]] | None = None,
    ) -> tuple[dict[str, dict[str, Any]], set[str]]:
        """Fetch only relevant positions without turning provider failures into exits."""

        candidate_targets: dict[str, set[str]] = defaultdict(set)
        for item in aggregates:
            if item.condition_id:
                candidate_targets[item.proxy_wallet].add(item.condition_id)
        targets: dict[str, set[str]] = defaultdict(set)
        for wallet, condition_ids in (additional_targets or {}).items():
            targets[wallet].update(condition_ids)
        for wallet, condition_ids in candidate_targets.items():
            targets[wallet].update(condition_ids)
        wallets = list(targets)
        positions_by_wallet: dict[str, dict[str, Any]] = {}
        failed_wallets: set[str] = set()
        for offset in range(0, len(wallets), 5):
            batch = wallets[offset : offset + 5]
            requested_by_wallet: dict[str, list[str]] = {}
            cached_by_wallet: dict[str, dict[str, Any]] = {}
            cache_now = monotonic()
            for wallet in batch:
                cached_positions: dict[str, Any] = {}
                requested: list[str] = []
                force_fresh = wallet in candidate_targets
                for condition_id in sorted(targets[wallet]):
                    cache_key = (wallet.lower(), condition_id)
                    cached = self._position_cache.get(cache_key)
                    if not force_fresh and cached is not None and cached[0] > cache_now:
                        cached_positions.update(cached[1])
                    else:
                        if cached is not None and cached[0] <= cache_now:
                            self._position_cache.pop(cache_key, None)
                        requested.append(condition_id)
                cached_by_wallet[wallet] = cached_positions
                requested_by_wallet[wallet] = requested

            async def fetch_wallet(wallet: str, requested: list[str]) -> list[Any]:
                if not requested:
                    return []
                return await self._retry(
                    self.client.fetch_active_positions,
                    user=wallet,
                    condition_ids=requested,
                )

            results = await asyncio.gather(
                *(fetch_wallet(wallet, requested_by_wallet[wallet]) for wallet in batch),
                return_exceptions=True,
            )
            for wallet, result in zip(batch, results, strict=True):
                if isinstance(result, Exception):
                    failed_wallets.add(wallet)
                    capture = current_whale_request_capture()
                    if capture is not None:
                        await capture.monitor.log_failure(
                            "position_verification_failed",
                            scan_id=capture.scan_id,
                            wallet=wallet,
                            condition_ids=requested_by_wallet[wallet],
                            error_type=type(result).__name__,
                            error_message=(str(result).strip() or type(result).__name__)[:2000],
                            failure_stage="pre_order_position_verification",
                            auto_follow_blocked=wallet in candidate_targets,
                        )
                    continue
                positions = dict(cached_by_wallet[wallet])
                requested = requested_by_wallet[wallet]
                fetched_by_condition: dict[str, dict[str, Any]] = {
                    condition_id: {} for condition_id in requested
                }
                for position in result:
                    if position.size <= ZERO or position.condition_id not in fetched_by_condition:
                        continue
                    fetched_by_condition[position.condition_id][position.asset_id] = position
                expires_at = monotonic() + ACTIVE_POSITION_CACHE_SECONDS
                for condition_id, condition_positions in fetched_by_condition.items():
                    self._position_cache[(wallet.lower(), condition_id)] = (
                        expires_at,
                        condition_positions,
                    )
                    positions.update(condition_positions)
                positions_by_wallet[wallet] = positions
        return positions_by_wallet, failed_wallets

    async def _collect_trades(
        self,
        *,
        start: datetime,
        end: datetime,
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
                end=end,
                limit=limit,
                offset=offset,
            )
            page_items = list(payload)
            timestamps = [
                _attribute(item, "timestamp")
                for item in page_items
                if isinstance(_attribute(item, "timestamp"), datetime)
            ]
            # Keep a defensive local bound even though the provider supports the
            # range, since the overlap window is intentionally re-read and deduped.
            results.extend(
                item
                for item in page_items
                if not isinstance(_attribute(item, "timestamp"), datetime)
                or start <= _attribute(item, "timestamp") <= end
            )
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
                # A 429 is the only API error carrying Retry-After.  When the
                # provider omits it, use a slightly wider exponential fallback
                # instead of immediately repeating the same burst.
                fallback = 2 ** (attempt + 1) if error.rate_limited else attempt + 1
                await asyncio.sleep(max(0.05, float(error.retry_after or fallback)))
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
                rows: list[dict[str, Any]] = []
                for fingerprint, trade in batch:
                    if fingerprint in known:
                        continue
                    size = _decimal(_attribute(trade, "size"))
                    price = _decimal(_attribute(trade, "price"))
                    rows.append(
                        {
                            "fingerprint": fingerprint,
                            "proxy_wallet": str(_attribute(trade, "proxy_wallet") or "").lower(),
                            "asset_id": str(_attribute(trade, "asset_id", "asset") or ""),
                            "condition_id": str(_attribute(trade, "condition_id") or ""),
                            "side": str(_attribute(trade, "side") or "").upper(),
                            "size": size,
                            "price": price,
                            "amount": _decimal(_attribute(trade, "amount")) or size * price,
                            "outcome": str(_attribute(trade, "outcome") or ""),
                            "outcome_index": int(
                                _attribute(trade, "outcome_index", default=0) or 0
                            ),
                            "title": str(_attribute(trade, "title") or ""),
                            "market_slug": str(_attribute(trade, "market_slug", "slug") or ""),
                            "event_slug": str(_attribute(trade, "event_slug") or ""),
                            "icon_url": _attribute(trade, "icon_url", "icon"),
                            "display_name": _display_name(trade),
                            "transaction_hash": _attribute(trade, "transaction_hash"),
                            "timestamp": _attribute(trade, "timestamp"),
                            "imported_at": now,
                        }
                    )
                if not rows:
                    continue
                if self.database.settings.database_url.startswith("sqlite"):
                    await session.execute(
                        sqlite_insert(WhaleTrade)
                        .values(rows)
                        .on_conflict_do_nothing(index_elements=["fingerprint"])
                    )
                    await session.commit()
                else:
                    session.add_all(WhaleTrade(**row) for row in rows)
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
        returned = {str(_attribute(item, "condition_id") or "") for item in markets}
        missing = [condition_id for condition_id in wanted if condition_id not in returned]
        closed_fetch = getattr(self.client, "fetch_closed_markets_with_tags", None)
        if missing and closed_fetch is not None:
            markets.extend(await self._retry(closed_fetch, condition_ids=missing))
        await self._persist_markets(markets, now=now)

    async def _persist_markets(self, markets: Iterable[Any], *, now: datetime) -> None:
        markets = list(markets)
        if not markets:
            return
        condition_ids = [str(_attribute(item, "condition_id") or "") for item in markets]
        async with self.database.sessions() as session:
            existing = {
                item.condition_id: item
                for item in (
                    await session.scalars(
                        select(WhaleMarket).where(WhaleMarket.condition_id.in_(condition_ids))
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
                _apply_market_snapshot(row, item, now=now)
            await session.commit()

    async def _persist_position_wallet_hints(
        self,
        positions: Iterable[WhaleMarketPositionSnapshot],
        *,
        now: datetime,
    ) -> None:
        hints: dict[str, WhaleMarketPositionSnapshot] = {}
        for position in positions:
            hints.setdefault(position.proxy_wallet, position)
        if not hints:
            return
        stale_at = now - PROFILE_MISSING_CACHE - timedelta(seconds=1)
        async with self.database.sessions() as session:
            existing = {
                row.proxy_wallet: row
                for row in (
                    await session.scalars(
                        select(WhaleWallet).where(WhaleWallet.proxy_wallet.in_(hints))
                    )
                ).all()
            }
            for wallet, hint in hints.items():
                row = existing.get(wallet)
                if row is None:
                    row = WhaleWallet(
                        proxy_wallet=wallet,
                        display_name=hint.display_name or f"{wallet[:6]}…{wallet[-4:]}",
                        profile_missing=True,
                        refreshed_at=stale_at,
                    )
                    session.add(row)
                elif hint.display_name and (not row.display_name or row.profile_missing):
                    row.display_name = hint.display_name
                if hint.profile_image_url and not row.profile_image_url:
                    row.profile_image_url = hint.profile_image_url
                row.verified_badge = row.verified_badge or hint.verified_badge
                if (
                    row.profile_created_at is None
                    and row.pseudonym is None
                    and row.taker_tier is None
                ):
                    row.profile_missing = True
                    row.refreshed_at = min(row.refreshed_at, stale_at)
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
        ]
        wanted = wanted[: max(0, self.settings.whale_profile_batch_limit)]
        for offset in range(0, len(wanted), 10):
            batch = wanted[offset : offset + 10]
            profiles = await asyncio.gather(
                *(
                    self._retry(self.client.fetch_public_profile, address=wallet)
                    for wallet in batch
                ),
                return_exceptions=True,
            )
            async with self.database.sessions() as session:
                for wallet, profile in zip(batch, profiles, strict=True):
                    if isinstance(profile, Exception):
                        continue
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
                        row.profile_image_url = _attribute(profile, "profile_image_url")
                        row.profile_created_at = _attribute(
                            profile, "created_at", "profile_created_at"
                        )
                        row.verified_badge = bool(
                            _attribute(profile, "verified_badge", default=False)
                        )
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

    @staticmethod
    def _follow_ineligible_reason(
        market: WhaleMarket | None,
        *,
        position_present: bool,
        config: dict[str, Any],
    ) -> str | None:
        if market is None:
            return "market_metadata_unavailable"
        if market.closed or not market.active or market_price_is_settled(market):
            return "market_closed"
        if not position_present:
            return "position_exited"
        if not market.accepting_orders:
            return "orders_not_accepted"
        if market.liquidity < _decimal(config["min_liquidity_usdc"]):
            return "low_liquidity"
        return None

    async def _persist_entries(
        self,
        aggregates: list[WhaleAggregate],
        *,
        rule_matches: dict[tuple[str, str], set[str]],
        positions_by_wallet: dict[str, dict[str, Any]],
        failed_wallets: set[str],
        config: dict[str, Any],
        now: datetime,
        window_start: datetime,
    ) -> list[int]:
        async with self.database.sessions() as session:
            email_settings = await session.get(EmailSettings, 1)
            existing = {
                (row.proxy_wallet, row.asset_id): row
                for row in (await session.scalars(select(WhaleEntry))).all()
            }
            relevant_condition_ids = {
                item.condition_id for item in aggregates if item.condition_id
            } | {row.condition_id for row in existing.values()}
            markets = {
                row.condition_id: row
                for row in (
                    await session.scalars(
                        select(WhaleMarket).where(
                            WhaleMarket.condition_id.in_(relevant_condition_ids)
                        )
                    )
                ).all()
            }
            states = {
                (row.entry_id, row.rule_type): row
                for row in (await session.scalars(select(WhaleEntryRuleState))).all()
            }
            email_candidates: list[WhaleEmailCandidate] = []
            auto_candidates: list[
                tuple[WhaleEntry, set[str], WhaleMarket | None, bool, bool, bool]
            ] = []
            for aggregate in aggregates:
                market = markets.get(aggregate.condition_id)
                outcome_index = (
                    _market_outcome_index(
                        market,
                        asset_id=aggregate.asset_id,
                        fallback=aggregate.outcome_index,
                    )
                    if market is not None
                    else aggregate.outcome_index
                )
                if outcome_index is None:
                    outcome_index = aggregate.outcome_index
                key = (aggregate.proxy_wallet, aggregate.asset_id)
                row = existing.get(key)
                if row is None:
                    row = WhaleEntry(
                        proxy_wallet=aggregate.proxy_wallet,
                        asset_id=aggregate.asset_id,
                        condition_id=aggregate.condition_id,
                        outcome=aggregate.outcome,
                        outcome_index=outcome_index,
                        gross_buy_usdc=aggregate.gross_buy_usdc,
                        gross_buy_size=aggregate.gross_buy_size,
                        sold_size=aggregate.sold_size,
                        sold_usdc=aggregate.sold_usdc,
                        net_size=ZERO,
                        net_ratio=ZERO,
                        avg_buy_price=aggregate.avg_buy_price,
                        max_single_usdc=aggregate.max_single_usdc,
                        trade_count=aggregate.trade_count,
                        first_buy_at=aggregate.first_buy_at,
                        last_buy_at=aggregate.last_buy_at,
                        status="exited",
                        hedged=aggregate.hedged,
                        window_start=window_start,
                        computed_at=now,
                        follow_eligible=False,
                    )
                    session.add(row)
                    await session.flush()
                    existing[key] = row
                row.condition_id = aggregate.condition_id
                row.outcome = aggregate.outcome
                row.outcome_index = outcome_index
                row.gross_buy_usdc = aggregate.gross_buy_usdc
                row.gross_buy_size = aggregate.gross_buy_size
                row.sold_size = aggregate.sold_size
                row.sold_usdc = aggregate.sold_usdc
                row.avg_buy_price = aggregate.avg_buy_price
                row.max_single_usdc = aggregate.max_single_usdc
                if aggregate.source != "positions":
                    row.trade_count = aggregate.trade_count
                    # `first_buy_at` is the durable monitored build time.  Do not
                    # let the rolling 24-hour window move it forward after older
                    # fills age out; subsequent buys only advance `last_buy_at`.
                    row.first_buy_at = min(row.first_buy_at, aggregate.first_buy_at)
                    row.last_buy_at = aggregate.last_buy_at
                row.hedged = aggregate.hedged
                row.window_start = window_start
                row.computed_at = now

                market_terminal = bool(
                    market is not None
                    and (market.closed or not market.active or market_price_is_settled(market))
                )
                position_failed = aggregate.proxy_wallet in failed_wallets
                position = positions_by_wallet.get(aggregate.proxy_wallet, {}).get(
                    aggregate.asset_id
                )
                position_present = position is not None and position.size > ZERO
                if not position_failed:
                    row.position_checked_at = now
                    if position_present:
                        row.net_size = position.size
                        row.net_ratio = max(
                            ZERO,
                            min(
                                HUNDRED,
                                position.size / aggregate.gross_buy_size * HUNDRED
                                if aggregate.gross_buy_size > ZERO
                                else HUNDRED,
                            ),
                        )
                        row.status = "holding"
                    else:
                        row.net_size = ZERO
                        row.net_ratio = ZERO
                        row.status = "exited"

                if market_terminal and market is not None and market_price_is_settled(market):
                    prices = [_decimal(value) for value in _json_list(market.outcome_prices_json)]
                    if 0 <= row.outcome_index < len(prices):
                        row.settlement_price = prices[row.outcome_index]
                        row.settled_at = now
                    row.status = "exited"

                reason = self._follow_ineligible_reason(
                    market,
                    position_present=(row.net_size > ZERO if position_failed else position_present),
                    config=config,
                )
                if position_failed:
                    row.follow_eligible = False
                    row.follow_ineligible_reason = "position_check_failed"
                else:
                    row.follow_eligible = reason is None
                    row.follow_ineligible_reason = reason

                matches = rule_matches[key]
                new_rules: set[str] = set()
                for rule_type in matches:
                    state = states.get((row.id, rule_type))
                    if state is None:
                        new_rules.add(rule_type)
                        threshold = (
                            _decimal(config["new_account_threshold_usdc"])
                            if rule_type == NEW_ACCOUNT_RULE
                            else _decimal(config["large_amount_threshold_usdc"])
                        )
                        state = WhaleEntryRuleState(
                            entry_id=row.id,
                            rule_type=rule_type,
                            active=False,
                            first_triggered_at=now,
                            last_qualified_at=now,
                            threshold_usdc_snapshot=threshold,
                            registration_days_snapshot=(
                                int(config["registration_window_days"])
                                if rule_type == NEW_ACCOUNT_RULE
                                else None
                            ),
                        )
                        session.add(state)
                        states[(row.id, rule_type)] = state
                    else:
                        state.last_qualified_at = now
                    if position_failed:
                        if state.first_triggered_at == now:
                            state.inactive_at = now
                            state.inactive_reason = "position_check_failed"
                        continue
                    state.active = position_present and not market_terminal
                    if state.active:
                        state.inactive_at = None
                        state.inactive_reason = None
                    else:
                        state.inactive_at = now
                        state.inactive_reason = (
                            "market_closed" if market_terminal else "position_exited"
                        )

                if new_rules:
                    auto_candidates.append(
                        (
                            row,
                            set(new_rules),
                            market,
                            position_failed,
                            position_present,
                            market_terminal,
                        )
                    )

                if (
                    email_settings is not None
                    and email_settings.notifications_enabled
                    and new_rules
                    and not position_failed
                    and position_present
                    and not market_terminal
                ):
                    email_candidates.append(
                        WhaleEmailCandidate(
                            entry_id=row.id,
                            new_rules=frozenset(new_rules),
                        )
                    )

            qualified_keys = set(rule_matches)
            entries_by_id = {row.id: row for row in existing.values()}
            for (entry_id, _rule_type), state in states.items():
                if not state.active:
                    continue
                row = entries_by_id.get(entry_id)
                if row is None:
                    continue
                key = (row.proxy_wallet, row.asset_id)
                # Once a rule has triggered, it stays current while the position
                # remains open. The 24-hour window and account-age rule only
                # decide whether a new trigger is created.
                if key in qualified_keys:
                    continue
                market = markets.get(row.condition_id)
                market_terminal = bool(
                    market is not None
                    and (market.closed or not market.active or market_price_is_settled(market))
                )
                if row.proxy_wallet in failed_wallets:
                    row.follow_eligible = False
                    row.follow_ineligible_reason = "position_check_failed"
                    continue
                position = positions_by_wallet.get(row.proxy_wallet, {}).get(row.asset_id)
                position_present = position is not None and position.size > ZERO
                row.position_checked_at = now
                if position_present:
                    row.net_size = position.size
                    row.net_ratio = max(
                        ZERO,
                        min(
                            HUNDRED,
                            position.size / row.gross_buy_size * HUNDRED
                            if row.gross_buy_size > ZERO
                            else HUNDRED,
                        ),
                    )
                    row.status = "holding"
                else:
                    row.net_size = ZERO
                    row.net_ratio = ZERO
                    row.status = "exited"
                if market_terminal:
                    row.status = "exited"
                    if market is not None and market_price_is_settled(market):
                        prices = [
                            _decimal(value) for value in _json_list(market.outcome_prices_json)
                        ]
                        if 0 <= row.outcome_index < len(prices):
                            row.settlement_price = prices[row.outcome_index]
                            row.settled_at = now
                reason = self._follow_ineligible_reason(
                    market,
                    position_present=position_present,
                    config=config,
                )
                row.follow_eligible = reason is None
                row.follow_ineligible_reason = reason
                if market_terminal or not position_present:
                    state.active = False
                    state.inactive_at = now
                    state.inactive_reason = (
                        "market_closed" if market_terminal else "position_exited"
                    )

            await enqueue_whale_email_deliveries(
                session,
                candidates=email_candidates,
                triggered_at=now,
            )

            pending_decision_ids: list[int] = []
            active_rules_by_condition_asset: defaultdict[str, defaultdict[str, set[str]]] = (
                defaultdict(lambda: defaultdict(set))
            )
            active_entries_by_condition_asset: defaultdict[
                str, defaultdict[str, list[WhaleEntry]]
            ] = defaultdict(lambda: defaultdict(list))
            for (entry_id, _rule_type), state in states.items():
                if not state.active:
                    continue
                active_entry = entries_by_id.get(entry_id)
                if active_entry is not None and active_entry.net_size > ZERO:
                    active_rules_by_condition_asset[active_entry.condition_id][
                        active_entry.asset_id
                    ].add(state.rule_type)
                    entries = active_entries_by_condition_asset[active_entry.condition_id][
                        active_entry.asset_id
                    ]
                    if active_entry not in entries:
                        entries.append(active_entry)

            held_decisions = (
                list(
                    (
                        await session.scalars(
                            select(WhaleAutoFollowDecision)
                            .join(
                                WhaleOrder,
                                WhaleOrder.id == WhaleAutoFollowDecision.buy_order_id,
                            )
                            .join(
                                WhaleFollowPosition,
                                WhaleFollowPosition.id == WhaleOrder.position_id,
                            )
                            .where(
                                WhaleAutoFollowDecision.condition_id.in_(relevant_condition_ids),
                                WhaleAutoFollowDecision.selected_rule.is_not(None),
                                WhaleOrder.filled_size > ZERO,
                                WhaleFollowPosition.size > ZERO,
                                WhaleFollowPosition.status.in_(["opening", "open", "closing"]),
                            )
                        )
                    ).all()
                )
                if relevant_condition_ids
                else []
            )
            held_decisions_by_condition_asset: defaultdict[
                str, defaultdict[str, list[WhaleAutoFollowDecision]]
            ] = defaultdict(lambda: defaultdict(list))
            for decision in held_decisions:
                held_decisions_by_condition_asset[decision.condition_id][decision.asset_id].append(
                    decision
                )

            candidate_entry_ids = {candidate[0].id for candidate in auto_candidates}
            conflict_resolutions: dict[str, AutoConflictResolution] = {}
            large_priority_enabled = bool(config["large_amount_conflict_priority_enabled"])
            priority_condition_ids = set(active_rules_by_condition_asset)
            if large_priority_enabled:
                priority_condition_ids.update(held_decisions_by_condition_asset)
            for condition_id in priority_condition_ids:
                priority_rules_by_asset = {
                    asset_id: set(rules)
                    for asset_id, rules in active_rules_by_condition_asset[condition_id].items()
                }
                if large_priority_enabled:
                    for asset_id, decisions in held_decisions_by_condition_asset[
                        condition_id
                    ].items():
                        priority_rules_by_asset.setdefault(asset_id, set()).update(
                            decision.selected_rule
                            for decision in decisions
                            if decision.selected_rule is not None
                        )
                if len(priority_rules_by_asset) < 2:
                    continue
                strongest_by_asset = {
                    asset_id: _strongest_whale_rule(rules)
                    for asset_id, rules in priority_rules_by_asset.items()
                }
                highest_priority = max(
                    WHALE_RULE_PRIORITY.get(rule or "", 0) for rule in strongest_by_asset.values()
                )
                highest_assets = {
                    asset_id
                    for asset_id, rule in strongest_by_asset.items()
                    if WHALE_RULE_PRIORITY.get(rule or "", 0) == highest_priority
                }
                held_asset_ids = set(held_decisions_by_condition_asset[condition_id])
                if not large_priority_enabled:
                    highest_assets = set(priority_rules_by_asset)
                    blocked_asset_ids = set(priority_rules_by_asset)
                    exit_asset_ids = set(held_asset_ids)
                    lock_required = True
                elif len(highest_assets) > 1:
                    blocked_asset_ids = set(priority_rules_by_asset)
                    exit_asset_ids = set(held_asset_ids)
                    lock_required = True
                else:
                    winning_asset_id = next(iter(highest_assets))
                    blocked_asset_ids = set(priority_rules_by_asset) - {winning_asset_id}
                    exit_asset_ids = held_asset_ids & blocked_asset_ids
                    lock_required = bool(exit_asset_ids)

                trigger_entry: WhaleEntry | None = None
                if lock_required:
                    trigger_assets = highest_assets - exit_asset_ids or highest_assets
                    trigger_entries = [
                        entry
                        for asset_id in trigger_assets
                        for entry in active_entries_by_condition_asset[condition_id][asset_id]
                        if (
                            strongest_by_asset[asset_id] is not None
                            and (entry.id, strongest_by_asset[asset_id]) in states
                            and states[(entry.id, strongest_by_asset[asset_id])].active
                        )
                    ]
                    if trigger_entries:
                        trigger_entry = min(
                            trigger_entries,
                            key=lambda entry: (
                                entry.id not in candidate_entry_ids,
                                entry.id,
                            ),
                        )
                conflict_resolutions[condition_id] = AutoConflictResolution(
                    blocked_asset_ids=frozenset(blocked_asset_ids),
                    exit_asset_ids=frozenset(exit_asset_ids),
                    trigger_entry=trigger_entry,
                    lock_required=lock_required,
                )

            candidate_conditions = {
                candidate[0].condition_id for candidate in auto_candidates
            } | set(conflict_resolutions)
            existing_locks = (
                {
                    lock.condition_id: lock
                    for lock in (
                        await session.scalars(
                            select(WhaleAutoMarketLock).where(
                                WhaleAutoMarketLock.condition_id.in_(candidate_conditions)
                            )
                        )
                    ).all()
                }
                if candidate_conditions
                else {}
            )

            for condition_id, resolution in conflict_resolutions.items():
                if not resolution.lock_required or condition_id in existing_locks:
                    continue
                trigger_entry = resolution.trigger_entry
                if trigger_entry is None:
                    continue
                trigger_rules = sorted(
                    active_rules_by_condition_asset[condition_id][trigger_entry.asset_id]
                )
                lock = WhaleAutoMarketLock(
                    condition_id=condition_id,
                    trigger_entry_id=trigger_entry.id,
                    trigger_wallet=trigger_entry.proxy_wallet,
                    trigger_asset_id=trigger_entry.asset_id,
                    trigger_outcome=trigger_entry.outcome,
                    trigger_amount_usdc=trigger_entry.gross_buy_usdc,
                    trigger_rules_json=_json_dump(trigger_rules),
                    reason=(
                        f"反向钱包 {trigger_entry.proxy_wallet} 触发 "
                        f"{','.join(trigger_rules)}，金额 {trigger_entry.gross_buy_usdc} USDC"
                    ),
                    exit_status=("pending" if resolution.exit_asset_ids else "not_required"),
                    last_error=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(lock)
                existing_locks[condition_id] = lock

            for condition_id, resolution in conflict_resolutions.items():
                if not resolution.exit_asset_ids:
                    continue
                lock = existing_locks.get(condition_id)
                if lock is None:
                    continue
                for asset_id in resolution.exit_asset_ids:
                    for bought in held_decisions_by_condition_asset[condition_id][asset_id]:
                        bought.status = "exit_pending"
                        bought.reason = "持仓后出现同级或更高优先级反向信号，等待风控卖出"
                        bought.updated_at = now
                lock.exit_status = "pending"
                lock.updated_at = now

            for (
                entry,
                new_rules,
                market,
                position_failed,
                position_present,
                market_terminal,
            ) in auto_candidates:
                decision = await session.scalar(
                    select(WhaleAutoFollowDecision).where(
                        WhaleAutoFollowDecision.asset_id == entry.asset_id,
                        WhaleAutoFollowDecision.proxy_wallet == entry.proxy_wallet,
                    )
                )
                classification = _whale_statistics_classification(
                    market.tags_json if market is not None else None
                )
                category = str(classification["category"])
                active_rules = sorted(
                    rule_type
                    for rule_type in WHALE_RULES
                    if (entry.id, rule_type) in states and states[(entry.id, rule_type)].active
                )
                matched_rules = active_rules or sorted(new_rules)
                resolution = conflict_resolutions.get(entry.condition_id)
                if decision is not None:
                    decision.matched_rules_json = _json_dump(matched_rules)
                    decision.updated_at = now
                    if decision.buy_order_id is None:
                        if entry.condition_id in existing_locks:
                            decision.status = "conflict_locked"
                            decision.reason = "该市场已经被永久分歧锁定"
                            decision.processed_at = now
                        elif resolution is not None and entry.asset_id in (
                            resolution.blocked_asset_ids
                        ):
                            decision.status = "skipped"
                            decision.reason = "反向全量超大额信号优先，本次新号信号不买入"
                            decision.processed_at = now
                    continue
                decision = WhaleAutoFollowDecision(
                    entry_id=entry.id,
                    proxy_wallet=entry.proxy_wallet,
                    asset_id=entry.asset_id,
                    condition_id=entry.condition_id,
                    outcome=entry.outcome,
                    outcome_index=entry.outcome_index,
                    matched_rules_json=_json_dump(matched_rules),
                    selected_rule=None,
                    category=category,
                    configured_amount_usdc=None,
                    configured_min_price=None,
                    configured_max_price=None,
                    configured_low_price_max_price=None,
                    configured_low_price_amount_usdc=None,
                    selected_amount_usdc=None,
                    observed_best_ask=None,
                    status="pending",
                    reason=None,
                    buy_order_id=None,
                    latest_sell_order_id=None,
                    processed_at=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(decision)
                await session.flush()

                if entry.condition_id in existing_locks:
                    decision.status = "conflict_locked"
                    decision.reason = "该市场已经被永久分歧锁定"
                    decision.processed_at = now
                    continue
                if resolution is not None and entry.asset_id in resolution.blocked_asset_ids:
                    decision.status = "skipped"
                    decision.reason = "反向全量超大额信号优先，本次新号信号不买入"
                    decision.processed_at = now
                    continue
                if position_failed:
                    decision.status = "skipped"
                    decision.reason = "巨鲸当前持仓核验失败"
                    decision.processed_at = now
                    continue
                if not position_present:
                    decision.status = "skipped"
                    decision.reason = "巨鲸触发时已经完全退出"
                    decision.processed_at = now
                    continue
                if market_terminal:
                    decision.status = "failed"
                    decision.reason = "市场已经关闭或结果已经确定"
                    decision.processed_at = now
                    continue

                selected: (
                    tuple[
                        str,
                        Decimal,
                        Decimal,
                        Decimal,
                        Decimal | None,
                        Decimal | None,
                    ]
                    | None
                ) = None
                for rule_type in (LARGE_AMOUNT_RULE, NEW_ACCOUNT_RULE):
                    if rule_type not in matched_rules:
                        continue
                    prefix = "large_amount" if rule_type == LARGE_AMOUNT_RULE else "new_account"
                    categories = {
                        str(value)
                        for value in _json_list(config[f"{prefix}_auto_follow_categories_json"])
                    }
                    if bool(config[f"{prefix}_auto_follow_enabled"]) and category in categories:
                        selected = (
                            rule_type,
                            _decimal(config[f"{prefix}_auto_follow_amount_usdc"]),
                            _auto_follow_price(config[f"{prefix}_auto_follow_min_price"]),
                            _auto_follow_price(config[f"{prefix}_auto_follow_max_price"]),
                            (
                                _auto_follow_price(
                                    config[f"{prefix}_auto_follow_low_price_max_price"]
                                )
                                if config[f"{prefix}_auto_follow_low_price_max_price"] is not None
                                else None
                            ),
                            (
                                _decimal(config[f"{prefix}_auto_follow_low_price_amount_usdc"])
                                if config[f"{prefix}_auto_follow_low_price_amount_usdc"] is not None
                                else None
                            ),
                        )
                        break
                if selected is None:
                    enabled_rules = []
                    for rule_type in matched_rules:
                        prefix = "large_amount" if rule_type == LARGE_AMOUNT_RULE else "new_account"
                        if bool(config[f"{prefix}_auto_follow_enabled"]):
                            enabled_rules.append(rule_type)
                    decision.status = "skipped"
                    decision.reason = (
                        "市场分类未在自动跟单策略中启用"
                        if enabled_rules
                        else "对应自动跟单策略当前关闭"
                    )
                    decision.processed_at = now
                    continue
                (
                    decision.selected_rule,
                    decision.configured_amount_usdc,
                    decision.configured_min_price,
                    decision.configured_max_price,
                    decision.configured_low_price_max_price,
                    decision.configured_low_price_amount_usdc,
                ) = selected
                pending_decision_ids.append(decision.id)

            active_entry_ids = {entry_id for (entry_id, _), state in states.items() if state.active}
            for row in existing.values():
                if row.id not in active_entry_ids:
                    row.follow_eligible = False
                    if row.follow_ineligible_reason is None:
                        row.follow_ineligible_reason = "rule_inactive"
            await session.commit()
            return pending_decision_ids

    async def _process_auto_decisions(self, decision_ids: Iterable[int]) -> None:
        if self.executor is None:
            return
        for decision_id in decision_ids:
            try:
                async with self.database.sessions() as session:
                    decision = await session.get(WhaleAutoFollowDecision, decision_id)
                    if decision is None or decision.status != "pending":
                        continue
                    if await session.get(WhaleAutoMarketLock, decision.condition_id) is not None:
                        decision.status = "conflict_locked"
                        decision.reason = "该市场已经被永久分歧锁定"
                        decision.processed_at = utcnow()
                        decision.updated_at = utcnow()
                        await session.commit()
                        continue
                    amount = _decimal(decision.configured_amount_usdc)
                    minimum = _auto_follow_price(decision.configured_min_price)
                    maximum = _auto_follow_price(decision.configured_max_price)
                    low_maximum = (
                        _auto_follow_price(decision.configured_low_price_max_price)
                        if decision.configured_low_price_max_price is not None
                        else None
                    )
                    low_amount = (
                        _decimal(decision.configured_low_price_amount_usdc)
                        if decision.configured_low_price_amount_usdc is not None
                        else None
                    )
                    entry_id = decision.entry_id
                    asset_id = decision.asset_id
                quote = await self.executor.quote_follow(
                    asset_id=asset_id,
                    amount_usdc=amount,
                    entry_id=entry_id,
                    require_active_signal=False,
                    minimum_price=minimum,
                    maximum_price=maximum,
                    low_price_max_price=low_maximum,
                    low_price_amount_usdc=low_amount,
                )
                async with self.database.sessions() as session:
                    decision = await session.get(WhaleAutoFollowDecision, decision_id)
                    if decision is None or decision.status != "pending":
                        continue
                    decision.observed_best_ask = quote.best_ask
                    decision.selected_amount_usdc = quote.amount_usdc
                    decision.updated_at = utcnow()
                    if await session.get(WhaleAutoMarketLock, decision.condition_id) is not None:
                        decision.status = "conflict_locked"
                        decision.reason = "下单前市场出现分歧，已经永久锁定"
                        decision.processed_at = utcnow()
                        await session.commit()
                        continue
                    await session.commit()
                order_id = await self.executor.execute_follow(
                    quote,
                    f"auto:{decision_id}",
                    order_source="auto_follow",
                )
                async with self.database.sessions() as session:
                    decision = await session.get(WhaleAutoFollowDecision, decision_id)
                    order = await session.get(WhaleOrder, order_id)
                    if decision is None:
                        continue
                    decision.buy_order_id = order_id
                    decision.processed_at = utcnow()
                    decision.updated_at = utcnow()
                    if order is not None and order.filled_size > ZERO:
                        market_lock = await session.get(WhaleAutoMarketLock, decision.condition_id)
                        requires_exit = _decision_requires_conflict_exit(decision, market_lock)
                        decision.status = "exit_pending" if requires_exit else "bought"
                        decision.reason = (
                            "买入成交后市场出现分歧，等待风控卖出"
                            if requires_exit
                            else "自动跟单买入已执行"
                        )
                        if requires_exit and market_lock is not None:
                            market_lock.exit_status = "pending"
                            market_lock.updated_at = utcnow()
                    else:
                        decision.status = "failed"
                        decision.reason = (
                            order.reason if order is not None and order.reason else "自动买入未成交"
                        )
                    await session.commit()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                async with self.database.sessions() as session:
                    decision = await session.get(WhaleAutoFollowDecision, decision_id)
                    if decision is not None and decision.status == "pending":
                        order = await session.scalar(
                            select(WhaleOrder).where(
                                WhaleOrder.idempotency_key == f"whale:auto:{decision_id}"
                            )
                        )
                        if order is not None:
                            decision.buy_order_id = order.id
                        observed_best_ask = getattr(error, "observed_best_ask", None)
                        if observed_best_ask is not None:
                            decision.observed_best_ask = _decimal(observed_best_ask)
                        if order is not None and order.filled_size > ZERO:
                            market_lock = await session.get(
                                WhaleAutoMarketLock, decision.condition_id
                            )
                            requires_exit = _decision_requires_conflict_exit(decision, market_lock)
                            decision.status = "exit_pending" if requires_exit else "bought"
                            decision.reason = (
                                "买入成交后市场出现分歧，等待风控卖出"
                                if requires_exit
                                else "自动跟单买入已执行"
                            )
                            if requires_exit and market_lock is not None:
                                market_lock.exit_status = "pending"
                                market_lock.updated_at = utcnow()
                        else:
                            decision.status = "failed"
                            decision.reason = str(error)[:1000]
                        decision.processed_at = utcnow()
                        decision.updated_at = utcnow()
                        await session.commit()

    async def _process_conflict_exits(self) -> None:
        if self.executor is None:
            return
        async with self.database.sessions() as session:
            locks = list(
                (
                    await session.scalars(
                        select(WhaleAutoMarketLock).where(
                            WhaleAutoMarketLock.exit_status.in_(["pending", "exiting"])
                        )
                    )
                ).all()
            )
        for market_lock in locks:
            try:
                async with self.database.sessions() as session:
                    bought_decisions = list(
                        (
                            await session.scalars(
                                select(WhaleAutoFollowDecision)
                                .join(
                                    WhaleOrder,
                                    WhaleOrder.id == WhaleAutoFollowDecision.buy_order_id,
                                )
                                .where(
                                    WhaleAutoFollowDecision.condition_id
                                    == market_lock.condition_id,
                                    WhaleAutoFollowDecision.status == "exit_pending",
                                    WhaleOrder.filled_size > ZERO,
                                )
                            )
                        ).all()
                    )
                    asset_ids = list(dict.fromkeys(row.asset_id for row in bought_decisions))
                    positions = (
                        list(
                            (
                                await session.scalars(
                                    select(WhaleFollowPosition).where(
                                        WhaleFollowPosition.condition_id
                                        == market_lock.condition_id,
                                        WhaleFollowPosition.asset_id.in_(asset_ids),
                                        WhaleFollowPosition.size > ZERO,
                                        WhaleFollowPosition.status.in_(
                                            ["opening", "open", "closing"]
                                        ),
                                    )
                                )
                            ).all()
                        )
                        if asset_ids
                        else []
                    )
                    lock_row = await session.get(WhaleAutoMarketLock, market_lock.condition_id)
                    if lock_row is None:
                        continue
                    if not positions:
                        lock_row.exit_status = "completed"
                        lock_row.last_error = None
                        lock_row.updated_at = utcnow()
                        for decision in bought_decisions:
                            decision.status = "exit_completed"
                            decision.reason = "分歧市场风控退出已完成"
                            decision.updated_at = utcnow()
                        await session.commit()
                        continue
                    lock_row.exit_status = "exiting"
                    lock_row.updated_at = utcnow()
                    await session.commit()

                pending = False
                latest_order_by_asset: dict[str, int] = {}
                for position in positions:
                    if await self.executor.write_off_terminal_sell_remainder(position.id):
                        continue
                    if position.status == "closing":
                        pending = True
                        continue
                    quote = await self.executor.quote_sell(
                        position_id=position.id,
                        size=None,
                        sell_all=True,
                    )
                    order_id = await self.executor.execute_sell(
                        quote,
                        f"conflict:{market_lock.condition_id}:{position.id}:{uuid4().hex}",
                        order_source="conflict_exit",
                    )
                    latest_order_by_asset[position.asset_id] = order_id
                    async with self.database.sessions() as session:
                        current = await session.get(WhaleFollowPosition, position.id)
                        if current is not None and current.size > ZERO:
                            pending = True

                async with self.database.sessions() as session:
                    lock_row = await session.get(WhaleAutoMarketLock, market_lock.condition_id)
                    if lock_row is None:
                        continue
                    decisions = list(
                        (
                            await session.scalars(
                                select(WhaleAutoFollowDecision)
                                .join(
                                    WhaleOrder,
                                    WhaleOrder.id == WhaleAutoFollowDecision.buy_order_id,
                                )
                                .where(
                                    WhaleAutoFollowDecision.condition_id
                                    == market_lock.condition_id,
                                    WhaleAutoFollowDecision.status == "exit_pending",
                                    WhaleOrder.filled_size > ZERO,
                                )
                            )
                        ).all()
                    )
                    lock_row.exit_status = "pending" if pending else "completed"
                    lock_row.last_error = None
                    lock_row.updated_at = utcnow()
                    for decision in decisions:
                        decision.status = "exit_pending" if pending else "exit_completed"
                        decision.reason = (
                            "分歧市场仍有剩余份额，等待继续退出"
                            if pending
                            else "分歧市场风控退出已完成"
                        )
                        decision.latest_sell_order_id = latest_order_by_asset.get(
                            decision.asset_id, decision.latest_sell_order_id
                        )
                        decision.updated_at = utcnow()
                    await session.commit()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                message = str(error)[:1000]
                async with self.database.sessions() as session:
                    lock_row = await session.get(WhaleAutoMarketLock, market_lock.condition_id)
                    if lock_row is None:
                        continue
                    lock_row.last_error = message
                    lock_row.exit_status = (
                        "market_closed"
                        if "不再开放" in message or "结果已经确定" in message
                        else "pending"
                    )
                    lock_row.updated_at = utcnow()
                    decisions = list(
                        (
                            await session.scalars(
                                select(WhaleAutoFollowDecision)
                                .join(
                                    WhaleOrder,
                                    WhaleOrder.id == WhaleAutoFollowDecision.buy_order_id,
                                )
                                .where(
                                    WhaleAutoFollowDecision.condition_id
                                    == market_lock.condition_id,
                                    WhaleAutoFollowDecision.status == "exit_pending",
                                    WhaleOrder.filled_size > ZERO,
                                )
                            )
                        ).all()
                    )
                    latest_sell_ids = {
                        asset_id: order_id
                        for asset_id, order_id in (
                            await session.execute(
                                select(WhaleOrder.asset_id, func.max(WhaleOrder.id))
                                .where(
                                    WhaleOrder.condition_id == market_lock.condition_id,
                                    WhaleOrder.source == "conflict_exit",
                                    WhaleOrder.side == "SELL",
                                )
                                .group_by(WhaleOrder.asset_id)
                            )
                        ).all()
                    }
                    for decision in decisions:
                        decision.status = (
                            "failed" if lock_row.exit_status == "market_closed" else "exit_pending"
                        )
                        decision.latest_sell_order_id = latest_sell_ids.get(
                            decision.asset_id, decision.latest_sell_order_id
                        )
                        decision.reason = f"分歧风控卖出未完成：{message}"
                        decision.updated_at = utcnow()
                    await session.commit()

    async def _update_tag_counts(self, *, now: datetime) -> None:
        async with self.database.sessions() as session:
            market_ids = set(
                (
                    await session.scalars(
                        select(WhaleEntry.condition_id)
                        .where(_not_excluded_wallet(WhaleEntry.proxy_wallet))
                        .distinct()
                    )
                ).all()
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
    strategy_minimum_price: Decimal | None
    strategy_maximum_price: Decimal | None
    low_price_max_price: Decimal | None
    selected_low_price_amount: bool
    tick_size: Decimal
    minimum_order_usdc: Decimal
    estimated_shares: Decimal
    estimated_fee_usdc: Decimal
    total_cost_usdc: Decimal
    profit_ratio_percent: Decimal
    max_loss_usdc: Decimal
    winning_payout_usdc: Decimal
    winning_profit_usdc: Decimal
    immediate_exit_price: Decimal | None
    immediate_exit_proceeds_usdc: Decimal | None
    immediate_exit_fee_usdc: Decimal | None
    immediate_exit_pnl_usdc: Decimal | None
    immediate_exit_pnl_percent: Decimal | None
    immediate_exit_unavailable_reason: str | None
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

    async def refresh_balance(self) -> Decimal:
        """Refresh the shared execution-wallet collateral balance."""
        return await self._live_balance(await self._account())

    async def sync_chain_test_market(self, market: WhaleMarketSnapshot) -> None:
        now = utcnow()
        async with self.database.sessions() as session:
            row = await session.get(WhaleMarket, market.condition_id)
            if row is None:
                row = WhaleMarket(condition_id=market.condition_id)
                session.add(row)
            _apply_market_snapshot(row, market, now=now)
            await session.commit()

    async def quote_chain_test_buy(
        self,
        *,
        market: WhaleMarketSnapshot,
        asset_id: str,
        amount_usdc: Decimal,
    ) -> WhaleFollowQuote:
        await self.sync_chain_test_market(market)
        quote = await self.quote_follow(
            asset_id=asset_id,
            amount_usdc=amount_usdc,
            entry_id=None,
            require_active_signal=False,
        )
        await self._ensure_chain_test_buy_limits(quote, refresh_balance=False)
        return quote

    async def _ensure_chain_test_buy_limits(
        self,
        quote: WhaleFollowQuote,
        *,
        refresh_balance: bool,
    ) -> None:
        account = await self._account()
        balance = (
            await self._live_balance(account) if refresh_balance else quote.available_balance_usdc
        )
        spendable = max(ZERO, min(account.budget_usdc, balance) - account.cash_reserve_usdc)
        if quote.total_cost_usdc > spendable:
            raise ValueError("测试买入会突破执行钱包预算或现金保留额")
        day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        async with self.database.sessions() as session:
            open_exposure = sum(
                (
                    position.cost_usdc
                    for position in (
                        await session.scalars(
                            select(WhaleFollowPosition).where(
                                WhaleFollowPosition.status.in_(["opening", "open", "closing"])
                            )
                        )
                    ).all()
                ),
                ZERO,
            )
            daily_orders = list(
                (
                    await session.scalars(
                        select(WhaleOrder).where(
                            WhaleOrder.side == "BUY",
                            WhaleOrder.created_at >= day_start,
                            WhaleOrder.status.not_in(
                                ["blocked", "rejected", "unfilled", "cancelled"]
                            ),
                        )
                    )
                ).all()
            )
            daily_buys = sum(
                (
                    order.filled_usdc + order.fee_usdc
                    if order.filled_size > ZERO
                    else order.requested_usdc
                    for order in daily_orders
                ),
                ZERO,
            )
            daily_loss = -sum(
                (
                    min(entry.realized_pnl, ZERO)
                    for entry in (
                        await session.scalars(
                            select(WhaleFollowLedger).where(
                                WhaleFollowLedger.timestamp >= day_start
                            )
                        )
                    ).all()
                ),
                ZERO,
            )
        if open_exposure + quote.total_cost_usdc > account.max_total_exposure_usdc:
            raise ValueError("测试买入会突破执行钱包总敞口限制")
        if daily_buys + quote.total_cost_usdc > account.daily_buy_limit_usdc:
            raise ValueError("测试买入会突破执行钱包每日买入限额")
        if daily_loss >= account.daily_loss_limit_usdc:
            raise ValueError("执行钱包已达到每日亏损限制，不能继续测试买入")

    async def _account(self) -> ExecutionAccount:
        async with self.database.sessions() as session:
            account = await session.get(ExecutionAccount, 1)
            if (
                account is None
                or account.status not in {"ready", "insufficient_balance"}
                or account.signature_type != 3
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
                # Deliberately do not mutate status: cash reserve is an order-time
                # warning and must not overwrite credential verification state.
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

    async def quote_follow(
        self,
        *,
        asset_id: str,
        amount_usdc: Decimal,
        entry_id: int | None,
        require_active_signal: bool = True,
        minimum_price: Decimal | None = None,
        maximum_price: Decimal | None = None,
        low_price_max_price: Decimal | None = None,
        low_price_amount_usdc: Decimal | None = None,
    ) -> WhaleFollowQuote:
        if not self.settings.trading_enabled:
            raise ValueError("自动实盘已被系统紧急停用")
        if minimum_price is not None:
            minimum_price = _auto_follow_price(minimum_price)
        if maximum_price is not None:
            maximum_price = _auto_follow_price(maximum_price)
        if low_price_max_price is not None:
            low_price_max_price = _auto_follow_price(low_price_max_price)
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
            if entry_id is not None:
                if entry is None or entry.asset_id != asset_id:
                    raise ValueError("巨鲸投入记录与所选 outcome 不匹配")
                if await session.get(WhaleExclusion, entry.proxy_wallet.lower()) is not None:
                    raise ValueError("该巨鲸账户已加入排除名单，不能继续跟买")
                if require_active_signal:
                    active_rule_count = int(
                        await session.scalar(
                            select(func.count(WhaleEntryRuleState.id)).where(
                                WhaleEntryRuleState.entry_id == entry.id,
                                WhaleEntryRuleState.active.is_(True),
                            )
                        )
                        or 0
                    )
                    if active_rule_count == 0:
                        raise ValueError("该巨鲸信号已经进入历史记录，不能继续跟买")
                    if not entry.follow_eligible:
                        raise ValueError("该巨鲸信号当前不满足跟买条件")
            slippage = whale_settings.follow_slippage_cents
            sell_slippage = whale_settings.sell_slippage_cents
            warning_delta = whale_settings.max_price_delta_cents
            source_wallet = entry.proxy_wallet if entry is not None else None
            whale_avg_price = entry.avg_buy_price if entry is not None else None
        account = await self._account()
        market, outcome_index, outcome = await self._market_for_asset(asset_id)
        self._ensure_market_open(market)
        if market_price_is_settled(market):
            raise ValueError("市场结果已经确定，不再接受跟单")
        book = await self.client.fetch_order_book(asset_id)
        if book.best_ask is None:
            raise AutoFollowQuoteRejected("市场当前没有可成交卖盘")
        if minimum_price is not None and book.best_ask < minimum_price:
            raise AutoFollowQuoteRejected(
                f"实际买价 {_decimal_display(book.best_ask)} "
                f"低于策略最低价 {_decimal_display(minimum_price)}",
                observed_best_ask=book.best_ask,
            )
        if maximum_price is not None and book.best_ask > maximum_price:
            raise AutoFollowQuoteRejected(
                f"实际买价 {_decimal_display(book.best_ask)} "
                f"高于策略最高价 {_decimal_display(maximum_price)}",
                observed_best_ask=book.best_ask,
            )
        selected_low_price_amount = False
        if low_price_max_price is not None or low_price_amount_usdc is not None:
            if low_price_max_price is None or low_price_amount_usdc is None:
                raise ValueError("低价分界与低价金额配置不完整")
            if minimum_price is None or maximum_price is None:
                raise ValueError("低价金额必须配置在自动跟单价格区间内")
            if not minimum_price < low_price_max_price < maximum_price:
                raise ValueError("低价分界必须严格位于自动跟单价格区间内")
            amount_usdc, selected_low_price_amount = _select_auto_follow_amount(
                base_amount=amount_usdc,
                best_ask=book.best_ask,
                low_price_max_price=low_price_max_price,
                low_price_amount=low_price_amount_usdc,
            )
        worst_price = market_worst_price(book.best_ask, book.tick_size, slippage, side="BUY")
        if maximum_price is not None:
            worst_price = min(worst_price, maximum_price)
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
        winning_profit = shares - total_cost
        immediate_exit_price: Decimal | None = None
        immediate_exit_proceeds: Decimal | None = None
        immediate_exit_fee: Decimal | None = None
        immediate_exit_pnl: Decimal | None = None
        immediate_exit_pnl_percent: Decimal | None = None
        immediate_exit_unavailable_reason: str | None = None
        if book.best_bid is None:
            immediate_exit_unavailable_reason = "市场当前没有可成交买盘"
        elif shares < book.min_order_size:
            immediate_exit_unavailable_reason = f"预计份额低于市场最小卖出量 {book.min_order_size}"
        else:
            immediate_exit_price = market_worst_price(
                book.best_bid,
                book.tick_size,
                sell_slippage,
                side="SELL",
            )
            immediate_exit_proceeds = shares * immediate_exit_price
            immediate_exit_fee = estimated_market_fee(
                shares,
                immediate_exit_price,
                market.fee_rate,
                market.fee_exponent,
            )
            immediate_exit_pnl = immediate_exit_proceeds - immediate_exit_fee - total_cost
            immediate_exit_pnl_percent = (
                immediate_exit_pnl / total_cost * HUNDRED if total_cost > ZERO else ZERO
            )
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
            strategy_minimum_price=minimum_price,
            strategy_maximum_price=maximum_price,
            low_price_max_price=low_price_max_price,
            selected_low_price_amount=selected_low_price_amount,
            tick_size=book.tick_size,
            minimum_order_usdc=minimum_order_usdc,
            estimated_shares=shares,
            estimated_fee_usdc=fee,
            total_cost_usdc=total_cost,
            profit_ratio_percent=profit_ratio,
            max_loss_usdc=total_cost,
            winning_payout_usdc=shares,
            winning_profit_usdc=winning_profit,
            immediate_exit_price=immediate_exit_price,
            immediate_exit_proceeds_usdc=immediate_exit_proceeds,
            immediate_exit_fee_usdc=immediate_exit_fee,
            immediate_exit_pnl_usdc=immediate_exit_pnl,
            immediate_exit_pnl_percent=immediate_exit_pnl_percent,
            immediate_exit_unavailable_reason=immediate_exit_unavailable_reason,
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

    async def execute_follow(
        self,
        quote: WhaleFollowQuote,
        confirmation_id: str,
        *,
        order_source: str = "follow",
    ) -> int:
        async with self._lock:
            if not self.settings.trading_enabled:
                raise ValueError("自动实盘已被系统紧急停用")
            if order_source == "chain_test":
                await self._ensure_chain_test_buy_limits(quote, refresh_balance=True)
            if quote.source_wallet is not None:
                async with self.database.sessions() as session:
                    if await session.get(WhaleExclusion, quote.source_wallet.lower()) is not None:
                        raise ValueError("该巨鲸账户已加入排除名单，不能继续跟买")
            book = await self.client.fetch_order_book(quote.asset_id)
            if book.best_ask is None:
                raise AutoFollowQuoteRejected("市场当前没有可成交卖盘")
            if (
                quote.strategy_minimum_price is not None
                and book.best_ask < quote.strategy_minimum_price
            ):
                raise AutoFollowQuoteRejected(
                    "市场价格已经跌出自动跟单区间，不再买入",
                    observed_best_ask=book.best_ask,
                )
            if _auto_follow_price_band_changed(
                best_ask=book.best_ask,
                low_price_max_price=quote.low_price_max_price,
                selected_low=quote.selected_low_price_amount,
            ):
                raise AutoFollowQuoteRejected(
                    "市场价格已经跨越自动跟单金额档位，不再使用旧金额买入",
                    observed_best_ask=book.best_ask,
                )
            if book.best_ask > quote.worst_price or (
                quote.strategy_maximum_price is not None
                and book.best_ask > quote.strategy_maximum_price
            ):
                raise AutoFollowQuoteRejected(
                    "市场价格已经涨出自动跟单限价，不再买入",
                    observed_best_ask=book.best_ask,
                )
            if book.tick_size != quote.tick_size:
                raise ValueError("市场价格步进已变化，请重新预览")
            account = await self._account()
            trader = await self._trader(account)
            order_id = await self._create_order(
                quote=quote,
                confirmation_id=confirmation_id,
                side="BUY",
                order_source=order_source,
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
        order_source: str,
    ) -> int:
        now = utcnow()
        async with self.database.sessions() as session:
            if order_source == "auto_follow":
                settings = await session.get(WhaleSettings, 1)
                if settings is None:
                    raise ValueError("巨鲸模块尚未初始化")
                count_cap = settings.auto_follow_market_max_purchase_count
                amount_cap = settings.auto_follow_market_max_amount_usdc
                if (count_cap is None) != (amount_cap is None):
                    raise ValueError("单市场自动跟单上限配置不完整，已经停止买入")
                if count_cap is not None and amount_cap is not None:
                    prior_orders = list(
                        (
                            await session.scalars(
                                select(WhaleOrder).where(
                                    WhaleOrder.condition_id == quote.condition_id,
                                    WhaleOrder.source == "auto_follow",
                                    WhaleOrder.side == "BUY",
                                )
                            )
                        ).all()
                    )
                    used_count, used_amount = _auto_follow_market_usage(prior_orders)
                    _ensure_auto_follow_market_capacity(
                        used_count=used_count,
                        used_amount=used_amount,
                        requested_amount=quote.amount_usdc,
                        count_cap=count_cap,
                        amount_cap=amount_cap,
                    )
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
                source=order_source,
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

    async def reconcile_pending_orders(self) -> str | None:
        """Poll accepted orders before any automatic retry can submit another order."""

        if self._lock.locked():
            return None
        async with self._lock:
            async with self.database.sessions() as session:
                orders = list(
                    (
                        await session.scalars(
                            select(WhaleOrder).where(
                                WhaleOrder.status.in_(["submitted", "reconciliation_pending"]),
                                WhaleOrder.external_order_id.is_not(None),
                            )
                        )
                    ).all()
                )
            if not orders:
                return None
            try:
                trader = await self._trader()
            except Exception as error:
                return f"待确认订单暂时无法对账：{error}"
            warnings: list[str] = []
            for order in orders:
                try:
                    result = await trader.order_status(str(order.external_order_id))
                    result = await self._hydrate_fee(trader, result)
                    await self.apply_result(order.id, result)
                except Exception as error:
                    warnings.append(f"订单 #{order.id} 对账失败：{error}")
            return "；".join(warnings[:3]) or None

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

            if order.source == "auto_follow" and order.filled_size > ZERO:
                decision = await session.scalar(
                    select(WhaleAutoFollowDecision).where(
                        WhaleAutoFollowDecision.buy_order_id == order.id
                    )
                )
                if decision is not None:
                    market_lock = await session.get(WhaleAutoMarketLock, decision.condition_id)
                    requires_exit = _decision_requires_conflict_exit(decision, market_lock)
                    decision.status = "exit_pending" if requires_exit else "bought"
                    decision.reason = (
                        "买入成交后市场出现分歧，等待风控卖出"
                        if requires_exit
                        else "自动跟单买入已执行"
                    )
                    decision.processed_at = now
                    decision.updated_at = now
                    if requires_exit and market_lock is not None:
                        market_lock.exit_status = "pending"
                        market_lock.updated_at = now

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
                            source=order.source,
                            external_event_key=None,
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
                            detail=(
                                "巨鲸自动跟单买入"
                                if order.source == "auto_follow"
                                else (
                                    "链上环境测试买入"
                                    if order.source == "chain_test"
                                    else "人工跟随巨鲸买入"
                                )
                            ),
                            timestamp=now,
                        )
                    )
                else:
                    before_size = position.size
                    full_exit_requested = order.requested_size >= before_size
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
                            source=order.source,
                            external_event_key=None,
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
                            detail=(
                                "分歧市场风控卖出"
                                if order.source == "conflict_exit"
                                else (
                                    "链上环境测试卖出"
                                    if order.source == "chain_test"
                                    else "人工卖出巨鲸跟单持仓"
                                )
                            ),
                            timestamp=now,
                        )
                    )
                    if full_exit_requested:
                        await self._write_off_terminal_sell_remainder(
                            session,
                            position,
                            order,
                            now=now,
                            transaction_hash=next(
                                (
                                    fill.transaction_hash
                                    for fill in new_fills
                                    if fill.transaction_hash
                                ),
                                None,
                            ),
                        )
                position.updated_at = now
            elif position is not None and new_size <= ZERO:
                pending_status = order.status in {
                    "submitted",
                    "reconciliation_pending",
                    "live",
                    "matched",
                }
                if order.side == "BUY" and position.size <= ZERO:
                    position.status = "opening" if pending_status else "closed"
                    position.closed_at = None if pending_status else now
                    position.updated_at = now
                elif order.side == "SELL" and position.size > ZERO:
                    position.status = "closing" if pending_status else "open"
                    position.updated_at = now
            await session.commit()

    async def write_off_terminal_sell_remainder(self, position_id: int) -> bool:
        """Close a tiny remainder left by an already-terminal sell-all order."""

        async with self.database.sessions() as session:
            position = await session.get(WhaleFollowPosition, position_id)
            if position is None:
                return False
            order = await session.scalar(
                select(WhaleOrder)
                .where(
                    WhaleOrder.position_id == position_id,
                    WhaleOrder.side == "SELL",
                )
                .order_by(WhaleOrder.created_at.desc(), WhaleOrder.id.desc())
                .limit(1)
            )
            if order is None:
                return False
            transaction_hash = await session.scalar(
                select(WhaleFill.transaction_hash)
                .where(
                    WhaleFill.order_id == order.id,
                    WhaleFill.transaction_hash.is_not(None),
                )
                .order_by(WhaleFill.timestamp.desc(), WhaleFill.id.desc())
                .limit(1)
            )
            written_off = await self._write_off_terminal_sell_remainder(
                session,
                position,
                order,
                now=utcnow(),
                transaction_hash=transaction_hash,
            )
            if written_off:
                await self._complete_conflict_exit_if_flat(
                    session,
                    condition_id=position.condition_id,
                    now=position.updated_at,
                )
                await session.commit()
            return written_off

    async def reconcile_terminal_sell_remainders(self) -> int:
        """Sweep restart-safe low-value remainders before market lifecycle work."""

        async with self.database.sessions() as session:
            position_ids = list(
                (
                    await session.scalars(
                        select(WhaleFollowPosition.id).where(
                            WhaleFollowPosition.size > ZERO,
                            WhaleFollowPosition.size <= FAK_IGNORABLE_REMAINDER_USDC,
                            WhaleFollowPosition.status.in_(["opening", "open", "closing"]),
                        )
                    )
                ).all()
            )
        written_off = 0
        for position_id in position_ids:
            if await self.write_off_terminal_sell_remainder(position_id):
                written_off += 1
        return written_off

    @staticmethod
    async def _complete_conflict_exit_if_flat(
        session: Any,
        *,
        condition_id: str,
        now: datetime,
    ) -> None:
        market_lock = await session.get(WhaleAutoMarketLock, condition_id)
        if market_lock is None or market_lock.exit_status == "not_required":
            return
        decisions = list(
            (
                await session.scalars(
                    select(WhaleAutoFollowDecision)
                    .join(WhaleOrder, WhaleOrder.id == WhaleAutoFollowDecision.buy_order_id)
                    .where(
                        WhaleAutoFollowDecision.condition_id == condition_id,
                        WhaleAutoFollowDecision.status == "exit_pending",
                        WhaleOrder.filled_size > ZERO,
                    )
                )
            ).all()
        )
        target_asset_ids = list(dict.fromkeys(decision.asset_id for decision in decisions))
        remaining_query = select(func.count(WhaleFollowPosition.id)).where(
            WhaleFollowPosition.condition_id == condition_id,
            WhaleFollowPosition.size > ZERO,
            WhaleFollowPosition.status.in_(["opening", "open", "closing"]),
        )
        if target_asset_ids:
            remaining_query = remaining_query.where(
                WhaleFollowPosition.asset_id.in_(target_asset_ids)
            )
        remaining = await session.scalar(remaining_query)
        if int(remaining or 0) > 0:
            return
        market_lock.exit_status = "completed"
        market_lock.last_error = None
        market_lock.updated_at = now
        for decision in decisions:
            decision.status = "exit_completed"
            decision.reason = "分歧市场风控退出已完成"
            decision.updated_at = now

    @staticmethod
    async def _write_off_terminal_sell_remainder(
        session: Any,
        position: WhaleFollowPosition,
        order: WhaleOrder,
        *,
        now: datetime,
        transaction_hash: str | None,
    ) -> bool:
        pending_statuses = {
            "submitted",
            "reconciliation_pending",
            "live",
            "matched",
            "planned",
            "signed",
        }
        if (
            order.side != "SELL"
            or order.status in pending_statuses
            or order.filled_size <= ZERO
            or position.size <= ZERO
            or position.status not in {"opening", "open", "closing"}
            or position.size * ONE > FAK_IGNORABLE_REMAINDER_USDC
            or order.requested_size + REDEMPTION_SIZE_TOLERANCE < order.filled_size + position.size
        ):
            return False
        dust_key = hashlib.sha256(f"dust|order|{order.id}".encode()).hexdigest()
        existing = await session.scalar(
            select(WhaleFollowLedger.id).where(WhaleFollowLedger.external_event_key == dust_key)
        )
        if existing is not None:
            return False
        dust_size = position.size
        dust_cost = position.cost_usdc
        session.add(
            WhaleFollowLedger(
                position_id=position.id,
                order_id=order.id,
                type="dust_writeoff",
                source=order.source,
                external_event_key=dust_key,
                size=dust_size,
                price=None,
                amount_usdc=ZERO,
                fee_usdc=ZERO,
                realized_pnl=-dust_cost,
                transaction_hash=transaction_hash,
                detail="卖出全部后的不可交易尾差核销",
                timestamp=now,
            )
        )
        position.realized_pnl -= dust_cost
        position.size = ZERO
        position.cost_usdc = ZERO
        position.status = "closed"
        position.closed_at = now
        position.updated_at = now
        return True

    @staticmethod
    def _external_trade_key(position_id: int, trade: Any) -> str:
        identity = trade.transaction_hash or (
            f"{trade.asset_id}|{trade.side}|{trade.size}|{trade.price}|"
            f"{trade.timestamp.isoformat(timespec='microseconds')}"
        )
        return hashlib.sha256(f"manual-trade|{position_id}|{identity}".encode()).hexdigest()

    @staticmethod
    def _external_redemption_key(position_id: int, redemption: Any) -> str:
        identity = redemption.transaction_hash or (
            f"{redemption.condition_id}|{redemption.asset_id}|"
            f"{redemption.outcome_index}|{redemption.size}|{redemption.usdc_size}|"
            f"{redemption.timestamp.isoformat(timespec='microseconds')}"
        )
        return hashlib.sha256(f"external-redemption|{position_id}|{identity}".encode()).hexdigest()

    @staticmethod
    def _redemption_matches_position(redemption: Any, position: WhaleFollowPosition) -> bool:
        if redemption.condition_id != position.condition_id:
            return False
        if redemption.asset_id:
            return redemption.asset_id == position.asset_id
        if redemption.outcome_index is not None and position.outcome_index is not None:
            return redemption.outcome_index == position.outcome_index
        return bool(redemption.outcome) and (
            redemption.outcome.strip().casefold() == position.outcome.strip().casefold()
        )

    async def reconcile_external_wallet_activity(self) -> str | None:
        if self._lock.locked():
            return None
        async with self._lock:
            return await self._reconcile_external_wallet_activity()

    async def _reconcile_external_wallet_activity(self) -> str | None:
        """Adopt manual trades and Polymarket auto-redemptions for active follow cycles."""

        async with self.database.sessions() as session:
            account = await session.get(ExecutionAccount, 1)
            if (
                account is None
                or account.status not in {"ready", "insufficient_balance"}
                or not account.funder_address
                or account.signature_type != 3
                or not account.keychain_service
                or not account.keychain_account
            ):
                return None
            positions = list(
                (
                    await session.scalars(
                        select(WhaleFollowPosition).where(
                            WhaleFollowPosition.size > ZERO,
                            WhaleFollowPosition.status.in_(["open", "redeeming"]),
                            WhaleFollowPosition.opened_at.is_not(None),
                        )
                    )
                ).all()
            )
            if not positions:
                return None
            system_hashes = {
                value
                for value in (
                    await session.scalars(
                        select(WhaleFill.transaction_hash).where(
                            WhaleFill.transaction_hash.is_not(None)
                        )
                    )
                ).all()
                if value
            }
            system_hashes.update(
                value
                for value in (
                    await session.scalars(
                        select(WhaleFollowLedger.transaction_hash).where(
                            WhaleFollowLedger.order_id.is_not(None),
                            WhaleFollowLedger.transaction_hash.is_not(None),
                        )
                    )
                ).all()
                if value
            )
            known_event_keys = {
                value
                for value in (
                    await session.scalars(
                        select(WhaleFollowLedger.external_event_key).where(
                            WhaleFollowLedger.external_event_key.is_not(None)
                        )
                    )
                ).all()
                if value
            }
            markets = {
                market.condition_id: market
                for market in (
                    await session.scalars(
                        select(WhaleMarket).where(
                            WhaleMarket.condition_id.in_(
                                list(dict.fromkeys(position.condition_id for position in positions))
                            )
                        )
                    )
                ).all()
            }
            funder_address = account.funder_address

        cutoff = utcnow() - WHALE_EXTERNAL_RECONCILIATION_DELAY
        start = min(position.created_at for position in positions) - WHALE_MANUAL_ADOPTION_LOOKBACK
        condition_ids = list(dict.fromkeys(position.condition_id for position in positions))
        trades = await self.client.fetch_trades(
            funder_address,
            condition_ids=condition_ids,
            start=start,
            end=cutoff,
        )
        redemption_fetch_error: str | None = None
        try:
            redemptions = await self.client.fetch_redemptions(
                funder_address,
                start=start,
                end=cutoff,
            )
        except PolymarketAPIError as error:
            redemptions = []
            redemption_fetch_error = str(error)
        trader = await self._trader(account)
        balances = {
            position.asset_id: await trader.onchain_outcome_balance(position.asset_id)
            for position in positions
        }
        trades_by_asset: dict[str, list[Any]] = defaultdict(list)
        for trade in trades:
            trades_by_asset[trade.asset_id].append(trade)
        for asset_trades in trades_by_asset.values():
            asset_trades.sort(key=lambda trade: (trade.timestamp, trade.transaction_hash or ""))
        redemptions_by_condition: dict[str, list[Any]] = defaultdict(list)
        for redemption in redemptions:
            redemptions_by_condition[redemption.condition_id].append(redemption)
        for condition_redemptions in redemptions_by_condition.values():
            condition_redemptions.sort(
                key=lambda redemption: (
                    redemption.timestamp,
                    redemption.transaction_hash or "",
                )
            )

        warnings: list[str] = []
        if redemption_fetch_error:
            warnings.append(f"Polymarket 自动赎回流水读取失败：{redemption_fetch_error}")
        async with self.database.sessions() as session:
            for snapshot in positions:
                position = await session.get(WhaleFollowPosition, snapshot.id)
                if (
                    position is None
                    or position.size <= ZERO
                    or position.status not in {"open", "redeeming"}
                ):
                    continue
                market = markets.get(position.condition_id)
                fee_rate = market.fee_rate if market is not None else ZERO
                fee_exponent = market.fee_exponent if market is not None else ONE
                last_manual_sell: tuple[Any, str] | None = None
                candidate_trades = []
                for trade in trades_by_asset.get(position.asset_id, []):
                    if trade.transaction_hash and trade.transaction_hash in system_hashes:
                        continue
                    if trade.timestamp < position.created_at and trade.side != "BUY":
                        continue
                    event_key = self._external_trade_key(position.id, trade)
                    if event_key in known_event_keys:
                        continue
                    candidate_trades.append((trade, event_key))
                post_cycle_net = sum(
                    (
                        trade.size if trade.side == "BUY" else -trade.size
                        for trade, _ in candidate_trades
                        if trade.timestamp >= position.created_at
                    ),
                    ZERO,
                )
                pre_cycle_remaining = max(
                    ZERO,
                    balances[position.asset_id] - position.size - post_cycle_net,
                )
                for trade, event_key in candidate_trades:
                    event_size = trade.size
                    if trade.timestamp < position.created_at:
                        event_size = min(event_size, pre_cycle_remaining)
                        pre_cycle_remaining -= event_size
                        if event_size <= ZERO:
                            continue
                    fee = estimated_market_fee(
                        event_size,
                        trade.price,
                        fee_rate,
                        fee_exponent,
                    ).quantize(Decimal("0.00001"), rounding=ROUND_DOWN)
                    gross = event_size * trade.price
                    if trade.side == "BUY":
                        cost = gross + fee
                        position.size += event_size
                        position.cost_usdc += cost
                        position.lifetime_bought_size += event_size
                        position.lifetime_bought_usdc += gross
                        position.lifetime_fee_usdc += fee
                        session.add(
                            WhaleFollowLedger(
                                position_id=position.id,
                                order_id=None,
                                type="buy",
                                source="manual",
                                external_event_key=event_key,
                                size=event_size,
                                price=trade.price,
                                amount_usdc=cost,
                                fee_usdc=fee,
                                realized_pnl=ZERO,
                                transaction_hash=trade.transaction_hash,
                                detail="Polymarket 手动买入，已自动归集到跟单仓位",
                                timestamp=trade.timestamp,
                            )
                        )
                    elif position.size > ZERO:
                        before_size = position.size
                        sold = min(before_size, event_size)
                        cost = position.cost_usdc * sold / before_size
                        gross_for_position = gross * sold / event_size
                        fee_for_position = fee * sold / event_size
                        proceeds = gross_for_position - fee_for_position
                        pnl = proceeds - cost
                        position.size = max(ZERO, before_size - sold)
                        position.cost_usdc = max(ZERO, position.cost_usdc - cost)
                        position.lifetime_sold_size += sold
                        position.lifetime_sold_usdc += gross_for_position
                        position.lifetime_fee_usdc += fee_for_position
                        position.realized_pnl += pnl
                        session.add(
                            WhaleFollowLedger(
                                position_id=position.id,
                                order_id=None,
                                type="sell",
                                source="manual",
                                external_event_key=event_key,
                                size=sold,
                                price=trade.price,
                                amount_usdc=proceeds,
                                fee_usdc=fee_for_position,
                                realized_pnl=pnl,
                                transaction_hash=trade.transaction_hash,
                                detail="Polymarket 手动卖出，已自动计入统一跟单仓位",
                                timestamp=trade.timestamp,
                            )
                        )
                        last_manual_sell = (trade, event_key)
                    known_event_keys.add(event_key)
                    position.updated_at = max(position.updated_at, trade.timestamp)

                chain_balance = balances[position.asset_id]
                if chain_balance <= REDEMPTION_SIZE_TOLERANCE:
                    matching_redemption = None
                    matching_event_key = None
                    for redemption in redemptions_by_condition.get(position.condition_id, []):
                        if redemption.timestamp < position.created_at:
                            continue
                        if not self._redemption_matches_position(redemption, position):
                            continue
                        if redemption.size <= ZERO:
                            continue
                        if redemption.size + REDEMPTION_SIZE_TOLERANCE < position.size:
                            continue
                        event_key = self._external_redemption_key(position.id, redemption)
                        if event_key in known_event_keys:
                            continue
                        matching_redemption = redemption
                        matching_event_key = event_key
                        break
                    if matching_redemption is not None and matching_event_key is not None:
                        payout_rate = max(
                            ZERO,
                            min(ONE, matching_redemption.usdc_size / matching_redemption.size),
                        )
                        await self._apply_redeemed_position(
                            session,
                            position,
                            payout_usdc=position.size * payout_rate,
                            transaction_hash=matching_redemption.transaction_hash,
                            detail="Polymarket 自动赎回已完成，官方 REDEEM 流水自动对账",
                            external_event_key=matching_event_key,
                            redeemed_at=matching_redemption.timestamp,
                        )
                        known_event_keys.add(matching_event_key)
                        continue
                if abs(chain_balance - position.size) > REDEMPTION_SIZE_TOLERANCE:
                    if (
                        chain_balance <= REDEMPTION_SIZE_TOLERANCE
                        and market is not None
                        and (market.closed or market_price_is_settled(market))
                    ):
                        warnings.append(
                            f"{position.title} / {position.outcome} 本地 {position.size} 份，"
                            "链上已归零，尚未获取到对应的自动赎回流水"
                        )
                    else:
                        warnings.append(
                            f"{position.title} / {position.outcome} 本地 {position.size} 份，"
                            f"链上 {chain_balance} 份，等待外部成交数据补齐"
                        )
                    continue
                if position.size <= ZERO:
                    position.status = "closed"
                    position.closed_at = position.closed_at or utcnow()
                    position.cost_usdc = ZERO
                    continue
                if last_manual_sell is None:
                    continue
                trade, sell_event_key = last_manual_sell
                if position.size * trade.price > FAK_IGNORABLE_REMAINDER_USDC:
                    continue
                dust_size = position.size
                dust_cost = position.cost_usdc
                dust_key = hashlib.sha256(f"dust|{sell_event_key}".encode()).hexdigest()
                session.add(
                    WhaleFollowLedger(
                        position_id=position.id,
                        order_id=None,
                        type="dust_writeoff",
                        source="reconciliation",
                        external_event_key=dust_key,
                        size=dust_size,
                        price=None,
                        amount_usdc=ZERO,
                        fee_usdc=ZERO,
                        realized_pnl=-dust_cost,
                        transaction_hash=trade.transaction_hash,
                        detail="外部手动卖出后的不可交易尾差核销",
                        timestamp=trade.timestamp,
                    )
                )
                position.realized_pnl -= dust_cost
                position.size = ZERO
                position.cost_usdc = ZERO
                position.status = "closed"
                position.closed_at = trade.timestamp
                position.updated_at = max(position.updated_at, trade.timestamp)
            await session.commit()
        return "；".join(warnings[:3]) or None

    async def quote_sell(
        self,
        *,
        position_id: int,
        size: Decimal | None,
        sell_all: bool,
    ) -> WhaleSellQuote:
        if not self.settings.trading_enabled:
            raise ValueError("自动实盘已被系统紧急停用")
        await self.reconcile_external_wallet_activity()
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

    async def execute_sell(
        self,
        quote: WhaleSellQuote,
        confirmation_id: str,
        *,
        order_source: str = "follow",
    ) -> int:
        async with self._lock:
            if not self.settings.trading_enabled:
                raise ValueError("自动实盘已被系统紧急停用")
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
                    source=order_source,
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
                order = await session.get(WhaleOrder, order_id)
                if (
                    position is not None
                    and order is not None
                    and position.status == "closing"
                    and order.status
                    not in {"submitted", "reconciliation_pending", "live", "matched"}
                ):
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
        on-chain balances, so any non-whale position, in-flight redemption, or
        manually held token turns the operation into manual review.
        """

        await self._reconcile_platform_managed_resolved_losses()
        async with self.database.sessions() as session:
            whale_settings = await session.get(WhaleSettings, 1)
            account = await session.get(ExecutionAccount, 1)
            if (
                whale_settings is None
                or not whale_settings.auto_redeem
                or account is None
                or not account.auto_redeem
                or account.status not in {"ready", "insufficient_balance"}
                or not account.funder_address
                or account.signature_type != 3
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

    async def _reconcile_platform_managed_resolved_losses(self) -> int:
        """Close zero-payout positions without waiting for a redemption event.

        Polymarket-managed redemption may leave losing ERC-1155 tokens in the
        wallet because they have no collateral to claim.  A closed market's
        canonical token mapping and exact 0/1 payout vector are sufficient to
        write off that economic position without submitting an on-chain action.
        """

        async with self.database.sessions() as session:
            account = await session.get(ExecutionAccount, 1)
            if account is None or account.auto_redeem:
                return 0
            positions = list(
                (
                    await session.scalars(
                        select(WhaleFollowPosition).where(
                            WhaleFollowPosition.size > ZERO,
                            WhaleFollowPosition.status == "open",
                        )
                    )
                ).all()
            )
            if not positions:
                return 0
            markets = {
                market.condition_id: market
                for market in (
                    await session.scalars(
                        select(WhaleMarket).where(
                            WhaleMarket.condition_id.in_(
                                list(dict.fromkeys(position.condition_id for position in positions))
                            )
                        )
                    )
                ).all()
            }
            now = utcnow()
            reconciled = 0
            for position in positions:
                market = markets.get(position.condition_id)
                if market is None or not market.closed:
                    continue
                token_ids = [str(value) for value in _json_list(market.clob_token_ids_json)]
                raw_payouts = _json_list(market.outcome_prices_json)
                if not token_ids or len(token_ids) != len(raw_payouts):
                    continue
                try:
                    outcome_index = token_ids.index(position.asset_id)
                    payouts = [Decimal(str(value)) for value in raw_payouts]
                except (DecimalException, ValueError, TypeError):
                    continue
                if (
                    payouts[outcome_index] != ZERO
                    or ONE not in payouts
                    or any(
                        not payout.is_finite() or payout < ZERO or payout > ONE
                        for payout in payouts
                    )
                ):
                    continue
                size = position.size
                cost = position.cost_usdc
                event_key = hashlib.sha256(
                    f"resolved-loss|{position.id}|{position.condition_id}|{position.asset_id}".encode()
                ).hexdigest()
                session.add(
                    WhaleFollowLedger(
                        position_id=position.id,
                        order_id=None,
                        type="resolved_loss",
                        source="reconciliation",
                        external_event_key=event_key,
                        size=size,
                        price=ZERO,
                        amount_usdc=ZERO,
                        fee_usdc=ZERO,
                        realized_pnl=-cost,
                        transaction_hash=None,
                        detail="Polymarket 市场已结算为败方，零价值仓位自动核销（未提交链上赎回）",
                        timestamp=now,
                    )
                )
                position.realized_pnl -= cost
                position.size = ZERO
                position.cost_usdc = ZERO
                position.status = "resolved_loss"
                position.closed_at = now
                position.updated_at = max(position.updated_at, now)
                reconciled += 1
            if reconciled:
                await session.commit()
            return reconciled

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

        active_execution: RedemptionExecution | None = None
        async with self.database.sessions() as session:
            active_execution = await session.scalar(
                select(RedemptionExecution)
                .where(
                    RedemptionExecution.wallet_address == (account.funder_address or ""),
                    RedemptionExecution.condition_id == condition_id,
                    RedemptionExecution.status.in_(
                        ["pending", "submitting", "submitted", "manual_review", "completed"]
                    ),
                )
                .order_by(RedemptionExecution.id.desc())
                .limit(1)
            )
        if active_execution is not None:
            await self._mark_redemption_review(
                [position.id for position in positions],
                "同 condition 已存在赎回执行，已阻止重复提交",
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
        external_event_key: str | None = None,
        redeemed_at: datetime | None = None,
    ) -> None:
        async with self.database.sessions() as session:
            position = await session.get(WhaleFollowPosition, position_id)
            if position is None or position.size <= ZERO:
                return
            await self._apply_redeemed_position(
                session,
                position,
                payout_usdc=payout_usdc,
                transaction_hash=transaction_hash,
                detail=detail,
                external_event_key=external_event_key,
                redeemed_at=redeemed_at,
            )
            await session.commit()

    async def _apply_redeemed_position(
        self,
        session: Any,
        position: WhaleFollowPosition,
        *,
        payout_usdc: Decimal,
        transaction_hash: str | None,
        detail: str,
        external_event_key: str | None = None,
        redeemed_at: datetime | None = None,
    ) -> None:
        redemption = await session.scalar(
            select(WhaleRedemption).where(WhaleRedemption.position_id == position.id)
        )
        now = utcnow()
        ledger_time = redeemed_at or now
        if redemption is None:
            redemption = WhaleRedemption(
                position_id=position.id,
                size=position.size,
                attempts=0,
                created_at=now,
                updated_at=now,
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
                source="auto_redeem",
                external_event_key=external_event_key,
                size=size,
                price=None,
                amount_usdc=payout_usdc,
                fee_usdc=ZERO,
                realized_pnl=pnl,
                transaction_hash=transaction_hash,
                detail=detail,
                timestamp=ledger_time,
            )
        )
        position.realized_pnl += pnl
        position.size = ZERO
        position.cost_usdc = ZERO
        position.status = "resolved_loss" if payout_usdc <= ZERO else "redeemed"
        position.closed_at = ledger_time
        position.updated_at = max(position.updated_at, ledger_time)
        redemption.status = "completed"
        redemption.size = size
        redemption.payout_usdc = payout_usdc
        redemption.transaction_hash = transaction_hash
        redemption.last_error = None
        redemption.updated_at = now


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


async def whale_exclusion_addresses(database: Database) -> set[str]:
    async with database.sessions() as session:
        return {
            address.lower()
            for address in (await session.scalars(select(WhaleExclusion.proxy_wallet))).all()
        }


async def list_whale_exclusions(database: Database) -> dict[str, Any]:
    async with database.sessions() as session:
        exclusions = list(
            (
                await session.scalars(
                    select(WhaleExclusion).order_by(
                        WhaleExclusion.created_at.desc(),
                        WhaleExclusion.proxy_wallet.asc(),
                    )
                )
            ).all()
        )
        addresses = [row.proxy_wallet.lower() for row in exclusions]
        if not addresses:
            return {"total": 0, "items": []}
        wallets = {
            row.proxy_wallet.lower(): row
            for row in (
                await session.scalars(
                    select(WhaleWallet).where(func.lower(WhaleWallet.proxy_wallet).in_(addresses))
                )
            ).all()
        }
        counts = {
            address: int(count)
            for address, count in (
                await session.execute(
                    select(func.lower(WhaleEntry.proxy_wallet), func.count(WhaleEntry.id))
                    .where(func.lower(WhaleEntry.proxy_wallet).in_(addresses))
                    .group_by(func.lower(WhaleEntry.proxy_wallet))
                )
            ).all()
        }
    items = []
    for exclusion in exclusions:
        address = exclusion.proxy_wallet.lower()
        wallet = wallets.get(address)
        fallback = f"{address[:6]}…{address[-4:]}"
        items.append(
            {
                "proxy_wallet": address,
                "display_name": exclusion.label
                or (wallet.display_name if wallet is not None else None)
                or fallback,
                "profile_url": f"https://polymarket.com/profile/{address}",
                "hidden_entry_count": counts.get(address, 0),
                "created_at": exclusion.created_at,
            }
        )
    return {"total": len(items), "items": items}


async def whale_settings_read(database: Database) -> dict[str, Any]:
    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        if settings is None:
            raise ValueError("巨鲸模块尚未初始化")
        values = {
            column.name: getattr(settings, column.name)
            for column in WhaleSettings.__table__.columns
        }
        values["new_account_auto_follow_categories"] = _json_list(
            values.pop("new_account_auto_follow_categories_json")
        )
        values["large_amount_auto_follow_categories"] = _json_list(
            values.pop("large_amount_auto_follow_categories_json")
        )
        values.update(
            {
                "tracked_trade_count": int(
                    await session.scalar(
                        select(func.count(WhaleTrade.id)).where(
                            _not_excluded_wallet(WhaleTrade.proxy_wallet)
                        )
                    )
                    or 0
                ),
                "entry_count": int(
                    await session.scalar(
                        select(func.count(WhaleEntry.id)).where(
                            _not_excluded_wallet(WhaleEntry.proxy_wallet)
                        )
                    )
                    or 0
                ),
                "market_count": int(
                    await session.scalar(
                        select(func.count(func.distinct(WhaleEntry.condition_id))).where(
                            _not_excluded_wallet(WhaleEntry.proxy_wallet)
                        )
                    )
                    or 0
                ),
                "new_account_active_count": int(
                    await session.scalar(
                        select(func.count(WhaleEntryRuleState.id))
                        .join(WhaleEntry, WhaleEntry.id == WhaleEntryRuleState.entry_id)
                        .where(
                            WhaleEntryRuleState.rule_type == NEW_ACCOUNT_RULE,
                            WhaleEntryRuleState.active.is_(True),
                            _not_excluded_wallet(WhaleEntry.proxy_wallet),
                        )
                    )
                    or 0
                ),
                "new_account_history_count": int(
                    await session.scalar(
                        select(func.count(WhaleEntryRuleState.id))
                        .join(WhaleEntry, WhaleEntry.id == WhaleEntryRuleState.entry_id)
                        .where(
                            WhaleEntryRuleState.rule_type == NEW_ACCOUNT_RULE,
                            WhaleEntryRuleState.active.is_(False),
                            _not_excluded_wallet(WhaleEntry.proxy_wallet),
                        )
                    )
                    or 0
                ),
                "large_amount_active_count": int(
                    await session.scalar(
                        select(func.count(WhaleEntryRuleState.id))
                        .join(WhaleEntry, WhaleEntry.id == WhaleEntryRuleState.entry_id)
                        .where(
                            WhaleEntryRuleState.rule_type == LARGE_AMOUNT_RULE,
                            WhaleEntryRuleState.active.is_(True),
                            _not_excluded_wallet(WhaleEntry.proxy_wallet),
                        )
                    )
                    or 0
                ),
                "large_amount_history_count": int(
                    await session.scalar(
                        select(func.count(WhaleEntryRuleState.id))
                        .join(WhaleEntry, WhaleEntry.id == WhaleEntryRuleState.entry_id)
                        .where(
                            WhaleEntryRuleState.rule_type == LARGE_AMOUNT_RULE,
                            WhaleEntryRuleState.active.is_(False),
                            _not_excluded_wallet(WhaleEntry.proxy_wallet),
                        )
                    )
                    or 0
                ),
            }
        )
        return values


async def list_whale_auto_decisions(
    database: Database,
    *,
    rule: str = "all",
    status: str = "all",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    if rule not in {"all", NEW_ACCOUNT_RULE, LARGE_AMOUNT_RULE}:
        raise ValueError("自动跟单规则筛选无效")
    async with database.sessions() as session:
        decisions = list(
            (
                await session.scalars(
                    select(WhaleAutoFollowDecision).order_by(
                        WhaleAutoFollowDecision.created_at.desc(),
                        WhaleAutoFollowDecision.id.desc(),
                    )
                )
            ).all()
        )
        markets = (
            {
                row.condition_id: row
                for row in (
                    await session.scalars(
                        select(WhaleMarket).where(
                            WhaleMarket.condition_id.in_(
                                list(dict.fromkeys(row.condition_id for row in decisions))
                            )
                        )
                    )
                ).all()
            }
            if decisions
            else {}
        )
        followed_counts = {
            asset_id: int(count)
            for asset_id, count in (
                await session.execute(
                    select(
                        WhaleAutoFollowDecision.asset_id,
                        func.count(WhaleAutoFollowDecision.id),
                    )
                    .join(
                        WhaleOrder,
                        WhaleOrder.id == WhaleAutoFollowDecision.buy_order_id,
                    )
                    .where(WhaleOrder.filled_size > ZERO)
                    .group_by(WhaleAutoFollowDecision.asset_id)
                )
            ).all()
        }
    filtered: list[WhaleAutoFollowDecision] = []
    for decision in decisions:
        matched_rules = [str(value) for value in _json_list(decision.matched_rules_json)]
        if rule != "all" and rule not in matched_rules:
            continue
        if status != "all" and decision.status != status:
            continue
        filtered.append(decision)
    total = len(filtered)
    items = []
    for decision in filtered[offset : offset + limit]:
        market = markets.get(decision.condition_id)
        items.append(
            {
                "id": decision.id,
                "entry_id": decision.entry_id,
                "proxy_wallet": decision.proxy_wallet,
                "asset_id": decision.asset_id,
                "condition_id": decision.condition_id,
                "title": market.title if market is not None else "未命名市场",
                "market_slug": market.market_slug if market is not None else None,
                "event_slug": market.event_slug if market is not None else None,
                "outcome": decision.outcome,
                "matched_rules": [str(value) for value in _json_list(decision.matched_rules_json)],
                "selected_rule": decision.selected_rule,
                "category": decision.category,
                "category_label": WHALE_STATISTICS_CATEGORY_LABELS.get(decision.category, "其他"),
                "configured_amount_usdc": decision.configured_amount_usdc,
                "configured_min_price": decision.configured_min_price,
                "configured_max_price": decision.configured_max_price,
                "selected_amount_usdc": decision.selected_amount_usdc,
                "observed_best_ask": decision.observed_best_ask,
                "status": decision.status,
                "reason": _auto_follow_reason_display(decision.reason),
                "buy_order_id": decision.buy_order_id,
                "latest_sell_order_id": decision.latest_sell_order_id,
                "followed_wallet_count": followed_counts.get(decision.asset_id, 0),
                "processed_at": decision.processed_at,
                "created_at": decision.created_at,
                "updated_at": decision.updated_at,
            }
        )
    return {"total": total, "items": items}


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
    rule: str = NEW_ACCOUNT_RULE,
) -> dict[str, Any]:
    if rule not in WHALE_RULES:
        raise ValueError("巨鲸监控规则无效")
    now = utcnow()
    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        if settings is None:
            raise ValueError("巨鲸模块尚未初始化")
        entry_query = (
            select(WhaleEntry)
            .join(WhaleEntryRuleState, WhaleEntryRuleState.entry_id == WhaleEntry.id)
            .where(
                WhaleEntryRuleState.rule_type == rule,
                WhaleEntryRuleState.active.is_(True),
                _not_excluded_wallet(WhaleEntry.proxy_wallet),
            )
        )
        if condition_id:
            entry_query = entry_query.where(WhaleEntry.condition_id == condition_id)
        entries = list((await session.scalars(entry_query)).all())
        if not include_exited:
            entries = [
                entry for entry in entries if entry.status != "exited" and entry.net_size > ZERO
            ]
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
        rule_state_rows = list(
            (
                await session.scalars(
                    select(WhaleEntryRuleState).where(
                        WhaleEntryRuleState.entry_id.in_([entry.id for entry in entries])
                    )
                )
            ).all()
        )
        raw_trades = (
            list(
                (
                    await session.scalars(
                        select(WhaleTrade)
                        .where(
                            WhaleTrade.timestamp >= now - WHALE_TRADE_WINDOW,
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
    states_by_entry: dict[int, list[WhaleEntryRuleState]] = defaultdict(list)
    for state in rule_state_rows:
        states_by_entry[state.entry_id].append(state)

    cards: list[dict[str, Any]] = []
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
            entry_states = states_by_entry.get(entry.id, [])
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
                    "wallet_avatar_url": profile.profile_image_url if profile is not None else None,
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
                    "net_size": entry.net_size,
                    "current_value_usdc": (
                        entry.net_size * current_price
                        if 0 <= entry.outcome_index < len(prices)
                        else None
                    ),
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
                    "matched_rules": sorted(state.rule_type for state in entry_states),
                    "first_triggered_at": min(
                        (state.first_triggered_at for state in entry_states),
                        default=entry.computed_at,
                    ),
                    "last_qualified_at": max(
                        (state.last_qualified_at for state in entry_states),
                        default=entry.computed_at,
                    ),
                    "follow_eligible": entry.follow_eligible,
                    "follow_ineligible_reason": entry.follow_ineligible_reason,
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
        "window_start": now - WHALE_TRADE_WINDOW,
        "stale": stale,
        "total": total_cards,
        "items": cards[offset : offset + limit],
    }


async def list_whale_history(
    database: Database,
    *,
    rule: str,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    if rule not in WHALE_RULES:
        raise ValueError("巨鲸监控规则无效")
    now = utcnow()
    async with database.sessions() as session:
        state_query = (
            select(WhaleEntryRuleState)
            .join(WhaleEntry, WhaleEntry.id == WhaleEntryRuleState.entry_id)
            .where(
                WhaleEntryRuleState.rule_type == rule,
                WhaleEntryRuleState.active.is_(False),
                _not_excluded_wallet(WhaleEntry.proxy_wallet),
            )
            .order_by(
                WhaleEntryRuleState.inactive_at.desc(),
                WhaleEntryRuleState.last_qualified_at.desc(),
                WhaleEntryRuleState.id.desc(),
            )
        )
        total = int(
            await session.scalar(
                select(func.count(WhaleEntryRuleState.id))
                .join(WhaleEntry, WhaleEntry.id == WhaleEntryRuleState.entry_id)
                .where(
                    WhaleEntryRuleState.rule_type == rule,
                    WhaleEntryRuleState.active.is_(False),
                    _not_excluded_wallet(WhaleEntry.proxy_wallet),
                )
            )
            or 0
        )
        states = list((await session.scalars(state_query.offset(offset).limit(limit))).all())
        entry_ids = [state.entry_id for state in states]
        entries = {
            row.id: row
            for row in (
                await session.scalars(select(WhaleEntry).where(WhaleEntry.id.in_(entry_ids)))
            ).all()
        }
        all_states: dict[int, list[WhaleEntryRuleState]] = defaultdict(list)
        for state in (
            await session.scalars(
                select(WhaleEntryRuleState).where(WhaleEntryRuleState.entry_id.in_(entry_ids))
            )
        ).all():
            all_states[state.entry_id].append(state)
        wallet_ids = list(dict.fromkeys(row.proxy_wallet for row in entries.values()))
        wallets = {
            row.proxy_wallet: row
            for row in (
                await session.scalars(
                    select(WhaleWallet).where(WhaleWallet.proxy_wallet.in_(wallet_ids))
                )
            ).all()
        }
        condition_ids = list(dict.fromkeys(row.condition_id for row in entries.values()))
        markets = {
            row.condition_id: row
            for row in (
                await session.scalars(
                    select(WhaleMarket).where(WhaleMarket.condition_id.in_(condition_ids))
                )
            ).all()
        }

    items: list[dict[str, Any]] = []
    for state in states:
        entry = entries.get(state.entry_id)
        if entry is None:
            continue
        wallet = wallets.get(entry.proxy_wallet)
        market = markets.get(entry.condition_id)
        hold_pnl = (
            entry.gross_buy_size * entry.settlement_price - entry.gross_buy_usdc
            if entry.settlement_price is not None
            else None
        )
        items.append(
            {
                "entry_id": entry.id,
                "rule_type": state.rule_type,
                "matched_rules": sorted(item.rule_type for item in all_states[entry.id]),
                "proxy_wallet": entry.proxy_wallet,
                "display_name": wallet.display_name if wallet is not None else None,
                "wallet_created_at": wallet.profile_created_at if wallet is not None else None,
                "wallet_age_days": _wallet_age_days(
                    wallet.profile_created_at if wallet is not None else None,
                    now=now,
                ),
                "title": market.title if market is not None else "未命名市场",
                "outcome": entry.outcome,
                "market_slug": market.market_slug if market is not None else None,
                "event_slug": market.event_slug if market is not None else None,
                "gross_buy_usdc": entry.gross_buy_usdc,
                "gross_buy_size": entry.gross_buy_size,
                "avg_buy_price": entry.avg_buy_price,
                "net_size": entry.net_size,
                "first_buy_at": entry.first_buy_at,
                "first_triggered_at": state.first_triggered_at,
                "last_qualified_at": state.last_qualified_at,
                "inactive_at": state.inactive_at,
                "inactive_reason": state.inactive_reason,
                "threshold_usdc_snapshot": state.threshold_usdc_snapshot,
                "registration_days_snapshot": state.registration_days_snapshot,
                "settlement_price": entry.settlement_price,
                "settled_at": entry.settled_at,
                "hold_to_settlement_pnl_usdc": hold_pnl,
            }
        )
    return {"total": total, "items": items}


def _whale_statistics_range_start(range_name: str, *, now: datetime) -> datetime | None:
    if range_name not in WHALE_STATISTICS_RANGES:
        raise ValueError("巨鲸统计时间范围无效")
    duration = WHALE_STATISTICS_RANGES[range_name]
    return now - duration if duration is not None else None


def _whale_statistics_result(settlement_price: Decimal | None) -> str:
    if settlement_price is None:
        return "pending"
    if settlement_price == ONE:
        return "hit"
    if settlement_price == ZERO:
        return "miss"
    return "special"


def _whale_statistics_in_amount_band(amount: Decimal, band: str) -> bool:
    if band == "all":
        return True
    for key, _label, lower, upper in WHALE_STATISTICS_AMOUNT_BANDS:
        if key != band:
            continue
        return amount >= lower and (upper is None or amount < upper)
    raise ValueError("巨鲸统计金额分层无效")


def _whale_statistics_classification(
    tags: str | list[Any] | tuple[Any, ...] | None,
) -> dict[str, str | None]:
    slugs = {
        str(tag.get("slug") or "").strip().lower()
        for tag in _json_list(tags)
        if isinstance(tag, dict) and tag.get("slug")
    }
    if "esports" in slugs:
        category = "esports"
    elif "sports" in slugs:
        category = "sports"
    elif "politics" in slugs:
        category = "politics"
    elif "crypto" in slugs:
        category = "crypto"
    elif slugs & WHALE_STATISTICS_SCIENCE_TECH_TAGS:
        category = "science_tech"
    elif slugs & WHALE_STATISTICS_ENTERTAINMENT_TAGS:
        category = "entertainment"
    else:
        category = "other"

    subcategory: str | None = None
    subcategory_label: str | None = None
    definitions = WHALE_STATISTICS_SUBCATEGORIES.get(category, ())
    for key, label, matching_slugs in definitions:
        if matching_slugs and slugs & matching_slugs:
            subcategory = key
            subcategory_label = label
            break
    if definitions and subcategory is None:
        subcategory, subcategory_label, _ = definitions[-1]
    return {
        "category": category,
        "category_label": WHALE_STATISTICS_CATEGORY_LABELS[category],
        "subcategory": subcategory,
        "subcategory_label": subcategory_label,
    }


def _validate_whale_statistics_category_selection(category: str, subcategory: str) -> None:
    if category != "all" and category not in WHALE_STATISTICS_CATEGORY_LABELS:
        raise ValueError("巨鲸统计主分类无效")
    if subcategory == "all":
        return
    definitions = WHALE_STATISTICS_SUBCATEGORIES.get(category)
    if definitions is None or subcategory not in {item[0] for item in definitions}:
        raise ValueError("巨鲸统计子分类无效")


def _filter_whale_statistics_category(
    records: list[dict[str, Any]],
    *,
    category: str,
    subcategory: str,
) -> list[dict[str, Any]]:
    return [
        item
        for item in records
        if (category == "all" or item["category"] == category)
        and (subcategory == "all" or item["subcategory"] == subcategory)
    ]


async def _load_whale_statistics_records(
    database: Database,
    *,
    range_start: datetime | None = None,
    include_pending: bool = True,
) -> list[dict[str, Any]]:
    async with database.sessions() as session:
        entry_query = select(WhaleEntry).where(_not_excluded_wallet(WhaleEntry.proxy_wallet))
        if range_start is not None:
            range_filter = WhaleEntry.settled_at >= range_start
            entry_query = entry_query.where(
                or_(range_filter, WhaleEntry.settlement_price.is_(None))
                if include_pending
                else range_filter
            )
        elif not include_pending:
            entry_query = entry_query.where(WhaleEntry.settlement_price.is_not(None))
        entries = list((await session.scalars(entry_query)).all())
        if not entries:
            return []
        entry_ids = [entry.id for entry in entries]
        states = list(
            (
                await session.scalars(
                    select(WhaleEntryRuleState).where(WhaleEntryRuleState.entry_id.in_(entry_ids))
                )
            ).all()
        )
        wallet_ids = list(dict.fromkeys(entry.proxy_wallet for entry in entries))
        condition_ids = list(dict.fromkeys(entry.condition_id for entry in entries))
        wallets = {
            row.proxy_wallet: row
            for row in (
                await session.scalars(
                    select(WhaleWallet).where(WhaleWallet.proxy_wallet.in_(wallet_ids))
                )
            ).all()
        }
        markets = {
            row.condition_id: row
            for row in (
                await session.scalars(
                    select(WhaleMarket).where(WhaleMarket.condition_id.in_(condition_ids))
                )
            ).all()
        }

    states_by_entry: dict[int, list[WhaleEntryRuleState]] = defaultdict(list)
    for state in states:
        states_by_entry[state.entry_id].append(state)

    records: list[dict[str, Any]] = []
    for entry in entries:
        entry_states = states_by_entry.get(entry.id, [])
        if not entry_states:
            continue
        first_triggered_at = min(state.first_triggered_at for state in entry_states)
        settlement_price = (
            _decimal(entry.settlement_price) if entry.settlement_price is not None else None
        )
        result = _whale_statistics_result(settlement_price)
        theoretical_payout = (
            entry.gross_buy_size * settlement_price if settlement_price is not None else None
        )
        theoretical_pnl = (
            theoretical_payout - entry.gross_buy_usdc if theoretical_payout is not None else None
        )
        wallet = wallets.get(entry.proxy_wallet)
        market = markets.get(entry.condition_id)
        classification = _whale_statistics_classification(
            market.tags_json if market is not None else None
        )
        wallet_created_at = wallet.profile_created_at if wallet is not None else None
        wallet_age_days_at_trigger = (
            max(0, int((first_triggered_at - wallet_created_at).total_seconds() // 86400))
            if wallet_created_at is not None
            else None
        )
        records.append(
            {
                "entry_id": entry.id,
                "result": result,
                "matched_rules": sorted({state.rule_type for state in entry_states}),
                "proxy_wallet": entry.proxy_wallet,
                "display_name": wallet.display_name if wallet is not None else None,
                "profile_url": f"https://polymarket.com/profile/{entry.proxy_wallet}",
                "wallet_created_at": wallet_created_at,
                "wallet_age_days_at_trigger": wallet_age_days_at_trigger,
                "condition_id": entry.condition_id,
                "title": market.title if market is not None else "未命名市场",
                "outcome": entry.outcome,
                "market_slug": market.market_slug if market is not None else None,
                "event_slug": market.event_slug if market is not None else None,
                "polymarket_url": _market_url(market)
                if market is not None
                else "https://polymarket.com",
                **classification,
                "gross_buy_usdc": entry.gross_buy_usdc,
                "gross_buy_size": entry.gross_buy_size,
                "avg_buy_price": entry.avg_buy_price,
                "settlement_price": settlement_price,
                "theoretical_payout_usdc": theoretical_payout,
                "theoretical_pnl_usdc": theoretical_pnl,
                "theoretical_roi_percent": (
                    theoretical_pnl / entry.gross_buy_usdc * HUNDRED
                    if theoretical_pnl is not None and entry.gross_buy_usdc > ZERO
                    else None
                ),
                "first_triggered_at": first_triggered_at,
                "settled_at": entry.settled_at,
            }
        )
    return records


def _whale_statistics_group(records: list[dict[str, Any]], group: str) -> list[dict[str, Any]]:
    if group == "overall":
        return records
    if group == "dual_match":
        return [
            item
            for item in records
            if NEW_ACCOUNT_RULE in item["matched_rules"]
            and LARGE_AMOUNT_RULE in item["matched_rules"]
        ]
    if group in WHALE_RULES:
        return [item for item in records if group in item["matched_rules"]]
    raise ValueError("巨鲸统计规则无效")


def _whale_statistics_metrics(
    settled_records: list[dict[str, Any]],
    *,
    pending_count: int = 0,
) -> dict[str, Any]:
    hits = sum(item["result"] == "hit" for item in settled_records)
    misses = sum(item["result"] == "miss" for item in settled_records)
    specials = sum(item["result"] == "special" for item in settled_records)
    effective = hits + misses
    cost = sum((_decimal(item["gross_buy_usdc"]) for item in settled_records), ZERO)
    shares = sum((_decimal(item["gross_buy_size"]) for item in settled_records), ZERO)
    payout = sum((_decimal(item["theoretical_payout_usdc"]) for item in settled_records), ZERO)
    pnl = payout - cost
    hit_rate = Decimal(hits) / Decimal(effective) * HUNDRED if effective else None
    weighted_price = cost / shares if shares > ZERO else None
    break_even = weighted_price * HUNDRED if weighted_price is not None else None
    return {
        "settled_count": len(settled_records),
        "effective_sample_count": effective,
        "hit_count": hits,
        "miss_count": misses,
        "special_count": specials,
        "pending_count": pending_count,
        "hit_rate_percent": hit_rate,
        "theoretical_cost_usdc": cost,
        "theoretical_payout_usdc": payout,
        "theoretical_pnl_usdc": pnl,
        "theoretical_roi_percent": pnl / cost * HUNDRED if cost > ZERO else None,
        "weighted_avg_buy_price": weighted_price,
        "break_even_rate_percent": break_even,
        "edge_percentage_points": (
            hit_rate - break_even if hit_rate is not None and break_even is not None else None
        ),
        "wallet_count": len({str(item["proxy_wallet"]) for item in settled_records}),
        "market_count": len({str(item["condition_id"]) for item in settled_records}),
    }


def _whale_statistics_trend_key(settled_at: datetime, range_name: str) -> tuple[str, str]:
    local = settled_at.replace(tzinfo=UTC).astimezone(BEIJING_TIMEZONE)
    if range_name == "all":
        key = local.strftime("%Y-%m")
        return key, key
    if range_name == "90d":
        week_start = local.date() - timedelta(days=local.weekday())
        key = week_start.isoformat()
        return key, week_start.strftime("%m-%d")
    key = local.date().isoformat()
    return key, local.strftime("%m-%d")


async def whale_statistics(
    database: Database,
    *,
    range_name: str = "all",
    category: str = "all",
    subcategory: str = "all",
) -> dict[str, Any]:
    _validate_whale_statistics_category_selection(category, subcategory)
    now = utcnow()
    range_start = _whale_statistics_range_start(range_name, now=now)
    async with database.sessions() as session:
        coverage_start = await session.scalar(
            select(func.min(WhaleEntryRuleState.first_triggered_at))
            .join(WhaleEntry, WhaleEntry.id == WhaleEntryRuleState.entry_id)
            .where(_not_excluded_wallet(WhaleEntry.proxy_wallet))
        )
    all_records = await _load_whale_statistics_records(
        database,
        range_start=range_start,
        include_pending=True,
    )
    all_settled = [
        item
        for item in all_records
        if item["result"] != "pending"
        and item["settled_at"] is not None
        and (range_start is None or item["settled_at"] >= range_start)
        and item["settled_at"] <= now
    ]
    settled = _filter_whale_statistics_category(
        all_settled,
        category=category,
        subcategory=subcategory,
    )
    filtered_records = _filter_whale_statistics_category(
        all_records,
        category=category,
        subcategory=subcategory,
    )

    metrics_by_group: dict[str, dict[str, Any]] = {}
    for group in ("overall", NEW_ACCOUNT_RULE, LARGE_AMOUNT_RULE, "dual_match"):
        group_settled = _whale_statistics_group(settled, group)
        group_pending = _whale_statistics_group(
            [item for item in filtered_records if item["result"] == "pending"], group
        )
        metrics_by_group[group] = _whale_statistics_metrics(
            group_settled,
            pending_count=len(group_pending),
        )

    trend_groups: dict[str, dict[str, Any]] = {}
    for item in settled:
        key, label = _whale_statistics_trend_key(item["settled_at"], range_name)
        bucket = trend_groups.setdefault(key, {"label": label, "items": []})
        bucket["items"].append(item)
    trend = [
        {
            "key": key,
            "label": trend_groups[key]["label"],
            "metrics": _whale_statistics_metrics(trend_groups[key]["items"]),
        }
        for key in sorted(trend_groups)
    ]

    amount_bands = [
        {
            "key": key,
            "label": label,
            "metrics": _whale_statistics_metrics(
                [
                    item
                    for item in settled
                    if item["gross_buy_usdc"] >= lower
                    and (upper is None or item["gross_buy_usdc"] < upper)
                ]
            ),
        }
        for key, label, lower, upper in WHALE_STATISTICS_AMOUNT_BANDS
    ]
    pending_records = [item for item in all_records if item["result"] == "pending"]
    category_breakdown = [
        {
            "key": key,
            "label": label,
            "metrics": _whale_statistics_metrics(
                [item for item in all_settled if item["category"] == key],
                pending_count=sum(item["category"] == key for item in pending_records),
            ),
        }
        for key, label in WHALE_STATISTICS_CATEGORIES
    ]
    subcategory_breakdown = []
    if category in WHALE_STATISTICS_SUBCATEGORIES:
        subcategory_breakdown = [
            {
                "key": key,
                "label": label,
                "metrics": _whale_statistics_metrics(
                    [
                        item
                        for item in all_settled
                        if item["category"] == category and item["subcategory"] == key
                    ],
                    pending_count=sum(
                        item["category"] == category and item["subcategory"] == key
                        for item in pending_records
                    ),
                ),
            }
            for key, label, _matching_slugs in WHALE_STATISTICS_SUBCATEGORIES[category]
        ]
    return {
        "generated_at": now,
        "coverage_start": coverage_start,
        "range": range_name,
        "range_start": range_start,
        "range_end": now,
        "category": category,
        "subcategory": subcategory,
        "overall": metrics_by_group["overall"],
        "new_account": metrics_by_group[NEW_ACCOUNT_RULE],
        "large_amount": metrics_by_group[LARGE_AMOUNT_RULE],
        "dual_match": metrics_by_group["dual_match"],
        "trend": trend,
        "amount_bands": amount_bands,
        "category_breakdown": category_breakdown,
        "subcategory_breakdown": subcategory_breakdown,
    }


async def list_whale_statistics_signals(
    database: Database,
    *,
    range_name: str = "all",
    rule: str = "all",
    result: str = "all",
    amount_band: str = "all",
    category: str = "all",
    subcategory: str = "all",
    sort: str = "settled_desc",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    _validate_whale_statistics_category_selection(category, subcategory)
    if rule not in {"all", NEW_ACCOUNT_RULE, LARGE_AMOUNT_RULE, "both"}:
        raise ValueError("巨鲸统计规则无效")
    if result not in {"all", "hit", "miss", "special"}:
        raise ValueError("巨鲸统计结果无效")
    if sort not in {"settled_desc", "amount_desc", "pnl_desc", "pnl_asc"}:
        raise ValueError("巨鲸统计排序无效")
    _whale_statistics_in_amount_band(ZERO, amount_band)
    now = utcnow()
    range_start = _whale_statistics_range_start(range_name, now=now)
    records = [
        item
        for item in await _load_whale_statistics_records(
            database,
            range_start=range_start,
            include_pending=False,
        )
        if item["result"] != "pending"
        and item["settled_at"] is not None
        and (range_start is None or item["settled_at"] >= range_start)
        and item["settled_at"] <= now
    ]
    if rule == "both":
        records = _whale_statistics_group(records, "dual_match")
    elif rule != "all":
        records = _whale_statistics_group(records, rule)
    records = _filter_whale_statistics_category(
        records,
        category=category,
        subcategory=subcategory,
    )
    if result != "all":
        records = [item for item in records if item["result"] == result]
    records = [
        item
        for item in records
        if _whale_statistics_in_amount_band(item["gross_buy_usdc"], amount_band)
    ]
    if sort == "amount_desc":
        records.sort(key=lambda item: (item["gross_buy_usdc"], item["settled_at"]), reverse=True)
    elif sort == "pnl_desc":
        records.sort(
            key=lambda item: (item["theoretical_pnl_usdc"], item["settled_at"]),
            reverse=True,
        )
    elif sort == "pnl_asc":
        records.sort(key=lambda item: (item["theoretical_pnl_usdc"], item["settled_at"]))
    else:
        records.sort(key=lambda item: (item["settled_at"], item["entry_id"]), reverse=True)
    total = len(records)
    return {"total": total, "items": records[offset : offset + limit]}


def whale_order_payload(order: WhaleOrder) -> dict[str, Any]:
    fills = list(order.fills)
    filled_size = sum((fill.size for fill in fills), ZERO)
    filled_amount = sum((fill.amount for fill in fills), ZERO)
    return {
        "id": order.id,
        "position_id": order.position_id,
        "entry_id": order.entry_id,
        "source": order.source,
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


@dataclass(frozen=True, slots=True)
class WhaleFollowPnlSummary:
    open_cost_usdc: Decimal
    market_value_usdc: Decimal | None
    unrealized_pnl: Decimal | None
    realized_pnl: Decimal
    total_pnl: Decimal | None
    valuation_complete: bool
    unpriced_positions: int


def _whale_follow_pnl_summary(
    positions: Iterable[WhaleFollowPosition],
    ledger: Iterable[WhaleFollowLedger],
    marks: dict[int, tuple[Decimal | None, str]],
) -> WhaleFollowPnlSummary:
    position_rows = list(positions)
    open_positions = [
        position
        for position in position_rows
        if position.size > ZERO and position.status in {"opening", "open", "closing", "redeeming"}
    ]
    open_cost = sum((position.cost_usdc for position in open_positions), ZERO)
    unpriced_positions = sum(
        1 for position in open_positions if marks[position.id][1] == "unavailable"
    )
    valuation_complete = unpriced_positions == 0
    realized = sum((row.realized_pnl for row in ledger), ZERO)
    if valuation_complete:
        market_value = sum(
            (
                position.size * marks[position.id][0]
                for position in open_positions
                if marks[position.id][0] is not None
            ),
            ZERO,
        )
        unrealized = market_value - open_cost
        total = realized + unrealized
    else:
        market_value = None
        unrealized = None
        total = None
    return WhaleFollowPnlSummary(
        open_cost_usdc=open_cost,
        market_value_usdc=market_value,
        unrealized_pnl=unrealized,
        realized_pnl=realized,
        total_pnl=total,
        valuation_complete=valuation_complete,
        unpriced_positions=unpriced_positions,
    )


async def whale_follow_pnl_summary(
    database: Database,
    client: PolymarketClient,
) -> WhaleFollowPnlSummary:
    async with database.sessions() as session:
        positions = list((await session.scalars(select(WhaleFollowPosition))).all())
        ledger = list((await session.scalars(select(WhaleFollowLedger))).all())
    marks = await _position_marks(client, positions)
    return _whale_follow_pnl_summary(positions, ledger, marks)


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
        conflict_exit_position_ids = set(
            (
                await session.scalars(
                    select(WhaleFollowLedger.position_id)
                    .where(WhaleFollowLedger.source == "conflict_exit")
                    .distinct()
                )
            ).all()
        )
        chain_test_position_ids = set(
            (
                await session.scalars(
                    select(WhaleFollowLedger.position_id)
                    .where(WhaleFollowLedger.source == "chain_test")
                    .distinct()
                )
            ).all()
        )
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
                "source": row.source,
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
    pnl = _whale_follow_pnl_summary(positions, ledger, marks)
    open_positions = [
        position
        for position in positions
        if position.size > ZERO and position.status in {"opening", "open", "closing", "redeeming"}
    ]
    finished = [
        position
        for position in positions
        if position.status in {"closed", "redeemed", "resolved_loss"}
    ]
    performance_finished = [
        position
        for position in finished
        if position.id not in conflict_exit_position_ids
        and position.id not in chain_test_position_ids
    ]
    wins = sum(1 for position in performance_finished if position.realized_pnl > ZERO)
    losses = sum(1 for position in performance_finished if position.realized_pnl < ZERO)
    decided_count = wins + losses
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
    return {
        "items": items,
        "summary": {
            "total_invested_usdc": total_invested,
            "total_proceeds_usdc": total_proceeds,
            "total_fee_usdc": total_fees,
            "realized_pnl": pnl.realized_pnl,
            "unrealized_pnl": pnl.unrealized_pnl,
            "total_pnl": pnl.total_pnl,
            "open_position_count": len(open_positions),
            "closed_position_count": len(finished),
            "win_count": wins,
            "loss_count": losses,
            "excluded_conflict_exit_count": sum(
                1 for position in finished if position.id in conflict_exit_position_ids
            ),
            "excluded_chain_test_count": sum(
                1
                for position in finished
                if position.id in chain_test_position_ids
                and position.id not in conflict_exit_position_ids
            ),
            "win_rate_percent": (
                Decimal(wins) / Decimal(decided_count) * HUNDRED if decided_count else None
            ),
            "average_profit_ratio_percent": (
                sum(ratios, ZERO) / Decimal(len(ratios)) if ratios else None
            ),
        },
        "total": len(ledger),
    }
