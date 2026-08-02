"""Switch copy trading to fast public-trade signals and FAK market orders.

Revision ID: 0011_fast_copy_trading
Revises: 0010_copy_trading_v1
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_fast_copy_trading"
down_revision: str | None = "0010_copy_trading_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DECIMAL = sa.Numeric(38, 18)
PERCENT = sa.Numeric(5, 2)


def upgrade() -> None:
    op.add_column(
        "copy_subscriptions",
        sa.Column("market_slippage_cents", PERCENT, nullable=False, server_default="5"),
    )
    op.add_column(
        "copy_subscriptions",
        sa.Column("fast_poll_started_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "copy_subscriptions",
        sa.Column("last_trade_poll_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "copy_subscriptions",
        sa.Column("last_trade_error", sa.Text(), nullable=True),
    )

    for _name, column in (
        ("title", sa.Column("title", sa.Text(), nullable=True)),
        ("outcome", sa.Column("outcome", sa.String(200), nullable=True)),
        ("outcome_index", sa.Column("outcome_index", sa.Integer(), nullable=True)),
        ("event_slug", sa.Column("event_slug", sa.String(500), nullable=True)),
        ("market_slug", sa.Column("market_slug", sa.String(500), nullable=True)),
    ):
        op.add_column("wallet_trades", column)

    op.add_column(
        "copy_orders",
        sa.Column("reference_price", DECIMAL, nullable=True),
    )

    op.create_table(
        "copy_leader_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "subscription_id",
            sa.Integer(),
            sa.ForeignKey("copy_subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("asset_id", sa.String(100), nullable=False),
        sa.Column("condition_id", sa.String(66), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(200), nullable=False),
        sa.Column("outcome_index", sa.Integer(), nullable=True),
        sa.Column("event_slug", sa.String(500), nullable=True),
        sa.Column("market_slug", sa.String(500), nullable=True),
        sa.Column("settlement_date", sa.String(10), nullable=True),
        sa.Column("size", DECIMAL, nullable=False, server_default="0"),
        sa.Column("remaining_cost", DECIMAL, nullable=False, server_default="0"),
        sa.Column("last_trade_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "subscription_id",
            "asset_id",
            name="uq_copy_leader_states_asset",
        ),
    )
    op.create_index(
        "ix_copy_leader_states_subscription",
        "copy_leader_states",
        ["subscription_id"],
    )

    op.create_table(
        "copy_trade_signals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "subscription_id",
            sa.Integer(),
            sa.ForeignKey("copy_subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "wallet_trade_id",
            sa.Integer(),
            sa.ForeignKey("wallet_trades.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("copy_orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "subscription_id",
            "wallet_trade_id",
            name="uq_copy_trade_signals_subscription_trade",
        ),
    )
    op.create_index(
        "ix_copy_trade_signals_subscription_detected",
        "copy_trade_signals",
        ["subscription_id", "detected_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_copy_trade_signals_subscription_detected",
        table_name="copy_trade_signals",
    )
    op.drop_table("copy_trade_signals")
    op.drop_index(
        "ix_copy_leader_states_subscription",
        table_name="copy_leader_states",
    )
    op.drop_table("copy_leader_states")

    op.drop_column("copy_orders", "reference_price")
    for name in ("market_slug", "event_slug", "outcome_index", "outcome", "title"):
        op.drop_column("wallet_trades", name)
    for name in (
        "last_trade_error",
        "last_trade_poll_at",
        "fast_poll_started_at",
        "market_slippage_cents",
    ):
        op.drop_column("copy_subscriptions", name)
