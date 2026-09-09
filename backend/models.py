from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

DECIMAL_TYPE = Numeric(38, 18)
PERCENT_TYPE = Numeric(5, 2)


class Base(DeclarativeBase):
    pass


class ExecutionAccount(Base):
    __tablename__ = "execution_accounts"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_execution_accounts_singleton"),
        CheckConstraint("budget_usdc >= 0", name="ck_execution_accounts_budget"),
        CheckConstraint("cash_reserve_usdc >= 0", name="ck_execution_accounts_reserve"),
        CheckConstraint(
            "max_total_exposure_usdc >= 0",
            name="ck_execution_accounts_total_exposure",
        ),
        CheckConstraint(
            "daily_buy_limit_usdc >= 0",
            name="ck_execution_accounts_daily_buy_limit",
        ),
        CheckConstraint(
            "daily_loss_limit_usdc >= 0",
            name="ck_execution_accounts_daily_loss_limit",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signer_address: Mapped[str | None] = mapped_column(String(42), nullable=True)
    funder_address: Mapped[str | None] = mapped_column(String(42), nullable=True)
    signature_type: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    keychain_service: Mapped[str | None] = mapped_column(String(200), nullable=True)
    keychain_account: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="unconfigured")
    budget_usdc: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("400")
    )
    cash_reserve_usdc: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("240")
    )
    max_total_exposure_usdc: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("160")
    )
    daily_buy_limit_usdc: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("80")
    )
    daily_loss_limit_usdc: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("40")
    )
    auto_redeem: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    collateral_balance: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    last_balance_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class RedemptionExecution(Base):
    __tablename__ = "redemption_executions"
    __table_args__ = (
        UniqueConstraint(
            "wallet_address", "condition_id", name="uq_copy_redemption_execution_wallet_condition"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wallet_address: Mapped[str] = mapped_column(String(42), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(66), nullable=False)
    method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    execution_provider: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    estimated_payout_usdc: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    actual_pusd_delta: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    before_outcome_balance: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    after_outcome_balance: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    before_pusd_balance: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    after_pusd_balance: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    relayer_transaction_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    transaction_hash: Mapped[str | None] = mapped_column(String(100), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class WhaleSettings(Base):
    __tablename__ = "whale_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_whale_settings_singleton"),
        CheckConstraint(
            "dual_match_auto_follow_amount_usdc IS NULL OR dual_match_auto_follow_amount_usdc > 0",
            name="ck_whale_settings_dual_match_amount",
        ),
        CheckConstraint(
            "single_trade_threshold_usdc >= collect_filter_amount_usdc",
            name="ck_whale_settings_single_threshold",
        ),
        CheckConstraint(
            "cumulative_threshold_usdc >= collect_filter_amount_usdc",
            name="ck_whale_settings_cumulative_threshold",
        ),
        CheckConstraint(
            "exited_ratio_threshold >= 0 "
            "AND exited_ratio_threshold < holding_ratio_threshold "
            "AND holding_ratio_threshold <= 100",
            name="ck_whale_settings_ratio_thresholds",
        ),
        CheckConstraint(
            "max_follow_amount_usdc > 0",
            name="ck_whale_settings_max_follow_amount",
        ),
        CheckConstraint(
            "default_follow_amount_usdc > 0 "
            "AND default_follow_amount_usdc <= max_follow_amount_usdc",
            name="ck_whale_settings_default_follow_amount",
        ),
        CheckConstraint(
            "registration_window_days >= 1 AND registration_window_days <= 30",
            name="ck_whale_settings_registration_window_days",
        ),
        CheckConstraint(
            "new_account_threshold_usdc >= collect_filter_amount_usdc",
            name="ck_whale_settings_new_account_threshold",
        ),
        CheckConstraint(
            "large_amount_threshold_usdc >= collect_filter_amount_usdc",
            name="ck_whale_settings_large_amount_threshold",
        ),
        CheckConstraint(
            "new_account_auto_follow_amount_usdc > 0",
            name="ck_whale_settings_new_auto_amount",
        ),
        CheckConstraint(
            "new_account_auto_follow_min_price > 0 "
            "AND new_account_auto_follow_min_price <= new_account_auto_follow_max_price "
            "AND new_account_auto_follow_max_price < 1",
            name="ck_whale_settings_new_auto_prices",
        ),
        CheckConstraint(
            "large_amount_auto_follow_amount_usdc > 0",
            name="ck_whale_settings_large_auto_amount",
        ),
        CheckConstraint(
            "large_amount_auto_follow_min_price > 0 "
            "AND large_amount_auto_follow_min_price <= large_amount_auto_follow_max_price "
            "AND large_amount_auto_follow_max_price < 1",
            name="ck_whale_settings_large_auto_prices",
        ),
        CheckConstraint(
            "(new_account_auto_follow_low_price_max_price IS NULL "
            "AND new_account_auto_follow_low_price_amount_usdc IS NULL) OR "
            "(new_account_auto_follow_low_price_max_price IS NOT NULL AND "
            "new_account_auto_follow_low_price_amount_usdc IS NOT NULL AND "
            "new_account_auto_follow_low_price_max_price > "
            "new_account_auto_follow_min_price AND "
            "new_account_auto_follow_low_price_max_price < "
            "new_account_auto_follow_max_price AND "
            "new_account_auto_follow_low_price_amount_usdc > 0 AND "
            "new_account_auto_follow_low_price_amount_usdc < "
            "new_account_auto_follow_amount_usdc)",
            name="ck_whale_settings_new_auto_low_price",
        ),
        CheckConstraint(
            "(large_amount_auto_follow_low_price_max_price IS NULL "
            "AND large_amount_auto_follow_low_price_amount_usdc IS NULL) OR "
            "(large_amount_auto_follow_low_price_max_price IS NOT NULL AND "
            "large_amount_auto_follow_low_price_amount_usdc IS NOT NULL AND "
            "large_amount_auto_follow_low_price_max_price > "
            "large_amount_auto_follow_min_price AND "
            "large_amount_auto_follow_low_price_max_price < "
            "large_amount_auto_follow_max_price AND "
            "large_amount_auto_follow_low_price_amount_usdc > 0 AND "
            "large_amount_auto_follow_low_price_amount_usdc < "
            "large_amount_auto_follow_amount_usdc)",
            name="ck_whale_settings_large_auto_low_price",
        ),
        CheckConstraint(
            "(auto_follow_market_max_purchase_count IS NULL "
            "AND auto_follow_market_max_amount_usdc IS NULL) OR "
            "(auto_follow_market_max_purchase_count IS NOT NULL AND "
            "auto_follow_market_max_amount_usdc IS NOT NULL AND "
            "auto_follow_market_max_purchase_count > 0 "
            "AND auto_follow_market_max_amount_usdc > 0)",
            name="ck_whale_settings_auto_market_caps",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    window_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    registration_window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    collect_filter_amount_usdc: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("1000")
    )
    single_trade_threshold_usdc: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("100000")
    )
    cumulative_threshold_usdc: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("100000")
    )
    new_account_threshold_usdc: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("100000")
    )
    large_amount_threshold_usdc: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("500000")
    )
    monitor_categories_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default='["sports","esports","politics","crypto","science_tech","entertainment","other"]',
        server_default='["sports","esports","politics","crypto","science_tech","entertainment","other"]',
    )
    dual_match_auto_follow_amount_usdc: Mapped[Decimal | None] = mapped_column(
        DECIMAL_TYPE, nullable=True
    )
    new_account_auto_follow_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    new_account_auto_follow_amount_usdc: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("5")
    )
    new_account_auto_follow_min_price: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("0.65")
    )
    new_account_auto_follow_max_price: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("0.80")
    )
    new_account_auto_follow_categories_json: Mapped[str] = mapped_column(
        Text, nullable=False, default='["sports"]'
    )
    new_account_auto_follow_low_price_max_price: Mapped[Decimal | None] = mapped_column(
        DECIMAL_TYPE, nullable=True
    )
    new_account_auto_follow_low_price_amount_usdc: Mapped[Decimal | None] = mapped_column(
        DECIMAL_TYPE, nullable=True
    )
    large_amount_auto_follow_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    large_amount_auto_follow_amount_usdc: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("10")
    )
    large_amount_auto_follow_min_price: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("0.60")
    )
    large_amount_auto_follow_max_price: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("0.80")
    )
    large_amount_auto_follow_categories_json: Mapped[str] = mapped_column(
        Text, nullable=False, default='["sports"]'
    )
    large_amount_auto_follow_low_price_max_price: Mapped[Decimal | None] = mapped_column(
        DECIMAL_TYPE, nullable=True
    )
    large_amount_auto_follow_low_price_amount_usdc: Mapped[Decimal | None] = mapped_column(
        DECIMAL_TYPE, nullable=True
    )
    large_amount_conflict_priority_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    auto_follow_market_max_purchase_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    auto_follow_market_max_amount_usdc: Mapped[Decimal | None] = mapped_column(
        DECIMAL_TYPE, nullable=True
    )
    min_liquidity_usdc: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("5000")
    )
    min_remaining_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    max_price_delta_cents: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("5")
    )
    holding_ratio_threshold: Mapped[Decimal] = mapped_column(
        PERCENT_TYPE, nullable=False, default=Decimal("80")
    )
    exited_ratio_threshold: Mapped[Decimal] = mapped_column(
        PERCENT_TYPE, nullable=False, default=Decimal("20")
    )
    scan_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    profile_cache_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    trade_retention_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=72)
    max_follow_amount_usdc: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("200")
    )
    default_follow_amount_usdc: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("20")
    )
    follow_slippage_cents: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("3")
    )
    sell_slippage_cents: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("3")
    )
    auto_redeem: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_trade_cursor_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    coverage_incomplete_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_scan_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class WhaleScanRun(Base):
    __tablename__ = "whale_scan_runs"
    __table_args__ = (
        UniqueConstraint("scan_id", name="uq_whale_scan_runs_scan_id"),
        CheckConstraint(
            "status IN ('running', 'success', 'degraded', 'failed')",
            name="ck_whale_scan_runs_status",
        ),
        Index("ix_whale_scan_runs_started", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_id: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    requested_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    requested_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    oldest_trade_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    newest_trade_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    collected_trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page_limit_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    coverage_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class EmailSettings(Base):
    __tablename__ = "email_settings"
    __table_args__ = (CheckConstraint("id = 1", name="ck_email_settings_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    weekly_summary_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    weekly_summary_enabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[int] = mapped_column(Integer, nullable=False, default=465)
    smtp_security: Mapped[str] = mapped_column(String(20), nullable=False, default="ssl")
    smtp_username: Mapped[str | None] = mapped_column(String(320), nullable=True)
    smtp_from_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    smtp_from_name: Mapped[str] = mapped_column(String(200), nullable=False, default="PolyCopy")
    smtp_keychain_service: Mapped[str | None] = mapped_column(String(200), nullable=True)
    smtp_keychain_account: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class EmailRecipient(Base):
    __tablename__ = "email_recipients"

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class WhaleEmailDelivery(Base):
    __tablename__ = "whale_email_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "entry_id",
            "rule_key",
            "recipient_email",
            name="uq_whale_email_delivery_trigger_recipient",
        ),
        UniqueConstraint(
            "dedupe_key",
            "recipient_email",
            name="uq_whale_email_delivery_dedupe_recipient",
        ),
        CheckConstraint(
            "notification_kind IN ('entry','divergence','weekly_summary')",
            name="ck_whale_email_delivery_kind",
        ),
        CheckConstraint(
            "status IN ('pending','sending','retrying','sent','failed')",
            name="ck_whale_email_delivery_status",
        ),
        Index("ix_whale_email_delivery_due", "status", "next_attempt_at"),
        Index("ix_whale_email_delivery_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("whale_entries.id", ondelete="RESTRICT"), nullable=True
    )
    notification_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="entry")
    condition_id: Mapped[str] = mapped_column(String(66), nullable=False)
    entry_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    rule_key: Mapped[str] = mapped_column(String(80), nullable=False)
    rules_json: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False)
    market_title: Mapped[str] = mapped_column(Text, nullable=False)
    wallet_label: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WhaleTrade(Base):
    __tablename__ = "whale_trades"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_whale_trades_fingerprint"),
        Index("ix_whale_trades_time", "timestamp"),
        Index(
            "ix_whale_trades_wallet_asset_time",
            "proxy_wallet",
            "asset_id",
            "timestamp",
        ),
        Index("ix_whale_trades_condition_time", "condition_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    proxy_wallet: Mapped[str] = mapped_column(String(42), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(100), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(66), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    price: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    amount: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    outcome: Mapped[str] = mapped_column(String(200), nullable=False)
    outcome_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    market_slug: Mapped[str] = mapped_column(String(500), nullable=False)
    event_slug: Mapped[str] = mapped_column(String(500), nullable=False)
    icon_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    transaction_hash: Mapped[str | None] = mapped_column(String(100), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class WhaleMarketScanState(Base):
    __tablename__ = "whale_market_scan_states"

    condition_id: Mapped[str] = mapped_column(String(66), primary_key=True)
    coverage_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    coverage_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    batch_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    batch_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pending_ranges_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    auto_follow_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class WhaleBackfillSignalState(Base):
    __tablename__ = "whale_backfill_signal_states"

    proxy_wallet: Mapped[str] = mapped_column(String(42), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    condition_id: Mapped[str] = mapped_column(String(66), nullable=False)
    auto_follow_after: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    awaiting_new_buy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class WhaleMarketScanPage(Base):
    __tablename__ = "whale_market_scan_pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    condition_id: Mapped[str] = mapped_column(String(66), nullable=False, index=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class WhaleMarket(Base):
    __tablename__ = "whale_markets"
    __table_args__ = (
        Index("ix_whale_markets_refreshed", "refreshed_at"),
        Index("ix_whale_markets_end_date", "end_date"),
    )

    condition_id: Mapped[str] = mapped_column(String(66), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    market_slug: Mapped[str | None] = mapped_column(String(500), nullable=True)
    event_slug: Mapped[str | None] = mapped_column(String(500), nullable=True)
    icon_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcomes_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    outcome_prices_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    clob_token_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    accepting_orders: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    neg_risk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_date_is_date_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    liquidity: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    volume_24h: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    best_bid: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    best_ask: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    order_min_size: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("5")
    )
    tick_size: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("0.01")
    )
    fee_rate: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    fee_exponent: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class WhaleTag(Base):
    __tablename__ = "whale_tags"
    __table_args__ = (UniqueConstraint("slug", name="uq_whale_tags_slug"),)

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    slug: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    market_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class WhaleWallet(Base):
    __tablename__ = "whale_wallets"

    proxy_wallet: Mapped[str] = mapped_column(String(42), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pseudonym: Mapped[str | None] = mapped_column(String(200), nullable=True)
    profile_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_badge: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    taker_tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    taker_tier_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    weighted_volume: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    profile_missing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class WhaleExclusion(Base):
    __tablename__ = "whale_exclusions"

    proxy_wallet: Mapped[str] = mapped_column(String(42), primary_key=True)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class WhaleEntry(Base):
    __tablename__ = "whale_entries"
    __table_args__ = (
        UniqueConstraint("proxy_wallet", "asset_id", name="uq_whale_entries_wallet_asset"),
        CheckConstraint(
            "net_ratio >= 0 AND net_ratio <= 100",
            name="ck_whale_entries_net_ratio_percent",
        ),
        Index("ix_whale_entries_condition", "condition_id"),
        Index("ix_whale_entries_status_amount", "status", "gross_buy_usdc"),
        Index("ix_whale_entries_settled_at", "settled_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proxy_wallet: Mapped[str] = mapped_column(String(42), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(100), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(66), nullable=False)
    outcome: Mapped[str] = mapped_column(String(200), nullable=False)
    outcome_index: Mapped[int] = mapped_column(Integer, nullable=False)
    gross_buy_usdc: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    gross_buy_size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    sold_size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    sold_usdc: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    net_size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    net_ratio: Mapped[Decimal] = mapped_column(PERCENT_TYPE, nullable=False)
    avg_buy_price: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    max_single_usdc: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False)
    first_buy_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_buy_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    hedged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    window_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    follow_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    follow_ineligible_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovery_source: Mapped[str] = mapped_column(String(20), nullable=False, default="trades")
    position_cost_usdc: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    opposite_size: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    position_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    settlement_price: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    rule_states: Mapped[list[WhaleEntryRuleState]] = relationship(
        back_populates="entry", cascade="all, delete-orphan"
    )


class WhaleEntryRuleState(Base):
    __tablename__ = "whale_entry_rule_states"
    __table_args__ = (
        UniqueConstraint("entry_id", "rule_type", name="uq_whale_entry_rule_state"),
        CheckConstraint(
            "rule_type IN ('new_account', 'large_amount')",
            name="ck_whale_entry_rule_state_type",
        ),
        Index("ix_whale_entry_rule_active", "rule_type", "active", "last_qualified_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("whale_entries.id", ondelete="CASCADE"), nullable=False
    )
    rule_type: Mapped[str] = mapped_column(String(30), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    first_triggered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_qualified_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    inactive_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    inactive_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    threshold_usdc_snapshot: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    registration_days_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)

    entry: Mapped[WhaleEntry] = relationship(back_populates="rule_states")


class WhaleAutoFollowDecision(Base):
    __tablename__ = "whale_auto_follow_decisions"
    __table_args__ = (
        UniqueConstraint(
            "asset_id",
            "proxy_wallet",
            name="uq_whale_auto_decision_asset_wallet",
        ),
        Index("ix_whale_auto_decision_status_created", "status", "created_at"),
        Index("ix_whale_auto_decision_condition", "condition_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("whale_entries.id", ondelete="CASCADE"), nullable=False
    )
    proxy_wallet: Mapped[str] = mapped_column(String(42), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(100), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(66), nullable=False)
    outcome: Mapped[str] = mapped_column(String(200), nullable=False)
    outcome_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    matched_rules_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    selected_rule: Mapped[str | None] = mapped_column(String(30), nullable=True)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    configured_amount_usdc: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    configured_min_price: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    configured_max_price: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    configured_low_price_max_price: Mapped[Decimal | None] = mapped_column(
        DECIMAL_TYPE, nullable=True
    )
    configured_low_price_amount_usdc: Mapped[Decimal | None] = mapped_column(
        DECIMAL_TYPE, nullable=True
    )
    selected_amount_usdc: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    observed_best_ask: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    buy_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("whale_orders.id", ondelete="SET NULL"), nullable=True
    )
    latest_sell_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("whale_orders.id", ondelete="SET NULL"), nullable=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class WhaleAutoMarketLock(Base):
    __tablename__ = "whale_auto_market_locks"
    __table_args__ = (Index("ix_whale_auto_market_lock_exit", "exit_status", "updated_at"),)

    condition_id: Mapped[str] = mapped_column(String(66), primary_key=True)
    trigger_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("whale_entries.id", ondelete="SET NULL"), nullable=True
    )
    trigger_wallet: Mapped[str | None] = mapped_column(String(42), nullable=True)
    trigger_asset_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    trigger_outcome: Mapped[str | None] = mapped_column(String(200), nullable=True)
    trigger_amount_usdc: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    trigger_rules_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    exit_status: Mapped[str] = mapped_column(String(30), nullable=False, default="not_required")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class WhaleFollowPosition(Base):
    __tablename__ = "whale_follow_positions"
    __table_args__ = (
        UniqueConstraint("asset_id", "cycle_no", name="uq_whale_positions_asset_cycle"),
        Index("ix_whale_positions_status", "status"),
        Index("ix_whale_positions_condition", "condition_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(100), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(66), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(String(200), nullable=False)
    outcome_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    neg_risk: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    market_slug: Mapped[str | None] = mapped_column(String(500), nullable=True)
    event_slug: Mapped[str | None] = mapped_column(String(500), nullable=True)
    icon_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_wallet: Mapped[str | None] = mapped_column(String(42), nullable=True)
    source_whale_avg_price: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    cycle_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    cost_usdc: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    lifetime_bought_size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    lifetime_bought_usdc: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    lifetime_sold_size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    lifetime_sold_usdc: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    lifetime_fee_usdc: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    realized_pnl: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="opening")
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    orders: Mapped[list[WhaleOrder]] = relationship(back_populates="position")
    ledger_entries: Mapped[list[WhaleFollowLedger]] = relationship(back_populates="position")
    redemption: Mapped[WhaleRedemption | None] = relationship(
        back_populates="position", uselist=False
    )


class WhaleOrder(Base):
    __tablename__ = "whale_orders"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_whale_orders_idempotency"),
        Index("ix_whale_orders_position_created", "position_id", "created_at"),
        Index("ix_whale_orders_status_updated", "status", "updated_at"),
        Index(
            "ix_whale_orders_auto_market_usage",
            "condition_id",
            "source",
            "side",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[int | None] = mapped_column(
        ForeignKey("whale_follow_positions.id", ondelete="SET NULL"), nullable=True
    )
    entry_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="follow")
    execution_wallet: Mapped[str | None] = mapped_column(String(42), nullable=True)
    order_type: Mapped[str] = mapped_column(String(3), nullable=False, default="FAK")
    source_wallet: Mapped[str | None] = mapped_column(String(42), nullable=True)
    asset_id: Mapped[str] = mapped_column(String(100), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(66), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(String(200), nullable=False)
    outcome_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    neg_risk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    requested_size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    requested_usdc: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    limit_price: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    reference_price: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    whale_avg_price: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    filled_size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    filled_usdc: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    fee_usdc: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="planned")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    signed_order_hash: Mapped[str | None] = mapped_column(String(100), nullable=True)
    execution_provider: Mapped[str] = mapped_column(
        String(30), nullable=False, default="unified_sdk"
    )
    external_order_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    external_trade_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    position: Mapped[WhaleFollowPosition | None] = relationship(back_populates="orders")
    fills: Mapped[list[WhaleFill]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )
    ledger_entries: Mapped[list[WhaleFollowLedger]] = relationship(back_populates="order")


class WhaleFill(Base):
    __tablename__ = "whale_fills"
    __table_args__ = (UniqueConstraint("fingerprint", name="uq_whale_fills_fingerprint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("whale_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    external_trade_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    transaction_hash: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bucket_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    settlement_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    price: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    amount: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    fee_usdc: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    order: Mapped[WhaleOrder] = relationship(back_populates="fills")


class WhaleFollowLedger(Base):
    __tablename__ = "whale_follow_ledger"
    __table_args__ = (
        UniqueConstraint("external_event_key", name="uq_whale_ledger_external_event"),
        Index("ix_whale_ledger_position_time", "position_id", "timestamp"),
        Index("ix_whale_ledger_time", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("whale_follow_positions.id", ondelete="RESTRICT"), nullable=False
    )
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("whale_orders.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="follow")
    external_event_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    price: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    amount_usdc: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    fee_usdc: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    realized_pnl: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    transaction_hash: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    position: Mapped[WhaleFollowPosition] = relationship(back_populates="ledger_entries")
    order: Mapped[WhaleOrder | None] = relationship(back_populates="ledger_entries")


class WhaleRedemption(Base):
    __tablename__ = "whale_redemptions"
    __table_args__ = (UniqueConstraint("position_id", name="uq_whale_redemptions_position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("whale_follow_positions.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    payout_usdc: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    transaction_hash: Mapped[str | None] = mapped_column(String(100), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    position: Mapped[WhaleFollowPosition] = relationship(back_populates="redemption")
