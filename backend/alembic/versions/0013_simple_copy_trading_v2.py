"""Reset legacy copy data and install the three-event V2 schema.

Revision ID: 0013_simple_copy_trading_v2
Revises: 0012_large_trade_fixed_shares
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_simple_copy_trading_v2"
down_revision: str | None = "0012_large_trade_fixed_shares"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DECIMAL = sa.Numeric(38, 18)


def upgrade() -> None:
    # This release intentionally starts copy trading from an empty baseline. The
    # ordinary wallet monitor tables (positions, events and public trades) are untouched.
    for table in (
        "copy_trade_signals",
        "copy_redemptions",
        "copy_ledger",
        "copy_fills",
        "copy_orders",
        "copy_positions",
        "copy_leader_states",
        "copy_subscriptions",
    ):
        op.drop_table(table)

    op.execute("UPDATE execution_accounts SET signature_type = 1")

    op.create_table(
        "copy_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tracked_wallet_id",
            sa.Integer(),
            sa.ForeignKey("watched_wallets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("mode", sa.String(20), nullable=False, server_default="paper"),
        sa.Column("state", sa.String(20), nullable=False, server_default="disabled"),
        sa.Column("copy_ratio_percent", sa.Numeric(5, 2), nullable=False, server_default="10"),
        sa.Column("position_cap_usdc", DECIMAL, nullable=False, server_default="20"),
        sa.Column("total_exposure_cap_usdc", DECIMAL, nullable=False, server_default="160"),
        sa.Column("market_slippage_cents", sa.Numeric(5, 2), nullable=False, server_default="5"),
        sa.Column("baseline_event_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_processed_event_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled_at", sa.DateTime(), nullable=True),
        sa.Column("last_processed_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tracked_wallet_id", name="uq_copy_subscriptions_tracked_wallet"),
        sa.CheckConstraint(
            "copy_ratio_percent > 0 AND copy_ratio_percent <= 100",
            name="ck_copy_subscriptions_ratio",
        ),
        sa.CheckConstraint(
            "position_cap_usdc > 0 AND position_cap_usdc <= total_exposure_cap_usdc",
            name="ck_copy_subscriptions_position_cap",
        ),
        sa.CheckConstraint(
            "market_slippage_cents >= 0 AND market_slippage_cents <= 50",
            name="ck_copy_subscriptions_market_slippage",
        ),
    )
    op.create_index(
        "ix_copy_subscriptions_tracked_wallet_id",
        "copy_subscriptions",
        ["tracked_wallet_id"],
    )
    op.create_index(
        "uq_copy_subscriptions_single_live",
        "copy_subscriptions",
        ["mode"],
        unique=True,
        sqlite_where=sa.text("mode = 'live' AND state != 'disabled'"),
    )

    op.create_table(
        "copy_positions",
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
        sa.Column("neg_risk", sa.Boolean(), nullable=True),
        sa.Column("event_slug", sa.String(500), nullable=True),
        sa.Column("settlement_date", sa.String(10), nullable=True),
        sa.Column("cycle_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("attributed_size", DECIMAL, nullable=False, server_default="0"),
        sa.Column("attributed_cost", DECIMAL, nullable=False, server_default="0"),
        sa.Column("reserved_buy_usdc", DECIMAL, nullable=False, server_default="0"),
        sa.Column("realized_pnl", DECIMAL, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "subscription_id", "asset_id", "cycle_no", name="uq_copy_positions_asset_cycle"
        ),
    )
    op.create_index("ix_copy_positions_subscription_id", "copy_positions", ["subscription_id"])
    op.create_index(
        "ix_copy_positions_subscription_event",
        "copy_positions",
        ["subscription_id", "event_slug"],
    )

    op.create_table(
        "copy_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "subscription_id",
            sa.Integer(),
            sa.ForeignKey("copy_subscriptions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "copy_position_id",
            sa.Integer(),
            sa.ForeignKey("copy_positions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "leader_event_id",
            sa.Integer(),
            sa.ForeignKey("position_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="copy"),
        sa.Column("signed_order_hash", sa.String(100), nullable=True),
        sa.Column("asset_id", sa.String(100), nullable=False),
        sa.Column("condition_id", sa.String(66), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("requested_size", DECIMAL, nullable=False),
        sa.Column("requested_usdc", DECIMAL, nullable=False),
        sa.Column("limit_price", DECIMAL, nullable=False),
        sa.Column("reference_price", DECIMAL, nullable=True),
        sa.Column("filled_size", DECIMAL, nullable=False, server_default="0"),
        sa.Column("filled_usdc", DECIMAL, nullable=False, server_default="0"),
        sa.Column("fee_usdc", DECIMAL, nullable=False, server_default="0"),
        sa.Column("status", sa.String(30), nullable=False, server_default="planned"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("external_order_id", sa.String(200), nullable=True),
        sa.Column("external_trade_id", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_copy_orders_idempotency"),
    )
    op.create_index("ix_copy_orders_subscription_id", "copy_orders", ["subscription_id"])
    op.create_index(
        "ix_copy_orders_subscription_created", "copy_orders", ["subscription_id", "created_at"]
    )
    op.create_index("ix_copy_orders_status_updated", "copy_orders", ["status", "updated_at"])

    op.create_table(
        "copy_fills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("copy_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("external_trade_id", sa.String(200), nullable=True),
        sa.Column("size", DECIMAL, nullable=False),
        sa.Column("price", DECIMAL, nullable=False),
        sa.Column("amount", DECIMAL, nullable=False),
        sa.Column("fee_usdc", DECIMAL, nullable=False, server_default="0"),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("fingerprint", name="uq_copy_fills_fingerprint"),
    )
    op.create_index("ix_copy_fills_order_id", "copy_fills", ["order_id"])

    op.create_table(
        "copy_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "subscription_id",
            sa.Integer(),
            sa.ForeignKey("copy_subscriptions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "copy_position_id",
            sa.Integer(),
            sa.ForeignKey("copy_positions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("copy_orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("amount_usdc", DECIMAL, nullable=False, server_default="0"),
        sa.Column("realized_pnl", DECIMAL, nullable=False, server_default="0"),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_copy_ledger_subscription_time", "copy_ledger", ["subscription_id", "timestamp"]
    )

    op.create_table(
        "copy_redemptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "copy_position_id",
            sa.Integer(),
            sa.ForeignKey("copy_positions.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("size", DECIMAL, nullable=False),
        sa.Column("payout_usdc", DECIMAL, nullable=True),
        sa.Column("transaction_hash", sa.String(100), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    raise RuntimeError("三事件 V2 会清空旧跟单数据，不能自动降级；请恢复迁移前数据库备份")
