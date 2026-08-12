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
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

DECIMAL_TYPE = Numeric(38, 18)
PERCENT_TYPE = Numeric(5, 2)


class Base(DeclarativeBase):
    pass


class GlobalSettings(Base):
    __tablename__ = "global_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_global_settings_singleton"),
        CheckConstraint(
            "copy_ratio_percent >= 1 AND copy_ratio_percent <= 100",
            name="ck_global_settings_copy_ratio_percent",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    copy_ratio_percent: Mapped[Decimal] = mapped_column(
        PERCENT_TYPE,
        nullable=False,
        default=Decimal("10"),
    )


class WatchedWallet(Base):
    __tablename__ = "watched_wallets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    address: Mapped[str] = mapped_column(String(42), nullable=False)
    proxy_wallet: Mapped[str] = mapped_column(String(42), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    wallet_role: Mapped[str] = mapped_column(String(20), nullable=False, default="tracked")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    baseline_established: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="idle")
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    trade_history_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    trade_history_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    redemption_history_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    redemption_history_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    positions: Mapped[list[CurrentPosition]] = relationship(
        back_populates="wallet", cascade="all, delete-orphan"
    )
    candidates: Mapped[list[PositionChangeCandidate]] = relationship(
        back_populates="wallet", cascade="all, delete-orphan"
    )
    events: Mapped[list[PositionEvent]] = relationship(back_populates="wallet")
    trades: Mapped[list[WalletTrade]] = relationship(
        back_populates="wallet", cascade="all, delete-orphan"
    )


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
    wallet_id: Mapped[int] = mapped_column(
        ForeignKey("watched_wallets.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
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
    auto_redeem: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    collateral_balance: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    last_balance_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    wallet: Mapped[WatchedWallet] = relationship()


class CopySubscription(Base):
    __tablename__ = "copy_subscriptions"
    __table_args__ = (
        UniqueConstraint("tracked_wallet_id", name="uq_copy_subscriptions_tracked_wallet"),
        CheckConstraint(
            "copy_ratio_percent > 0 AND copy_ratio_percent <= 100",
            name="ck_copy_subscriptions_ratio",
        ),
        CheckConstraint(
            "position_cap_usdc > 0 AND position_cap_usdc <= total_exposure_cap_usdc",
            name="ck_copy_subscriptions_position_cap",
        ),
        CheckConstraint(
            "large_increase_threshold_usdc > 0",
            name="ck_copy_subscriptions_large_increase_threshold",
        ),
        CheckConstraint(
            "market_slippage_cents >= 0 AND market_slippage_cents <= 50",
            name="ck_copy_subscriptions_market_slippage",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tracked_wallet_id: Mapped[int] = mapped_column(
        ForeignKey("watched_wallets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="disabled")
    copy_ratio_percent: Mapped[Decimal] = mapped_column(
        PERCENT_TYPE, nullable=False, default=Decimal("10")
    )
    position_cap_usdc: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("20")
    )
    large_increase_threshold_usdc: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("100")
    )
    total_exposure_cap_usdc: Mapped[Decimal] = mapped_column(
        DECIMAL_TYPE, nullable=False, default=Decimal("160")
    )
    market_slippage_cents: Mapped[Decimal] = mapped_column(
        PERCENT_TYPE, nullable=False, default=Decimal("5")
    )
    baseline_event_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_processed_event_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    tracked_wallet: Mapped[WatchedWallet] = relationship()
    positions: Mapped[list[CopyPosition]] = relationship(
        back_populates="subscription", cascade="all, delete-orphan"
    )
    orders: Mapped[list[CopyOrder]] = relationship(back_populates="subscription")


class CopyPosition(Base):
    __tablename__ = "copy_positions"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id", "asset_id", "cycle_no", name="uq_copy_positions_asset_cycle"
        ),
        Index("ix_copy_positions_subscription_event", "subscription_id", "event_slug"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("copy_subscriptions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[str] = mapped_column(String(100), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(66), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(String(200), nullable=False)
    outcome_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    neg_risk: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    event_slug: Mapped[str | None] = mapped_column(String(500), nullable=True)
    settlement_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    cycle_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attributed_size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    attributed_cost: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    reserved_buy_usdc: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    realized_pnl: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    subscription: Mapped[CopySubscription] = relationship(back_populates="positions")


class CopyOrder(Base):
    __tablename__ = "copy_orders"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_copy_orders_idempotency"),
        Index("ix_copy_orders_subscription_created", "subscription_id", "created_at"),
        Index("ix_copy_orders_status_updated", "status", "updated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("copy_subscriptions.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    copy_position_id: Mapped[int | None] = mapped_column(
        ForeignKey("copy_positions.id", ondelete="SET NULL"), nullable=True
    )
    leader_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("position_events.id", ondelete="SET NULL"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="copy")
    signed_order_hash: Mapped[str | None] = mapped_column(String(100), nullable=True)
    execution_provider: Mapped[str | None] = mapped_column(String(30), nullable=True)
    asset_id: Mapped[str] = mapped_column(String(100), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(66), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    requested_size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    requested_usdc: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    leader_purchase_usdc: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    proportional_target_usdc: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    limit_price: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    reference_price: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    filled_size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    filled_usdc: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    fee_usdc: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="planned")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_order_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    external_trade_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    subscription: Mapped[CopySubscription] = relationship(back_populates="orders")
    fills: Mapped[list[CopyFill]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )


class CopyFill(Base):
    __tablename__ = "copy_fills"
    __table_args__ = (UniqueConstraint("fingerprint", name="uq_copy_fills_fingerprint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("copy_orders.id", ondelete="CASCADE"), nullable=False, index=True
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

    order: Mapped[CopyOrder] = relationship(back_populates="fills")


class CopyLedger(Base):
    __tablename__ = "copy_ledger"
    __table_args__ = (Index("ix_copy_ledger_subscription_time", "subscription_id", "timestamp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("copy_subscriptions.id", ondelete="RESTRICT"), nullable=False
    )
    copy_position_id: Mapped[int | None] = mapped_column(
        ForeignKey("copy_positions.id", ondelete="SET NULL"), nullable=True
    )
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("copy_orders.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    amount_usdc: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    realized_pnl: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False, default=0)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class CopyRedemptionExecution(Base):
    __tablename__ = "copy_redemption_executions"
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


class CopyRedemption(Base):
    __tablename__ = "copy_redemptions"
    __table_args__ = (UniqueConstraint("copy_position_id", name="uq_copy_redemptions_position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    copy_position_id: Mapped[int] = mapped_column(
        ForeignKey("copy_positions.id", ondelete="RESTRICT"), nullable=False
    )
    execution_id: Mapped[int | None] = mapped_column(
        ForeignKey("copy_redemption_executions.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    payout_usdc: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    transaction_hash: Mapped[str | None] = mapped_column(String(100), nullable=True)
    execution_provider: Mapped[str | None] = mapped_column(String(30), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class CurrentPosition(Base):
    __tablename__ = "current_positions"
    __table_args__ = (
        UniqueConstraint("wallet_id", "asset_id", name="uq_current_position_wallet_asset"),
        Index("ix_current_positions_wallet_value", "wallet_id", "current_value"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wallet_id: Mapped[int] = mapped_column(
        ForeignKey("watched_wallets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[str] = mapped_column(String(100), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(66), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(String(200), nullable=False)
    outcome_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    icon_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_slug: Mapped[str | None] = mapped_column(String(500), nullable=True)
    market_slug: Mapped[str | None] = mapped_column(String(500), nullable=True)
    size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    avg_price: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    current_price: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    initial_value: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    current_value: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    cash_pnl: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    percent_pnl: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    total_bought: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    missing_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    wallet: Mapped[WatchedWallet] = relationship(back_populates="positions")


class PositionChangeCandidate(Base):
    __tablename__ = "position_change_candidates"
    __table_args__ = (
        UniqueConstraint("wallet_id", "asset_id", name="uq_candidate_wallet_asset"),
        Index("ix_candidates_due", "last_changed_at", "hard_deadline_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wallet_id: Mapped[int] = mapped_column(
        ForeignKey("watched_wallets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[str] = mapped_column(String(100), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(66), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(String(200), nullable=False)
    event_slug: Mapped[str | None] = mapped_column(String(500), nullable=True)
    start_size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    latest_size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    before_avg_price: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    after_avg_price: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    latest_current_value: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    first_changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    hard_deadline_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    wallet: Mapped[WatchedWallet] = relationship(back_populates="candidates")


class PositionEvent(Base):
    __tablename__ = "position_events"
    __table_args__ = (
        UniqueConstraint(
            "source_fingerprint",
            name="uq_position_events_source_fingerprint",
        ),
        Index("ix_events_wallet_id_desc", "wallet_id", "id"),
        Index(
            "ix_events_wallet_settled_desc",
            "wallet_id",
            "settled_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wallet_id: Mapped[int] = mapped_column(
        ForeignKey("watched_wallets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    asset_id: Mapped[str] = mapped_column(String(100), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(66), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(String(200), nullable=False)
    event_slug: Mapped[str | None] = mapped_column(String(500), nullable=True)
    delta_size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    before_size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    after_size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    before_avg_price: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    after_avg_price: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    average_fill_price: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    current_value: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    reconciliation_status: Mapped[str] = mapped_column(String(30), nullable=False)
    first_detected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    settled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    source_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payout_amount: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    redemption_cost_basis: Mapped[Decimal | None] = mapped_column(DECIMAL_TYPE, nullable=True)
    transaction_hash: Mapped[str | None] = mapped_column(String(100), nullable=True)

    wallet: Mapped[WatchedWallet] = relationship(back_populates="events")
    fills: Mapped[list[PositionEventFill]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="PositionEventFill.timestamp.asc()",
    )


class PositionEventFill(Base):
    __tablename__ = "position_event_fills"
    __table_args__ = (UniqueConstraint("fingerprint", name="uq_position_event_fill_fingerprint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("position_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    price: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    amount: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    transaction_hash: Mapped[str | None] = mapped_column(String(100), nullable=True)

    event: Mapped[PositionEvent] = relationship(back_populates="fills")


class PositionOverlapPeriod(Base):
    __tablename__ = "position_overlap_periods"
    __table_args__ = (
        Index(
            "uq_overlap_periods_active_pair_asset",
            "my_wallet_id",
            "tracked_wallet_id",
            "asset_id",
            unique=True,
            sqlite_where=text("ended_at IS NULL"),
        ),
        Index(
            "ix_overlap_periods_tracked_asset_window",
            "tracked_wallet_id",
            "asset_id",
            "started_at",
            "ended_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    my_wallet_id: Mapped[int] = mapped_column(
        ForeignKey("watched_wallets.id", ondelete="RESTRICT"), nullable=False
    )
    tracked_wallet_id: Mapped[int] = mapped_column(
        ForeignKey("watched_wallets.id", ondelete="RESTRICT"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(String(100), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(66), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(String(200), nullable=False)
    event_slug: Mapped[str | None] = mapped_column(String(500), nullable=True)
    market_slug: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_my_size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    last_tracked_size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PositionOverlapAlert(Base):
    __tablename__ = "position_overlap_alerts"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_overlap_alerts_event"),
        Index(
            "ix_overlap_alerts_wallet_pair_desc",
            "my_wallet_id",
            "tracked_wallet_id",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    period_id: Mapped[int] = mapped_column(
        ForeignKey("position_overlap_periods.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("position_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    my_wallet_id: Mapped[int] = mapped_column(
        ForeignKey("watched_wallets.id", ondelete="RESTRICT"), nullable=False
    )
    tracked_wallet_id: Mapped[int] = mapped_column(
        ForeignKey("watched_wallets.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    period: Mapped[PositionOverlapPeriod] = relationship()
    event: Mapped[PositionEvent] = relationship()


class WalletTrade(Base):
    __tablename__ = "wallet_trades"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_wallet_trade_fingerprint"),
        Index("ix_wallet_trades_wallet_asset_time", "wallet_id", "asset_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wallet_id: Mapped[int] = mapped_column(
        ForeignKey("watched_wallets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(100), nullable=False)
    condition_id: Mapped[str] = mapped_column(String(66), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    size: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    price: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    amount: Mapped[Decimal] = mapped_column(DECIMAL_TYPE, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    transaction_hash: Mapped[str | None] = mapped_column(String(100), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(200), nullable=True)
    outcome_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_slug: Mapped[str | None] = mapped_column(String(500), nullable=True)
    market_slug: Mapped[str | None] = mapped_column(String(500), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    wallet: Mapped[WatchedWallet] = relationship(back_populates="trades")
