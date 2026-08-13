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
    activity_at: datetime

    @field_serializer("activity_at", when_used="json")
    def serialize_activity_date(self, value: datetime) -> str:
        return _as_utc_iso(value) or ""


class CopyActivitiesResponse(APIModel):
    items: list[CopyActivityRead]
    next_cursor: str | None = None


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
