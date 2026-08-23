from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_serializer,
    model_validator,
)

DecimalNumber = Annotated[
    Decimal,
    PlainSerializer(lambda value: float(value), return_type=float, when_used="json"),
]


def _as_utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class GlobalSettingsRead(APIModel):
    copy_ratio_percent: DecimalNumber


class GlobalSettingsUpdate(APIModel):
    copy_ratio_percent: Decimal = Field(
        ge=Decimal("1"),
        le=Decimal("100"),
        decimal_places=2,
    )


class ExecutionAccountRead(APIModel):
    wallet_id: int
    signer_address: str | None
    funder_address: str | None
    signature_type: int
    credentials_configured: bool = False
    status: str
    budget_usdc: DecimalNumber
    cash_reserve_usdc: DecimalNumber
    max_total_exposure_usdc: DecimalNumber
    daily_buy_limit_usdc: DecimalNumber
    daily_loss_limit_usdc: DecimalNumber
    auto_redeem: bool
    collateral_balance: DecimalNumber | None
    last_balance_at: datetime | None
    last_error: str | None

    @field_serializer("last_balance_at", when_used="json")
    def serialize_account_dates(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class ExecutionAccountUpdate(APIModel):
    wallet_id: int = Field(gt=0)
    signer_address: str | None = Field(default=None, max_length=42)
    funder_address: str | None = Field(default=None, max_length=42)
    signature_type: Literal[1, 3] = 3
    budget_usdc: Decimal = Field(default=Decimal("400"), ge=0)
    cash_reserve_usdc: Decimal = Field(default=Decimal("240"), ge=0)
    max_total_exposure_usdc: Decimal = Field(default=Decimal("160"), ge=0)
    daily_buy_limit_usdc: Decimal = Field(default=Decimal("80"), ge=0)
    daily_loss_limit_usdc: Decimal = Field(default=Decimal("40"), ge=0)
    auto_redeem: bool = True


class CopySubscriptionConfig(APIModel):
    copy_ratio_percent: Decimal = Field(default=Decimal("10"), gt=0, le=100)
    position_cap_usdc: Decimal = Field(default=Decimal("20"), gt=0)
    large_increase_threshold_usdc: Decimal = Field(default=Decimal("100"), gt=0)
    base_entry_threshold_usdc: Decimal = Field(default=Decimal("100"), gt=0)
    base_entry_ratio_percent: Decimal = Field(default=Decimal("10"), gt=0, le=100)
    tier_one_threshold_usdc: Decimal = Field(default=Decimal("50000"), gt=0)
    tier_one_ratio_percent: Decimal = Field(default=Decimal("0.1"), gt=0, le=100)
    tier_two_threshold_usdc: Decimal = Field(default=Decimal("100000"), gt=0)
    tier_two_ratio_percent: Decimal = Field(default=Decimal("0.2"), gt=0, le=100)
    total_exposure_cap_usdc: Decimal = Field(default=Decimal("160"), ge=0)
    market_slippage_cents: Decimal = Field(default=Decimal("5"), ge=0, le=50)

    @model_validator(mode="after")
    def validate_tiers(self) -> CopySubscriptionConfig:
        if self.tier_two_threshold_usdc <= self.tier_one_threshold_usdc:
            raise ValueError("第二档加仓阈值必须高于第一档")
        return self


class CopySubscriptionCreate(CopySubscriptionConfig):
    model_config = ConfigDict(extra="forbid")

    tracked_wallet_id: int = Field(gt=0)
    strategy_mode: Literal["normal", "large_increase"] = "normal"


class CopySubscriptionUpdate(CopySubscriptionConfig):
    model_config = ConfigDict(extra="forbid")


class CopySubscriptionAction(APIModel):
    action: Literal["close"]


class CopySubscriptionEnabledUpdate(APIModel):
    enabled: bool
    confirm_live: bool = False


class CopySubscriptionRead(APIModel):
    id: int
    tracked_wallet_id: int
    tracked_wallet_label: str | None = None
    enabled: bool = False
    state: Literal["active", "paused", "exit_only", "closing", "disabled", "error"]
    strategy_mode: Literal["normal", "large_increase"]
    copy_ratio_percent: DecimalNumber
    position_cap_usdc: DecimalNumber
    large_increase_threshold_usdc: DecimalNumber
    base_entry_threshold_usdc: DecimalNumber
    base_entry_ratio_percent: DecimalNumber
    tier_one_threshold_usdc: DecimalNumber
    tier_one_ratio_percent: DecimalNumber
    tier_two_threshold_usdc: DecimalNumber
    tier_two_ratio_percent: DecimalNumber
    total_exposure_cap_usdc: DecimalNumber
    market_slippage_cents: DecimalNumber
    baseline_event_id: int
    last_processed_event_id: int
    enabled_at: datetime | None
    last_processed_at: datetime | None
    last_error: str | None
    open_exposure_usdc: DecimalNumber = Decimal("0")
    daily_bought_usdc: DecimalNumber = Decimal("0")
    daily_realized_pnl: DecimalNumber = Decimal("0")

    @field_serializer(
        "enabled_at",
        "last_processed_at",
        when_used="json",
    )
    def serialize_subscription_dates(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class CopyPositionRead(APIModel):
    id: int
    subscription_id: int
    asset_id: str
    condition_id: str
    title: str
    outcome: str
    outcome_index: int | None
    neg_risk: bool | None
    event_slug: str | None
    settlement_date: str | None
    cycle_no: int
    attributed_size: DecimalNumber
    attributed_cost: DecimalNumber
    reserved_buy_usdc: DecimalNumber
    realized_pnl: DecimalNumber
    status: str
    redemption_status: str | None = None
    redemption_execution_provider: str | None = None
    redemption_transaction_id: str | None = None
    redemption_transaction_hash: str | None = None
    updated_at: datetime
    average_entry_price: DecimalNumber | None = None
    current_bid: DecimalNumber | None = None
    current_value: DecimalNumber | None = None
    unrealized_pnl: DecimalNumber | None = None
    unrealized_pnl_percent: DecimalNumber | None = None
    total_pnl: DecimalNumber | None = None
    lifetime_bought_size: DecimalNumber = Decimal("0")
    lifetime_bought_usdc: DecimalNumber = Decimal("0")
    lifetime_sold_size: DecimalNumber = Decimal("0")
    lifetime_sold_usdc: DecimalNumber = Decimal("0")
    lifetime_average_buy_price: DecimalNumber | None = None
    valuation_status: Literal["ok", "unavailable", "not_applicable"] = "not_applicable"
    valued_at: datetime | None = None

    @field_serializer("updated_at", "valued_at", when_used="json")
    def serialize_position_dates(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class CopyFillRead(APIModel):
    external_trade_id: str | None
    transaction_hash: str | None
    bucket_index: int | None
    settlement_status: str | None
    size: DecimalNumber
    price: DecimalNumber
    amount: DecimalNumber
    fee_usdc: DecimalNumber


class CopyOrderRead(APIModel):
    id: int
    subscription_id: int | None
    leader_event_id: int | None
    override_of_order_id: int | None = None
    asset_id: str
    side: Literal["BUY", "SELL"]
    source: Literal["copy", "rehearsal"]
    signed_order_hash: str | None
    execution_provider: str | None
    requested_size: DecimalNumber
    requested_usdc: DecimalNumber
    leader_purchase_usdc: DecimalNumber | None
    proportional_target_usdc: DecimalNumber | None
    limit_price: DecimalNumber
    reference_price: DecimalNumber | None
    filled_size: DecimalNumber
    filled_usdc: DecimalNumber
    fee_usdc: DecimalNumber
    status: str
    reason: str | None
    external_order_id: str | None
    external_trade_id: str | None
    fills: list[CopyFillRead] = Field(default_factory=list)
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def serialize_order_dates(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class CopyPortfolioSummaryRead(APIModel):
    open_cost_usdc: DecimalNumber = Decimal("0")
    market_value_usdc: DecimalNumber | None = None
    unrealized_pnl: DecimalNumber | None = None
    realized_pnl: DecimalNumber = Decimal("0")
    total_pnl: DecimalNumber | None = None
    valuation_complete: bool = True
    unpriced_positions: int = 0
    valued_at: datetime | None = None

    @field_serializer("valued_at", when_used="json")
    def serialize_valued_at(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class CopyDashboardRead(APIModel):
    live_copy_enabled: bool
    account: ExecutionAccountRead | None
    subscription: CopySubscriptionRead | None
    positions: list[CopyPositionRead]
    orders: list[CopyOrderRead]
    portfolio: CopyPortfolioSummaryRead


class CopyWorkspacePositionRead(CopyPositionRead):
    tracked_wallet_id: int
    tracked_wallet_label: str
    tracked_wallet_address: str


class CopyWorkspaceOrderRead(CopyOrderRead):
    tracked_wallet_id: int | None = None
    tracked_wallet_label: str | None = None
    tracked_wallet_address: str | None = None
    title: str | None = None
    outcome: str | None = None
    event_slug: str | None = None
    average_fill_price: DecimalNumber | None = None
    force_buy_eligible: bool = False
    force_buy_unavailable_reason: str | None = None
    force_buy_order_id: int | None = None
    force_buy_status: str | None = None


class CopyPositionsResponse(APIModel):
    items: list[CopyWorkspacePositionRead]
    portfolio: CopyPortfolioSummaryRead
    as_of: datetime

    @field_serializer("as_of", when_used="json")
    def serialize_as_of(self, value: datetime) -> str:
        return _as_utc_iso(value) or ""


class CopyOrdersResponse(APIModel):
    items: list[CopyWorkspaceOrderRead]
    next_cursor: str | None = None


class CopyActivityRead(APIModel):
    activity_id: str
    activity_type: Literal["order", "redemption"]
    source_id: int
    operation: Literal["BUY", "SELL", "REDEEM"]
    asset_id: str
    tracked_wallet_id: int | None = None
    tracked_wallet_label: str | None = None
    tracked_wallet_address: str | None = None
    title: str | None = None
    outcome: str | None = None
    event_slug: str | None = None
    requested_size: DecimalNumber = Decimal("0")
    requested_usdc: DecimalNumber = Decimal("0")
    leader_purchase_usdc: DecimalNumber | None = None
    proportional_target_usdc: DecimalNumber | None = None
    executed_size: DecimalNumber = Decimal("0")
    executed_usdc: DecimalNumber = Decimal("0")
    fee_usdc: DecimalNumber = Decimal("0")
    execution_price: DecimalNumber | None = None
    realized_pnl: DecimalNumber | None = None
    status: str
    reason: str | None = None
    execution_provider: str | None = None
    transaction_id: str | None = None
    transaction_hash: str | None = None
    fills: list[CopyFillRead] = Field(default_factory=list)
    force_buy_eligible: bool = False
    force_buy_unavailable_reason: str | None = None
    force_buy_order_id: int | None = None
    force_buy_status: str | None = None
    activity_at: datetime

    @field_serializer("activity_at", when_used="json")
    def serialize_activity_date(self, value: datetime) -> str:
        return _as_utc_iso(value) or ""


class CopyActivitiesResponse(APIModel):
    items: list[CopyActivityRead]
    next_cursor: str | None = None


class CopyOverviewPnlSourceRead(APIModel):
    realized_pnl: DecimalNumber = Decimal("0")
    unrealized_pnl: DecimalNumber | None = None
    total_pnl: DecimalNumber | None = None
    valuation_complete: bool = True
    unpriced_positions: int = 0


class CopyOverviewPnlBreakdownRead(APIModel):
    copy_trading: CopyOverviewPnlSourceRead
    whale_follow: CopyOverviewPnlSourceRead


class CopyOverviewTotalsRead(APIModel):
    collateral_balance: DecimalNumber | None = None
    available_capacity_usdc: DecimalNumber = Decimal("0")
    open_exposure_usdc: DecimalNumber = Decimal("0")
    daily_bought_usdc: DecimalNumber = Decimal("0")
    open_cost_usdc: DecimalNumber = Decimal("0")
    market_value_usdc: DecimalNumber | None = None
    unrealized_pnl: DecimalNumber | None = None
    realized_pnl: DecimalNumber = Decimal("0")
    total_pnl: DecimalNumber | None = None
    valuation_complete: bool = True
    unpriced_positions: int = 0
    pnl_breakdown: CopyOverviewPnlBreakdownRead


class CopyDailyRealizedPnlRead(APIModel):
    date: date
    realized_pnl: DecimalNumber = Decimal("0")
    bought_usdc: DecimalNumber = Decimal("0")


class CopyWalletSummaryRead(APIModel):
    id: int
    address: str
    proxy_wallet: str
    label: str
    status: str
    last_success_at: datetime | None
    last_error: str | None

    @field_serializer("last_success_at", when_used="json")
    def serialize_wallet_date(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class CopyStrategyOverviewRead(APIModel):
    subscription: CopySubscriptionRead
    wallet: CopyWalletSummaryRead
    portfolio: CopyPortfolioSummaryRead
    lifetime_bought_usdc: DecimalNumber = Decimal("0")
    lifetime_copy_order_count: int = 0
    open_positions: int
    stale: bool


class CopyOverviewRead(APIModel):
    live_copy_enabled: bool
    account: ExecutionAccountRead | None
    totals: CopyOverviewTotalsRead
    daily_realized_pnl: list[CopyDailyRealizedPnlRead]
    strategies: list[CopyStrategyOverviewRead]
    recent_orders: list[CopyWorkspaceOrderRead]
    recent_activities: list[CopyActivityRead] = Field(default_factory=list)
    as_of: datetime

    @field_serializer("as_of", when_used="json")
    def serialize_as_of(self, value: datetime) -> str:
        return _as_utc_iso(value) or ""


class RehearsalPreviewRequest(APIModel):
    market_url: str = Field(min_length=1, max_length=1000)
    outcome: str = Field(min_length=1, max_length=200)
    max_total_usdc: Decimal = Field(
        default=Decimal("1"),
        gt=0,
        le=Decimal("100"),
        decimal_places=2,
    )


class RehearsalPreviewRead(APIModel):
    confirmation_id: str
    market_url: str
    asset_id: str
    condition_id: str
    title: str
    outcome: str
    best_ask: DecimalNumber
    fee_rate_bps: int
    max_total_usdc: DecimalNumber
    expires_at: datetime


class RehearsalExecuteRequest(APIModel):
    confirmation_id: str = Field(min_length=20, max_length=200)
    confirmation_text: Literal["确认执行真实买入"]


class ForceBuyPreviewRead(APIModel):
    confirmation_id: str
    source_order_id: int
    title: str
    outcome: str
    proportional_target_usdc: DecimalNumber
    minimum_order_usdc: DecimalNumber
    minimum_adjusted: bool
    executable_usdc: DecimalNumber
    best_ask: DecimalNumber
    worst_price: DecimalNumber
    expires_at: datetime

    @field_serializer("expires_at", when_used="json")
    def serialize_expires_at(self, value: datetime) -> str:
        return _as_utc_iso(value) or ""


class ForceBuyExecuteRequest(APIModel):
    confirmation_id: str = Field(min_length=20, max_length=200)
    confirmation_text: Literal["确认强制真实买入"]


class CopyRecommendation(APIModel):
    action: Literal["buy", "sell"]
    ratio_percent: DecimalNumber
    shares: DecimalNumber
    estimated_usdc: DecimalNumber | None


class WalletCreate(APIModel):
    address: str = Field(min_length=1, max_length=500)
    label: str | None = Field(default=None, min_length=1, max_length=100)
    copy_strategy: WalletCopyStrategyCreate | None = None


class WalletCopyStrategyCreate(CopySubscriptionConfig):
    model_config = ConfigDict(extra="forbid")

    strategy_mode: Literal["normal", "large_increase"] = "normal"


class WalletUpdate(APIModel):
    label: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None


class WalletRead(APIModel):
    id: int
    address: str
    proxy_wallet: str
    label: str
    wallet_role: Literal["self", "tracked"]
    enabled: bool
    status: str
    last_success_at: datetime | None
    last_error: str | None
    created_at: datetime

    @field_serializer("last_success_at", "created_at", when_used="json")
    def serialize_dates(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class PurchaseLotRead(APIModel):
    purchase_date: date
    size: DecimalNumber
    avg_price: DecimalNumber
    initial_value: DecimalNumber
    current_value: DecimalNumber
    cash_pnl: DecimalNumber
    percent_pnl: DecimalNumber


class PositionCycleTradeRead(APIModel):
    id: int
    type: Literal["opened", "increased", "decreased"]
    size: DecimalNumber
    price: DecimalNumber
    amount: DecimalNumber
    timestamp: datetime
    transaction_hash: str | None

    @field_serializer("timestamp", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        serialized = _as_utc_iso(value)
        assert serialized is not None
        return serialized


class PositionRead(APIModel):
    wallet_id: int
    asset_id: str
    condition_id: str
    title: str
    outcome: str
    icon_url: str | None
    event_slug: str | None
    market_slug: str | None
    size: DecimalNumber
    avg_price: DecimalNumber
    current_price: DecimalNumber
    initial_value: DecimalNumber
    current_value: DecimalNumber
    cash_pnl: DecimalNumber
    percent_pnl: DecimalNumber
    end_date: datetime | None
    first_opened_at: datetime | None = None
    first_opened_at_source: Literal["trade", "first_seen"] = "first_seen"
    opened_date: date | None = None
    cycle_trades: list[PositionCycleTradeRead] = Field(default_factory=list)
    cycle_history_complete: bool = False
    purchase_lots: list[PurchaseLotRead] = Field(default_factory=list)

    @field_serializer("end_date", "first_opened_at", when_used="json")
    def serialize_position_dates(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class PositionSummary(APIModel):
    current_value: DecimalNumber
    initial_value: DecimalNumber
    cash_pnl: DecimalNumber
    count: int


class PositionsResponse(APIModel):
    items: list[PositionRead]
    summary: PositionSummary
    opened_dates: list[date] = Field(default_factory=list)
    purchase_dates: list[date] = Field(default_factory=list)
    purchase_history_complete: bool = False
    purchase_history_error: str | None = None
    as_of: datetime | None
    stale: bool

    @field_serializer("as_of", when_used="json")
    def serialize_as_of(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class PositionOverlapRead(APIModel):
    asset_id: str
    condition_id: str
    my_size: DecimalNumber
    tracked_size: DecimalNumber
    my_to_tracked_percent: DecimalNumber
    my_ratio: DecimalNumber
    tracked_ratio: DecimalNumber


class PositionOverlapsResponse(APIModel):
    my_wallet_id: int | None
    tracked_wallet_id: int
    items: list[PositionOverlapRead]
    overlap_count: int
    my_as_of: datetime | None
    tracked_as_of: datetime | None
    my_stale: bool | None
    tracked_stale: bool

    @field_serializer("my_as_of", "tracked_as_of", when_used="json")
    def serialize_dates(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class PositionOverlapDetail(APIModel):
    my_wallet: WalletRead
    tracked_wallet: WalletRead
    mine: PositionRead
    tracked: PositionRead
    my_to_tracked_percent: DecimalNumber
    my_ratio: DecimalNumber
    tracked_ratio: DecimalNumber
    my_stale: bool
    tracked_stale: bool


class PositionOverlapAlertRead(APIModel):
    id: int
    my_wallet_id: int
    tracked_wallet_id: int
    asset_id: str
    condition_id: str
    title: str
    outcome: str
    event_slug: str | None
    market_slug: str | None
    type: Literal["increased", "decreased", "closed"]
    before_size: DecimalNumber
    after_size: DecimalNumber
    delta_size: DecimalNumber
    detected_at: datetime
    created_at: datetime
    read_at: datetime | None
    copy_recommendation: CopyRecommendation

    @field_serializer("detected_at", "created_at", "read_at", when_used="json")
    def serialize_dates(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class PositionOverlapAlertsResponse(APIModel):
    items: list[PositionOverlapAlertRead]
    unread_count: int


class FillRead(APIModel):
    id: int
    side: Literal["BUY", "SELL"]
    size: DecimalNumber
    price: DecimalNumber
    amount: DecimalNumber
    timestamp: datetime
    transaction_hash: str | None

    @field_serializer("timestamp", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        serialized = _as_utc_iso(value)
        assert serialized is not None
        return serialized


class EventRead(APIModel):
    id: int
    wallet_id: int
    asset_id: str
    type: Literal["opened", "increased", "decreased", "closed", "redeemed"]
    title: str
    outcome: str
    event_slug: str | None
    delta_size: DecimalNumber
    before_size: DecimalNumber
    after_size: DecimalNumber
    before_avg_price: DecimalNumber
    after_avg_price: DecimalNumber
    average_fill_price: DecimalNumber | None
    current_value: DecimalNumber
    reconciliation_status: str
    first_detected_at: datetime
    settled_at: datetime
    payout_amount: DecimalNumber | None
    redemption_cost_basis: DecimalNumber | None = None
    redemption_entry_price: DecimalNumber | None = None
    redemption_price: DecimalNumber | None = None
    redemption_profit: DecimalNumber | None = None
    redemption_profit_percent: DecimalNumber | None = None
    redemption_cost_complete: bool | None = None
    close_cost_basis: DecimalNumber | None = None
    close_proceeds: DecimalNumber | None = None
    close_profit: DecimalNumber | None = None
    close_profit_percent: DecimalNumber | None = None
    close_profit_complete: bool = False
    transaction_hash: str | None
    fills: list[FillRead]
    copy_recommendation: CopyRecommendation | None = None

    @field_serializer("first_detected_at", "settled_at", when_used="json")
    def serialize_dates(self, value: datetime) -> str:
        serialized = _as_utc_iso(value)
        assert serialized is not None
        return serialized


class EventsResponse(APIModel):
    items: list[EventRead]
    next_cursor: str | None


class PositionEventCycleRead(APIModel):
    cycle_number: int
    status: Literal["open", "closed", "redeemed", "history_gap"]
    start_source: Literal["opened", "first_recorded"]
    history_complete: bool
    started_at: datetime
    ended_at: datetime | None
    confirmed_realized_pnl: DecimalNumber
    incomplete_profit_events: int
    events: list[EventRead]

    @field_serializer("started_at", "ended_at", when_used="json")
    def serialize_dates(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class PositionEventGroupRead(APIModel):
    wallet_id: int
    asset_id: str
    condition_id: str
    title: str
    outcome: str
    event_slug: str | None
    status: Literal["open", "closed", "redeemed", "history_gap"]
    event_count: int
    event_counts: dict[str, int]
    cycle_count: int
    first_recorded_at: datetime
    latest_recorded_at: datetime
    latest_event_id: int
    confirmed_realized_pnl: DecimalNumber
    incomplete_profit_events: int
    realized_pnl_source: Literal["polymarket", "recorded"] = "recorded"
    realized_pnl_status: Literal["confirmed", "unrealized", "unavailable"] = "confirmed"
    cycles: list[PositionEventCycleRead]

    @field_serializer("first_recorded_at", "latest_recorded_at", when_used="json")
    def serialize_dates(self, value: datetime) -> str:
        serialized = _as_utc_iso(value)
        assert serialized is not None
        return serialized


class WalletRecordedPnlRead(APIModel):
    recorded_since: datetime
    confirmed_realized_pnl: DecimalNumber
    current_unrealized_pnl: DecimalNumber
    confirmed_total_pnl: DecimalNumber
    incomplete_realized_events: int
    complete: bool
    source: Literal["polymarket", "recorded"] = "recorded"

    @field_serializer("recorded_since", when_used="json")
    def serialize_recorded_since(self, value: datetime) -> str:
        serialized = _as_utc_iso(value)
        assert serialized is not None
        return serialized


class PositionEventGroupsResponse(APIModel):
    items: list[PositionEventGroupRead]
    pnl: WalletRecordedPnlRead
    next_cursor: str | None


class HealthRead(APIModel):
    status: Literal["ok"]
    database: Literal["ok"]


class WhaleSettingsRead(APIModel):
    id: int = 1
    enabled: bool
    window_hours: int
    registration_window_days: int
    new_account_threshold_usdc: DecimalNumber
    large_amount_threshold_usdc: DecimalNumber
    collect_filter_amount_usdc: DecimalNumber
    single_trade_threshold_usdc: DecimalNumber
    cumulative_threshold_usdc: DecimalNumber
    min_liquidity_usdc: DecimalNumber
    min_remaining_minutes: int
    max_price_delta_cents: DecimalNumber
    holding_ratio_threshold: DecimalNumber
    exited_ratio_threshold: DecimalNumber
    scan_interval_seconds: int
    profile_cache_hours: int
    trade_retention_hours: int
    max_follow_amount_usdc: DecimalNumber
    default_follow_amount_usdc: DecimalNumber
    follow_slippage_cents: DecimalNumber
    sell_slippage_cents: DecimalNumber
    auto_redeem: bool
    last_scan_at: datetime | None
    last_scan_error: str | None
    consecutive_failures: int
    created_at: datetime
    updated_at: datetime
    tracked_trade_count: int = 0
    entry_count: int = 0
    market_count: int = 0
    new_account_active_count: int = 0
    new_account_history_count: int = 0
    large_amount_active_count: int = 0
    large_amount_history_count: int = 0

    @field_serializer("last_scan_at", "created_at", "updated_at", when_used="json")
    def serialize_dates(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class WhaleSettingsUpdate(APIModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    window_hours: int | None = Field(default=None, gt=0)
    registration_window_days: int | None = Field(default=None, ge=1, le=30)
    new_account_threshold_usdc: Decimal | None = Field(default=None, gt=0)
    large_amount_threshold_usdc: Decimal | None = Field(default=None, gt=0)
    collect_filter_amount_usdc: Decimal | None = Field(default=None, gt=0)
    single_trade_threshold_usdc: Decimal | None = Field(default=None, gt=0)
    cumulative_threshold_usdc: Decimal | None = Field(default=None, gt=0)
    min_liquidity_usdc: Decimal | None = Field(default=None, ge=0)
    min_remaining_minutes: int | None = Field(default=None, ge=0)
    max_price_delta_cents: Decimal | None = Field(default=None, ge=0)
    holding_ratio_threshold: Decimal | None = Field(default=None, gt=0, le=100)
    exited_ratio_threshold: Decimal | None = Field(default=None, ge=0, lt=100)
    scan_interval_seconds: int | None = Field(default=None, gt=0)
    profile_cache_hours: int | None = Field(default=None, gt=0)
    trade_retention_hours: int | None = Field(default=None, gt=0)
    max_follow_amount_usdc: Decimal | None = Field(default=None, gt=0)
    default_follow_amount_usdc: Decimal | None = Field(default=None, gt=0)
    follow_slippage_cents: Decimal | None = Field(default=None, ge=0, le=50)
    sell_slippage_cents: Decimal | None = Field(default=None, ge=0, le=50)
    auto_redeem: bool | None = None

    @model_validator(mode="after")
    def validate_thresholds(self) -> WhaleSettingsUpdate:
        collect = self.collect_filter_amount_usdc
        if (
            collect is not None
            and self.single_trade_threshold_usdc is not None
            and self.single_trade_threshold_usdc < collect
        ):
            raise ValueError("单笔重仓阈值不能低于采集金额阈值")
        if (
            collect is not None
            and self.cumulative_threshold_usdc is not None
            and self.cumulative_threshold_usdc < collect
        ):
            raise ValueError("累计重仓阈值不能低于采集金额阈值")
        if (
            collect is not None
            and self.new_account_threshold_usdc is not None
            and self.new_account_threshold_usdc < collect
        ):
            raise ValueError("新号大额门槛不能低于采集金额阈值")
        if (
            collect is not None
            and self.large_amount_threshold_usdc is not None
            and self.large_amount_threshold_usdc < collect
        ):
            raise ValueError("全量超大额门槛不能低于采集金额阈值")
        if (
            self.exited_ratio_threshold is not None
            and self.holding_ratio_threshold is not None
            and self.exited_ratio_threshold >= self.holding_ratio_threshold
        ):
            raise ValueError("退出比例阈值必须低于持有比例阈值")
        return self


class WhaleExclusionCreate(APIModel):
    model_config = ConfigDict(extra="forbid")

    address: str = Field(min_length=1, max_length=500)
    label: str | None = Field(default=None, max_length=200)


class WhaleExclusionRead(APIModel):
    proxy_wallet: str
    display_name: str
    profile_url: str
    hidden_entry_count: int
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def serialize_created_at(self, value: datetime) -> str:
        return _as_utc_iso(value) or ""


class WhaleExclusionListRead(APIModel):
    total: int
    items: list[WhaleExclusionRead]


class WhaleScanRead(APIModel):
    status: Literal["ok", "skipped"]


class WhaleRequestLogRead(APIModel):
    id: int
    scan_id: str
    status: Literal["pending", "success", "failed"]
    started_at: datetime
    finished_at: datetime | None
    method: str
    url: str
    query_params: dict[str, str | list[str]]
    http_status: int | None
    duration_ms: int | None
    error_type: str | None
    error_message: str | None
    response_excerpt: str | None

    @field_serializer("started_at", "finished_at", when_used="json")
    def serialize_dates(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class WhaleRequestLogListRead(APIModel):
    generated_at: datetime
    total: int
    items: list[WhaleRequestLogRead]

    @field_serializer("generated_at", when_used="json")
    def serialize_generated_at(self, value: datetime) -> str:
        return _as_utc_iso(value) or ""


class WhaleTagRead(APIModel):
    id: str
    slug: str
    label: str
    market_count: int = 0


class WhaleTradeRead(APIModel):
    id: int
    proxy_wallet: str
    asset_id: str
    condition_id: str
    side: Literal["BUY", "SELL"]
    size: DecimalNumber
    price: DecimalNumber
    amount: DecimalNumber
    outcome: str
    outcome_index: int
    title: str
    market_slug: str
    event_slug: str
    icon_url: str | None
    display_name: str | None
    transaction_hash: str | None
    timestamp: datetime

    @field_serializer("timestamp", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        return _as_utc_iso(value) or ""


class WhaleEntryRead(APIModel):
    entry_id: int
    proxy_wallet: str
    display_name: str | None
    wallet_avatar_url: str | None
    profile_url: str
    wallet_created_at: datetime | None
    wallet_age_days: int | None
    verified_badge: bool
    taker_tier_name: str | None
    gross_buy_usdc: DecimalNumber
    gross_buy_size: DecimalNumber
    net_size: DecimalNumber
    current_value_usdc: DecimalNumber | None
    avg_buy_price: DecimalNumber
    max_single_usdc: DecimalNumber
    trade_count: int
    first_buy_at: datetime
    last_buy_at: datetime
    status: Literal["holding", "reduced", "exited"]
    net_ratio: DecimalNumber
    hedged: bool
    price_delta_cents: DecimalNumber | None
    price_delta_percent: DecimalNumber | None
    matched_rules: list[Literal["new_account", "large_amount"]] = Field(default_factory=list)
    first_triggered_at: datetime
    last_qualified_at: datetime
    follow_eligible: bool
    follow_ineligible_reason: str | None

    @field_serializer(
        "wallet_created_at",
        "first_buy_at",
        "last_buy_at",
        "first_triggered_at",
        "last_qualified_at",
        when_used="json",
    )
    def serialize_dates(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class WhaleHistoryRead(APIModel):
    entry_id: int
    rule_type: Literal["new_account", "large_amount"]
    matched_rules: list[Literal["new_account", "large_amount"]] = Field(default_factory=list)
    proxy_wallet: str
    display_name: str | None
    wallet_created_at: datetime | None
    wallet_age_days: int | None
    title: str
    outcome: str
    market_slug: str | None
    event_slug: str | None
    gross_buy_usdc: DecimalNumber
    gross_buy_size: DecimalNumber
    avg_buy_price: DecimalNumber
    net_size: DecimalNumber
    first_buy_at: datetime
    first_triggered_at: datetime
    last_qualified_at: datetime
    inactive_at: datetime | None
    inactive_reason: str | None
    threshold_usdc_snapshot: DecimalNumber
    registration_days_snapshot: int | None
    settlement_price: DecimalNumber | None
    settled_at: datetime | None
    hold_to_settlement_pnl_usdc: DecimalNumber | None

    @field_serializer(
        "wallet_created_at",
        "first_buy_at",
        "first_triggered_at",
        "last_qualified_at",
        "inactive_at",
        "settled_at",
        when_used="json",
    )
    def serialize_history_dates(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class WhaleHistoryListRead(APIModel):
    total: int
    items: list[WhaleHistoryRead] = Field(default_factory=list)


class WhaleStatisticsMetricsRead(APIModel):
    settled_count: int
    effective_sample_count: int
    hit_count: int
    miss_count: int
    special_count: int
    pending_count: int
    hit_rate_percent: DecimalNumber | None
    theoretical_cost_usdc: DecimalNumber
    theoretical_payout_usdc: DecimalNumber
    theoretical_pnl_usdc: DecimalNumber
    theoretical_roi_percent: DecimalNumber | None
    weighted_avg_buy_price: DecimalNumber | None
    break_even_rate_percent: DecimalNumber | None
    edge_percentage_points: DecimalNumber | None
    wallet_count: int
    market_count: int


class WhaleStatisticsSliceRead(APIModel):
    key: str
    label: str
    metrics: WhaleStatisticsMetricsRead


class WhaleStatisticsRead(APIModel):
    generated_at: datetime
    coverage_start: datetime | None
    range: Literal["all", "7d", "30d", "90d"]
    range_start: datetime | None
    range_end: datetime
    overall: WhaleStatisticsMetricsRead
    new_account: WhaleStatisticsMetricsRead
    large_amount: WhaleStatisticsMetricsRead
    dual_match: WhaleStatisticsMetricsRead
    trend: list[WhaleStatisticsSliceRead] = Field(default_factory=list)
    amount_bands: list[WhaleStatisticsSliceRead] = Field(default_factory=list)

    @field_serializer(
        "generated_at",
        "coverage_start",
        "range_start",
        "range_end",
        when_used="json",
    )
    def serialize_statistics_dates(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class WhaleStatisticsSignalRead(APIModel):
    entry_id: int
    result: Literal["hit", "miss", "special"]
    matched_rules: list[Literal["new_account", "large_amount"]] = Field(default_factory=list)
    proxy_wallet: str
    display_name: str | None
    profile_url: str
    wallet_created_at: datetime | None
    wallet_age_days_at_trigger: int | None
    condition_id: str
    title: str
    outcome: str
    market_slug: str | None
    event_slug: str | None
    polymarket_url: str
    gross_buy_usdc: DecimalNumber
    gross_buy_size: DecimalNumber
    avg_buy_price: DecimalNumber
    settlement_price: DecimalNumber
    theoretical_payout_usdc: DecimalNumber
    theoretical_pnl_usdc: DecimalNumber
    theoretical_roi_percent: DecimalNumber | None
    first_triggered_at: datetime
    settled_at: datetime

    @field_serializer(
        "wallet_created_at",
        "first_triggered_at",
        "settled_at",
        when_used="json",
    )
    def serialize_signal_dates(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class WhaleStatisticsSignalListRead(APIModel):
    total: int
    items: list[WhaleStatisticsSignalRead] = Field(default_factory=list)


class WhaleEntryDetailRead(WhaleEntryRead):
    trades: list[WhaleTradeRead] = Field(default_factory=list)


class WhaleMarketSideRead(APIModel):
    outcome_index: int
    outcome: str
    asset_id: str
    current_price: DecimalNumber | None
    best_bid: DecimalNumber | None
    best_ask: DecimalNumber | None
    side_total_usdc: DecimalNumber
    side_wallet_count: int
    entries: list[WhaleEntryRead] = Field(default_factory=list)


class WhaleMarketSideDetailRead(WhaleMarketSideRead):
    entries: list[WhaleEntryDetailRead] = Field(default_factory=list)


class WhaleMarketRead(APIModel):
    condition_id: str
    title: str
    icon_url: str | None
    market_slug: str | None
    event_slug: str | None
    polymarket_url: str
    tags: list[WhaleTagRead] = Field(default_factory=list)
    end_date: datetime | None
    remaining_seconds: int | None
    end_date_is_date_only: bool
    liquidity: DecimalNumber
    volume_24h: DecimalNumber
    total_whale_usdc: DecimalNumber
    whale_wallet_count: int
    both_sides: bool
    dominant_outcome_index: int | None
    side_imbalance_ratio: DecimalNumber | None
    sides: list[WhaleMarketSideRead] = Field(default_factory=list)

    @field_serializer("end_date", when_used="json")
    def serialize_end_date(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class WhaleMarketDetailRead(WhaleMarketRead):
    sides: list[WhaleMarketSideDetailRead] = Field(default_factory=list)


class WhaleMarketListRead(APIModel):
    generated_at: datetime
    window_start: datetime
    stale: bool
    total: int
    items: list[WhaleMarketRead] = Field(default_factory=list)

    @field_serializer("generated_at", "window_start", when_used="json")
    def serialize_dates(self, value: datetime) -> str:
        return _as_utc_iso(value) or ""


class WhaleFollowPreviewRequest(APIModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(min_length=1, max_length=100)
    amount_usdc: Decimal = Field(gt=0)
    entry_id: int = Field(gt=0)


class WhaleFollowPreviewRead(APIModel):
    confirmation_id: str
    expires_at: datetime
    asset_id: str
    condition_id: str
    title: str
    outcome: str
    outcome_index: int | None
    neg_risk: bool
    amount_usdc: DecimalNumber
    best_ask: DecimalNumber
    worst_price: DecimalNumber
    tick_size: DecimalNumber
    minimum_order_usdc: DecimalNumber
    estimated_shares: DecimalNumber
    estimated_fee_usdc: DecimalNumber
    total_cost_usdc: DecimalNumber
    profit_ratio_percent: DecimalNumber
    max_loss_usdc: DecimalNumber
    winning_payout_usdc: DecimalNumber
    winning_profit_usdc: DecimalNumber
    immediate_exit_price: DecimalNumber | None = None
    immediate_exit_proceeds_usdc: DecimalNumber | None = None
    immediate_exit_fee_usdc: DecimalNumber | None = None
    immediate_exit_pnl_usdc: DecimalNumber | None = None
    immediate_exit_pnl_percent: DecimalNumber | None = None
    immediate_exit_unavailable_reason: str | None = None
    whale_avg_price: DecimalNumber | None = None
    whale_profit_ratio_percent: DecimalNumber | None = None
    profit_ratio_gap_percent: DecimalNumber | None = None
    price_delta_cents: DecimalNumber | None = None
    price_delta_warning: bool = False
    reserve_warning: bool = False
    available_balance_usdc: DecimalNumber

    @field_serializer("expires_at", when_used="json")
    def serialize_expires_at(self, value: datetime) -> str:
        return _as_utc_iso(value) or ""


class WhaleFollowExecuteRequest(APIModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_id: str = Field(min_length=1, max_length=200)
    confirmation_text: Literal["确认真实买入"]


class WhaleSellPreviewRequest(APIModel):
    model_config = ConfigDict(extra="forbid")

    size: Decimal | None = Field(default=None, gt=0)
    sell_all: bool = False

    @model_validator(mode="after")
    def validate_sell_amount(self) -> WhaleSellPreviewRequest:
        if self.sell_all and self.size is not None:
            raise ValueError("全部卖出时不能同时指定卖出份额")
        if not self.sell_all and self.size is None:
            raise ValueError("必须指定卖出份额或选择全部卖出")
        return self


class WhaleSellPreviewRead(APIModel):
    confirmation_id: str
    expires_at: datetime
    position_id: int
    size: DecimalNumber
    best_bid: DecimalNumber
    worst_price: DecimalNumber
    minimum_order_size: DecimalNumber
    estimated_proceeds_usdc: DecimalNumber
    estimated_fee_usdc: DecimalNumber
    cost_basis_usdc: DecimalNumber
    estimated_pnl_usdc: DecimalNumber
    estimated_pnl_percent: DecimalNumber | None

    @field_serializer("expires_at", when_used="json")
    def serialize_expires_at(self, value: datetime) -> str:
        return _as_utc_iso(value) or ""


class WhaleSellExecuteRequest(APIModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_id: str = Field(min_length=1, max_length=200)
    confirmation_text: Literal["确认真实卖出"]


class WhaleFillRead(APIModel):
    id: int
    external_trade_id: str | None
    transaction_hash: str | None
    bucket_index: int | None
    settlement_status: str | None
    size: DecimalNumber
    price: DecimalNumber
    amount: DecimalNumber
    fee_usdc: DecimalNumber
    timestamp: datetime

    @field_serializer("timestamp", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        return _as_utc_iso(value) or ""


class WhaleOrderRead(APIModel):
    id: int
    position_id: int | None
    entry_id: int | None
    source_wallet: str | None
    asset_id: str
    condition_id: str
    title: str
    outcome: str
    outcome_index: int | None
    neg_risk: bool
    side: Literal["BUY", "SELL"]
    requested_size: DecimalNumber
    requested_usdc: DecimalNumber
    limit_price: DecimalNumber
    reference_price: DecimalNumber | None
    whale_avg_price: DecimalNumber | None
    filled_size: DecimalNumber
    filled_usdc: DecimalNumber
    fee_usdc: DecimalNumber
    average_fill_price: DecimalNumber | None = None
    status: str
    reason: str | None
    signed_order_hash: str | None
    execution_provider: str
    external_order_id: str | None
    external_trade_id: str | None
    fills: list[WhaleFillRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at", when_used="json")
    def serialize_dates(self, value: datetime) -> str:
        return _as_utc_iso(value) or ""


class WhaleRedemptionRead(APIModel):
    id: int
    position_id: int
    status: Literal["pending", "submitted", "completed", "failed", "manual_review"]
    size: DecimalNumber
    payout_usdc: DecimalNumber | None
    transaction_hash: str | None
    attempts: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at", when_used="json")
    def serialize_dates(self, value: datetime) -> str:
        return _as_utc_iso(value) or ""


class WhalePositionRead(APIModel):
    id: int
    asset_id: str
    condition_id: str
    title: str
    outcome: str
    outcome_index: int | None
    neg_risk: bool | None
    market_slug: str | None
    event_slug: str | None
    icon_url: str | None
    source_wallet: str | None
    source_whale_avg_price: DecimalNumber | None
    cycle_no: int
    size: DecimalNumber
    avg_cost_price: DecimalNumber | None
    cost_usdc: DecimalNumber
    current_price: DecimalNumber | None
    market_value_usdc: DecimalNumber | None
    unrealized_pnl: DecimalNumber | None
    realized_pnl: DecimalNumber
    total_pnl: DecimalNumber | None
    lifetime_bought_size: DecimalNumber
    lifetime_bought_usdc: DecimalNumber
    lifetime_sold_size: DecimalNumber
    lifetime_sold_usdc: DecimalNumber
    lifetime_fee_usdc: DecimalNumber
    status: str
    valuation_status: Literal["ok", "unavailable", "not_applicable"]
    opened_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_serializer(
        "opened_at",
        "closed_at",
        "created_at",
        "updated_at",
        when_used="json",
    )
    def serialize_dates(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class WhaleLedgerRead(APIModel):
    id: int
    position_id: int
    order_id: int | None
    type: Literal["buy", "sell", "redeem", "resolved_loss", "dust_writeoff"]
    size: DecimalNumber
    price: DecimalNumber | None
    amount_usdc: DecimalNumber
    fee_usdc: DecimalNumber
    realized_pnl: DecimalNumber
    transaction_hash: str | None
    detail: str | None
    timestamp: datetime

    @field_serializer("timestamp", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        return _as_utc_iso(value) or ""


class WhalePositionListRead(APIModel):
    items: list[WhalePositionRead] = Field(default_factory=list)
    total: int


class WhalePositionDetailRead(WhalePositionRead):
    orders: list[WhaleOrderRead] = Field(default_factory=list)
    ledger: list[WhaleLedgerRead] = Field(default_factory=list)
    redemption: WhaleRedemptionRead | None = None


class WhaleRecordRead(WhaleLedgerRead):
    title: str
    outcome: str
    asset_id: str
    condition_id: str
    market_slug: str | None
    event_slug: str | None
    source_wallet: str | None
    order_side: Literal["BUY", "SELL"] | None = None
    order_status: str | None = None


class WhaleRecordSummaryRead(APIModel):
    total_invested_usdc: DecimalNumber
    total_proceeds_usdc: DecimalNumber
    total_fee_usdc: DecimalNumber
    realized_pnl: DecimalNumber
    unrealized_pnl: DecimalNumber | None
    total_pnl: DecimalNumber | None
    open_position_count: int
    closed_position_count: int
    win_count: int
    loss_count: int
    win_rate_percent: DecimalNumber | None
    average_profit_ratio_percent: DecimalNumber | None


class WhaleRecordListRead(APIModel):
    items: list[WhaleRecordRead] = Field(default_factory=list)
    summary: WhaleRecordSummaryRead
    total: int
