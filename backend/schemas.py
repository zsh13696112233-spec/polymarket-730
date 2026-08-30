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
    field_validator,
    model_validator,
)

DecimalNumber = Annotated[
    Decimal,
    PlainSerializer(lambda value: float(value), return_type=float, when_used="json"),
]

WhaleMarketCategory = Literal[
    "esports",
    "sports",
    "politics",
    "crypto",
    "science_tech",
    "entertainment",
    "other",
]


def _as_utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ExecutionAccountRead(APIModel):
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
    signer_address: str = Field(min_length=42, max_length=42)
    funder_address: str = Field(min_length=42, max_length=42)
    signature_type: Literal[3] = 3
    budget_usdc: Decimal = Field(default=Decimal("400"), ge=0)
    cash_reserve_usdc: Decimal = Field(default=Decimal("240"), ge=0)
    max_total_exposure_usdc: Decimal = Field(default=Decimal("160"), ge=0)
    daily_buy_limit_usdc: Decimal = Field(default=Decimal("80"), ge=0)
    daily_loss_limit_usdc: Decimal = Field(default=Decimal("40"), ge=0)
    auto_redeem: bool = False


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
    new_account_auto_follow_enabled: bool
    new_account_auto_follow_amount_usdc: DecimalNumber
    new_account_auto_follow_min_price: DecimalNumber
    new_account_auto_follow_max_price: DecimalNumber
    new_account_auto_follow_categories: list[WhaleMarketCategory]
    new_account_auto_follow_low_price_max_price: DecimalNumber | None
    new_account_auto_follow_low_price_amount_usdc: DecimalNumber | None
    large_amount_auto_follow_enabled: bool
    large_amount_auto_follow_amount_usdc: DecimalNumber
    large_amount_auto_follow_min_price: DecimalNumber
    large_amount_auto_follow_max_price: DecimalNumber
    large_amount_auto_follow_categories: list[WhaleMarketCategory]
    large_amount_auto_follow_low_price_max_price: DecimalNumber | None
    large_amount_auto_follow_low_price_amount_usdc: DecimalNumber | None
    auto_follow_market_max_purchase_count: int | None
    auto_follow_market_max_amount_usdc: DecimalNumber | None
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
    new_account_auto_follow_enabled: bool | None = None
    new_account_auto_follow_amount_usdc: Decimal | None = Field(default=None, gt=0)
    new_account_auto_follow_min_price: Decimal | None = Field(default=None, gt=0, lt=1)
    new_account_auto_follow_max_price: Decimal | None = Field(default=None, gt=0, lt=1)
    new_account_auto_follow_categories: list[WhaleMarketCategory] | None = None
    new_account_auto_follow_low_price_max_price: Decimal | None = Field(default=None, gt=0, lt=1)
    new_account_auto_follow_low_price_amount_usdc: Decimal | None = Field(default=None, gt=0)
    large_amount_auto_follow_enabled: bool | None = None
    large_amount_auto_follow_amount_usdc: Decimal | None = Field(default=None, gt=0)
    large_amount_auto_follow_min_price: Decimal | None = Field(default=None, gt=0, lt=1)
    large_amount_auto_follow_max_price: Decimal | None = Field(default=None, gt=0, lt=1)
    large_amount_auto_follow_categories: list[WhaleMarketCategory] | None = None
    large_amount_auto_follow_low_price_max_price: Decimal | None = Field(default=None, gt=0, lt=1)
    large_amount_auto_follow_low_price_amount_usdc: Decimal | None = Field(default=None, gt=0)
    auto_follow_market_max_purchase_count: int | None = Field(default=None, gt=0)
    auto_follow_market_max_amount_usdc: Decimal | None = Field(default=None, gt=0)
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

    @field_validator(
        "new_account_auto_follow_categories",
        "large_amount_auto_follow_categories",
    )
    @classmethod
    def validate_auto_categories(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        unique = list(dict.fromkeys(value))
        if not unique:
            raise ValueError("自动跟单至少需要选择一个市场分类")
        return unique

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
        for label, minimum, maximum in (
            (
                "新号大额",
                self.new_account_auto_follow_min_price,
                self.new_account_auto_follow_max_price,
            ),
            (
                "全量超大额",
                self.large_amount_auto_follow_min_price,
                self.large_amount_auto_follow_max_price,
            ),
        ):
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValueError(f"{label}自动跟单最低买价不能高于最高买价")
        return self


class WhaleAutoDecisionRead(APIModel):
    id: int
    entry_id: int
    proxy_wallet: str
    asset_id: str
    condition_id: str
    title: str
    market_slug: str | None
    event_slug: str | None
    outcome: str
    matched_rules: list[Literal["new_account", "large_amount"]]
    selected_rule: Literal["new_account", "large_amount"] | None
    category: WhaleMarketCategory
    category_label: str
    configured_amount_usdc: DecimalNumber | None
    configured_min_price: DecimalNumber | None
    configured_max_price: DecimalNumber | None
    selected_amount_usdc: DecimalNumber | None
    observed_best_ask: DecimalNumber | None
    status: str
    reason: str | None
    buy_order_id: int | None
    latest_sell_order_id: int | None
    followed_wallet_count: int
    processed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("processed_at", "created_at", "updated_at", when_used="json")
    def serialize_auto_dates(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class WhaleAutoDecisionListRead(APIModel):
    total: int
    items: list[WhaleAutoDecisionRead] = Field(default_factory=list)


class EmailDeliveryMarketSummaryRead(APIModel):
    category_label: str
    outcome: str
    avg_buy_price: DecimalNumber
    gross_buy_usdc: DecimalNumber


class EmailDeliveryRead(APIModel):
    id: int
    entry_id: int | None
    notification_kind: Literal["entry", "divergence", "weekly_summary"]
    condition_id: str
    entry_ids: list[int]
    rules: list[Literal["new_account", "large_amount"]]
    recipient_email: str
    market_title: str
    wallet_label: str
    market_summaries: list[EmailDeliveryMarketSummaryRead] = Field(default_factory=list)
    subject: str
    body_text: str
    result: Literal["pending", "hit", "miss", "special", "not_applicable"]
    status: Literal["pending", "sending", "retrying", "sent", "failed"]
    attempt_count: int
    next_attempt_at: datetime | None
    last_error: str | None
    created_at: datetime
    sent_at: datetime | None

    @field_serializer("next_attempt_at", "created_at", "sent_at", when_used="json")
    def serialize_delivery_dates(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class EmailDeliveryListRead(APIModel):
    total: int
    items: list[EmailDeliveryRead]


class EmailSettingsRead(APIModel):
    notifications_enabled: bool = False
    weekly_summary_enabled: bool = False
    weekly_summary_enabled_at: datetime | None = None
    weekly_summary_last_sent_at: datetime | None = None
    weekly_summary_next_run_at: datetime | None = None
    notification_recipients: list[str] = Field(default_factory=list)
    smtp_host: str | None = None
    smtp_port: int = 465
    smtp_security: Literal["starttls", "ssl", "none"] = "ssl"
    smtp_username: str | None = None
    smtp_from_email: str | None = None
    smtp_from_name: str = "PolyCopy"
    smtp_authorization_code_configured: bool = False
    smtp_configured: bool = False

    @field_serializer(
        "weekly_summary_enabled_at",
        "weekly_summary_last_sent_at",
        "weekly_summary_next_run_at",
        when_used="json",
    )
    def serialize_weekly_summary_dates(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class EmailSettingsUpdate(APIModel):
    model_config = ConfigDict(extra="forbid")

    notifications_enabled: bool | None = None
    weekly_summary_enabled: bool | None = None
    notification_recipients: list[str] | None = None
    smtp_host: str | None = Field(default=None, min_length=1, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_security: Literal["starttls", "ssl", "none"] | None = None
    smtp_username: str | None = Field(default=None, min_length=3, max_length=320)
    smtp_from_email: str | None = Field(default=None, min_length=3, max_length=320)
    smtp_from_name: str | None = Field(default=None, min_length=1, max_length=200)
    smtp_authorization_code: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("notification_recipients")
    @classmethod
    def validate_notification_recipients(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized: list[str] = []
        for raw in value:
            email = raw.strip().lower()
            local, separator, domain = email.rpartition("@")
            if (
                not separator
                or not local
                or "." not in domain
                or domain.startswith(".")
                or domain.endswith(".")
                or any(character.isspace() for character in email)
                or len(email) > 320
            ):
                raise ValueError(f"无效的收件邮箱：{raw}")
            if email not in normalized:
                normalized.append(email)
        return normalized

    @field_validator("smtp_username", "smtp_from_email")
    @classmethod
    def validate_smtp_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        email = value.strip().lower()
        local, separator, domain = email.rpartition("@")
        if (
            not separator
            or not local
            or "." not in domain
            or domain.startswith(".")
            or domain.endswith(".")
            or any(character.isspace() for character in email)
        ):
            raise ValueError(f"无效的邮箱地址：{value}")
        return email

    @field_validator("smtp_host", "smtp_from_name", "smtp_authorization_code")
    @classmethod
    def strip_smtp_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class EmailTestRequest(APIModel):
    send_email: bool = False
    recipient_email: str | None = None

    @field_validator("recipient_email")
    @classmethod
    def validate_recipient_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        email = value.strip().lower()
        local, separator, domain = email.rpartition("@")
        if not separator or not local or "." not in domain or any(c.isspace() for c in email):
            raise ValueError("测试收件邮箱无效")
        return email

    @model_validator(mode="after")
    def require_recipient_for_send(self) -> EmailTestRequest:
        if self.send_email and not self.recipient_email:
            raise ValueError("发送测试邮件时必须填写收件邮箱")
        return self


class EmailTestRead(APIModel):
    status: Literal["ok"]
    connection: Literal["ok"]
    tls: Literal["ok", "not_used"]
    authentication: Literal["ok"]
    message_sent: bool
    detail: str


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
    source: Literal["http", "sdk"]
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


class HomeSystemRuleRead(APIModel):
    rule: Literal["new_account", "large_amount"]
    enabled: bool
    auto_follow_enabled: bool
    active_wallet_count: int


class HomeSystemRead(APIModel):
    status: Literal["healthy", "error", "disabled"]
    enabled: bool
    last_scan_at: datetime | None
    last_scan_error: str | None
    consecutive_failures: int
    scan_interval_seconds: int
    rules: list[HomeSystemRuleRead] = Field(default_factory=list)

    @field_serializer("last_scan_at", when_used="json")
    def serialize_last_scan_at(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class HomeTodayRead(APIModel):
    date: date
    buy_amount_usdc: DecimalNumber
    buy_count: int
    conflict_exit_proceeds_usdc: DecimalNumber
    conflict_exit_count: int
    realized_pnl_usdc: DecimalNumber
    realized_cost_usdc: DecimalNumber
    realized_roi_percent: DecimalNumber | None
    unrealized_pnl_usdc: DecimalNumber | None
    win_count: int
    loss_count: int
    flat_count: int
    win_rate_percent: DecimalNumber | None


class HomeWalletRead(APIModel):
    status: str
    available: bool
    cash_balance_usdc: DecimalNumber | None
    open_cost_usdc: DecimalNumber
    market_value_usdc: DecimalNumber | None
    total_assets_usdc: DecimalNumber | None
    unrealized_pnl_usdc: DecimalNumber | None
    cash_reserve_usdc: DecimalNumber
    available_cash_usdc: DecimalNumber | None
    open_position_count: int
    last_balance_at: datetime | None
    balance_stale: bool
    valuation_complete: bool
    unpriced_position_count: int
    last_error: str | None

    @field_serializer("last_balance_at", when_used="json")
    def serialize_last_balance_at(self, value: datetime | None) -> str | None:
        return _as_utc_iso(value)


class HomeDailyRead(APIModel):
    date: date
    buy_amount_usdc: DecimalNumber
    buy_count: int
    conflict_exit_proceeds_usdc: DecimalNumber
    conflict_exit_count: int
    realized_pnl_usdc: DecimalNumber
    realized_cost_usdc: DecimalNumber
    realized_roi_percent: DecimalNumber | None
    win_count: int
    loss_count: int
    flat_count: int


class HomeAutoDecisionRead(APIModel):
    id: int
    created_at: datetime
    selected_rule: Literal["new_account", "large_amount"] | None
    matched_rules: list[Literal["new_account", "large_amount"]]
    title: str
    outcome: str
    market_slug: str | None
    event_slug: str | None
    configured_amount_usdc: DecimalNumber | None
    filled_usdc: DecimalNumber
    status: str
    reason: str | None
    is_risk_exit: bool

    @field_serializer("created_at", when_used="json")
    def serialize_created_at(self, value: datetime) -> str:
        return _as_utc_iso(value) or ""


class HomeOverviewRead(APIModel):
    as_of: datetime
    timezone: Literal["Asia/Shanghai"]
    range_start: date
    range_end: date
    system: HomeSystemRead
    today: HomeTodayRead
    wallet: HomeWalletRead
    daily: list[HomeDailyRead] = Field(default_factory=list)
    recent_auto_decisions: list[HomeAutoDecisionRead] = Field(default_factory=list)

    @field_serializer("as_of", when_used="json")
    def serialize_as_of(self, value: datetime) -> str:
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
    category: Literal[
        "all",
        "esports",
        "sports",
        "politics",
        "crypto",
        "science_tech",
        "entertainment",
        "other",
    ]
    subcategory: str
    overall: WhaleStatisticsMetricsRead
    new_account: WhaleStatisticsMetricsRead
    large_amount: WhaleStatisticsMetricsRead
    dual_match: WhaleStatisticsMetricsRead
    trend: list[WhaleStatisticsSliceRead] = Field(default_factory=list)
    amount_bands: list[WhaleStatisticsSliceRead] = Field(default_factory=list)
    category_breakdown: list[WhaleStatisticsSliceRead] = Field(default_factory=list)
    subcategory_breakdown: list[WhaleStatisticsSliceRead] = Field(default_factory=list)

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
    category: Literal[
        "esports",
        "sports",
        "politics",
        "crypto",
        "science_tech",
        "entertainment",
        "other",
    ]
    category_label: str
    subcategory: str | None
    subcategory_label: str | None
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


class ChainTestResolveRequest(APIModel):
    model_config = ConfigDict(extra="forbid")

    market_url: str = Field(min_length=1, max_length=1000)


class ChainTestOutcomeRead(APIModel):
    asset_id: str
    label: str
    outcome_index: int
    reference_price: DecimalNumber


class ChainTestMarketRead(APIModel):
    condition_id: str
    title: str
    market_slug: str | None
    event_slug: str | None
    closed: bool
    active: bool
    accepting_orders: bool
    outcomes: list[ChainTestOutcomeRead] = Field(default_factory=list)


class ChainTestResolveRead(APIModel):
    resolution_id: str
    expires_at: datetime
    market_url: str
    event_title: str
    markets: list[ChainTestMarketRead] = Field(default_factory=list)

    @field_serializer("expires_at", when_used="json")
    def serialize_expires_at(self, value: datetime) -> str:
        return _as_utc_iso(value) or ""


class ChainTestBuyPreviewRequest(APIModel):
    model_config = ConfigDict(extra="forbid")

    resolution_id: str = Field(min_length=1, max_length=200)
    asset_id: str = Field(min_length=1, max_length=100)
    amount_usdc: Decimal = Field(gt=0)


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
    source: str
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
    source: Literal[
        "follow",
        "manual",
        "auto_follow",
        "conflict_exit",
        "auto_redeem",
        "reconciliation",
    ]
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
