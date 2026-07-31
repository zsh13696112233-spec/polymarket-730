from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
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


class Base(DeclarativeBase):
    pass


class WatchedWallet(Base):
    __tablename__ = "watched_wallets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    address: Mapped[str] = mapped_column(String(42), nullable=False)
    proxy_wallet: Mapped[str] = mapped_column(String(42), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
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
    __table_args__ = (Index("ix_events_wallet_id_desc", "wallet_id", "id"),)

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
    imported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    wallet: Mapped[WatchedWallet] = relationship(back_populates="trades")
