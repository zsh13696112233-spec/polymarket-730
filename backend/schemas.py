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
    signature_type: Literal[1] = 1
    budget_usdc: Decimal = Field(default=Decimal("400"), ge=0)
    cash_reserve_usdc: Decimal = Field(default=Decimal("240"), ge=0)
    max_total_exposure_usdc: Decimal = Field(default=Decimal("160"), ge=0)
    daily_buy_limit_usdc: Decimal = Field(default=Decimal("80"), ge=0)
    daily_loss_limit_usdc: Decimal = Field(default=Decimal("40"), ge=0)
    auto_redeem: bool = True


class CopySubscriptionConfig(APIModel):
    copy_ratio_percent: Decimal = Field(default=Decimal("10"), gt=0, le=100)
    position_cap_usdc: Decimal = Field(default=Decimal("20"), gt=0)
    total_exposure_cap_usdc: Decimal = Field(default=Decimal("160"), ge=0)
    market_slippage_cents: Decimal = Field(default=Decimal("5"), ge=0, le=50)


class CopySubscriptionCreate(CopySubscriptionConfig):
    tracked_wallet_id: int = Field(gt=0)
    mode: Literal["paper", "live"] = "paper"
    confirm_live: bool = False


class CopySubscriptionUpdate(CopySubscriptionConfig):
    pass


class CopySubscriptionAction(APIModel):
    action: Literal["activate", "pause", "resume", "exit_only", "close", "disable"]
    confirm_live: bool = False


class CopySubscriptionModeUpdate(APIModel):
    mode: Literal["paper", "live"]
    confirm_live: bool = False


class CopySubscriptionRead(APIModel):
    id: int
    tracked_wallet_id: int
    tracked_wallet_label: str | None = None
    mode: Literal["paper", "live"]
    state: Literal["active", "paused", "exit_only", "closing", "disabled", "error"]
    copy_ratio_percent: DecimalNumber
    position_cap_usdc: DecimalNumber
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


class CopyOrderRead(APIModel):
    id: int
    subscription_id: int | None
    leader_event_id: int | None
    asset_id: str
    side: Literal["BUY", "SELL"]
    mode: Literal["paper", "live"]
    source: Literal["copy", "rehearsal"]
    signed_order_hash: str | None
    requested_size: DecimalNumber
    requested_usdc: DecimalNumber
    limit_price: DecimalNumber
    reference_price: DecimalNumber | None
    filled_size: DecimalNumber
    filled_usdc: DecimalNumber
    fee_usdc: DecimalNumber
    status: str
    reason: str | None
    external_order_id: str | None
    external_trade_id: str | None
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
    account: ExecutionAccountRead | None
    subscription: CopySubscriptionRead | None
    positions: list[CopyPositionRead]
    orders: list[CopyOrderRead]
    portfolio: CopyPortfolioSummaryRead


class RehearsalPreviewRequest(APIModel):
    market_url: str = Field(min_length=1, max_length=1000)
    outcome: str = Field(min_length=1, max_length=200)
    max_total_usdc: Decimal = Field(default=Decimal("5"), gt=0, le=5)


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
    confirmation_text: Literal["确认执行5美元演练"]


class CopyRecommendation(APIModel):
    action: Literal["buy", "sell"]
    ratio_percent: DecimalNumber
    shares: DecimalNumber
    estimated_usdc: DecimalNumber | None


class WalletCreate(APIModel):
    address: str = Field(min_length=1, max_length=500)
    label: str | None = Field(default=None, min_length=1, max_length=100)


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


class HealthRead(APIModel):
    status: Literal["ok"]
    database: Literal["ok"]
