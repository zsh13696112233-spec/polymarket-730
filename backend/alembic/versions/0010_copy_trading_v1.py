"""Add the single-account copy-trading engine tables.

Revision ID: 0010_copy_trading_v1
Revises: 0009_global_copy_ratio
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_copy_trading_v1"
down_revision: str | None = "0009_global_copy_ratio"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DECIMAL = sa.Numeric(38, 18)


def upgrade() -> None:
    op.create_table(
        "execution_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "wallet_id",
            sa.Integer(),
            sa.ForeignKey("watched_wallets.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("signer_address", sa.String(42), nullable=True),
        sa.Column("funder_address", sa.String(42), nullable=True),
        sa.Column("signature_type", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("keychain_service", sa.String(200), nullable=True),
        sa.Column("keychain_account", sa.String(200), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="unconfigured"),
        sa.Column("budget_usdc", DECIMAL, nullable=False, server_default="400"),
        sa.Column("cash_reserve_usdc", DECIMAL, nullable=False, server_default="240"),
        sa.Column("max_total_exposure_usdc", DECIMAL, nullable=False, server_default="160"),
        sa.Column("daily_buy_limit_usdc", DECIMAL, nullable=False, server_default="80"),
        sa.Column("daily_loss_limit_usdc", DECIMAL, nullable=False, server_default="40"),
        sa.Column("auto_redeem", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("collateral_balance", DECIMAL, nullable=True),
        sa.Column("last_balance_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_execution_accounts_singleton"),
        sa.CheckConstraint("budget_usdc >= 0", name="ck_execution_accounts_budget"),
        sa.CheckConstraint("cash_reserve_usdc >= 0", name="ck_execution_accounts_reserve"),
        sa.CheckConstraint(
            "max_total_exposure_usdc >= 0",
            name="ck_execution_accounts_total_exposure",
        ),
        sa.CheckConstraint(
            "daily_buy_limit_usdc >= 0",
            name="ck_execution_accounts_daily_buy_limit",
        ),
        sa.CheckConstraint(
            "daily_loss_limit_usdc >= 0",
            name="ck_execution_accounts_daily_loss_limit",
        ),
    )

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
        sa.Column("market_scope", sa.String(30), nullable=False, server_default="temperature"),
        sa.Column("copy_ratio_percent", sa.Numeric(5, 2), nullable=False, server_default="2"),
        sa.Column("base_bucket_cap_usdc", DECIMAL, nullable=False, server_default="20"),
        sa.Column("strong_threshold_usdc", DECIMAL, nullable=False, server_default="1000"),
        sa.Column("strong_bucket_cap_usdc", DECIMAL, nullable=False, server_default="40"),
        sa.Column("event_cap_usdc", DECIMAL, nullable=False, server_default="60"),
        sa.Column("settlement_day_cap_usdc", DECIMAL, nullable=False, server_default="100"),
        sa.Column("total_exposure_cap_usdc", DECIMAL, nullable=False, server_default="160"),
        sa.Column("daily_buy_limit_usdc", DECIMAL, nullable=False, server_default="80"),
        sa.Column("daily_loss_limit_usdc", DECIMAL, nullable=False, server_default="40"),
        sa.Column("price_tolerance_ticks", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("price_tolerance_percent", sa.Numeric(5, 2), nullable=False, server_default="3"),
        sa.Column("order_ttl_minutes", sa.Integer(), nullable=False, server_default="360"),
        sa.Column("close_buffer_minutes", sa.Integer(), nullable=False, server_default="15"),
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
            "base_bucket_cap_usdc <= strong_bucket_cap_usdc",
            name="ck_copy_subscriptions_bucket_caps",
        ),
        sa.CheckConstraint(
            "strong_bucket_cap_usdc <= event_cap_usdc",
            name="ck_copy_subscriptions_event_cap",
        ),
        sa.CheckConstraint(
            "event_cap_usdc <= settlement_day_cap_usdc",
            name="ck_copy_subscriptions_day_cap",
        ),
        sa.CheckConstraint(
            "settlement_day_cap_usdc <= total_exposure_cap_usdc",
            name="ck_copy_subscriptions_total_cap",
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
        sa.Column("attributed_size", DECIMAL, nullable=False, server_default="0"),
        sa.Column("attributed_cost", DECIMAL, nullable=False, server_default="0"),
        sa.Column("reserved_buy_usdc", DECIMAL, nullable=False, server_default="0"),
        sa.Column("pending_target_usdc", DECIMAL, nullable=False, server_default="0"),
        sa.Column("dust_size", DECIMAL, nullable=False, server_default="0"),
        sa.Column("leader_size", DECIMAL, nullable=False, server_default="0"),
        sa.Column("leader_remaining_cost", DECIMAL, nullable=False, server_default="0"),
        sa.Column("realized_pnl", DECIMAL, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("subscription_id", "asset_id", name="uq_copy_positions_asset"),
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
            nullable=False,
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
        sa.Column("asset_id", sa.String(100), nullable=False),
        sa.Column("condition_id", sa.String(66), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("order_type", sa.String(10), nullable=False, server_default="GTD"),
        sa.Column("requested_size", DECIMAL, nullable=False),
        sa.Column("requested_usdc", DECIMAL, nullable=False),
        sa.Column("limit_price", DECIMAL, nullable=False),
        sa.Column("filled_size", DECIMAL, nullable=False, server_default="0"),
        sa.Column("filled_usdc", DECIMAL, nullable=False, server_default="0"),
        sa.Column("fee_usdc", DECIMAL, nullable=False, server_default="0"),
        sa.Column("status", sa.String(30), nullable=False, server_default="planned"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("external_order_id", sa.String(200), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_copy_orders_idempotency"),
    )
    op.create_index("ix_copy_orders_subscription_id", "copy_orders", ["subscription_id"])
    op.create_index(
        "ix_copy_orders_subscription_created",
        "copy_orders",
        ["subscription_id", "created_at"],
    )
    op.create_index("ix_copy_orders_status_expires", "copy_orders", ["status", "expires_at"])

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
        "ix_copy_ledger_subscription_time",
        "copy_ledger",
        ["subscription_id", "timestamp"],
    )

    op.create_table(
        "copy_redemptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "copy_position_id",
            sa.Integer(),
            sa.ForeignKey("copy_positions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("size", DECIMAL, nullable=False),
        sa.Column("payout_usdc", DECIMAL, nullable=True),
        sa.Column("transaction_hash", sa.String(100), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("copy_position_id", name="uq_copy_redemptions_position"),
    )


def downgrade() -> None:
    op.drop_table("copy_redemptions")
    op.drop_index("ix_copy_ledger_subscription_time", table_name="copy_ledger")
    op.drop_table("copy_ledger")
    op.drop_index("ix_copy_fills_order_id", table_name="copy_fills")
    op.drop_table("copy_fills")
    op.drop_index("ix_copy_orders_status_expires", table_name="copy_orders")
    op.drop_index("ix_copy_orders_subscription_created", table_name="copy_orders")
    op.drop_index("ix_copy_orders_subscription_id", table_name="copy_orders")
    op.drop_table("copy_orders")
    op.drop_index("ix_copy_positions_subscription_event", table_name="copy_positions")
    op.drop_index("ix_copy_positions_subscription_id", table_name="copy_positions")
    op.drop_table("copy_positions")
    op.drop_index("uq_copy_subscriptions_single_live", table_name="copy_subscriptions")
    op.drop_index("ix_copy_subscriptions_tracked_wallet_id", table_name="copy_subscriptions")
    op.drop_table("copy_subscriptions")
    op.drop_table("execution_accounts")
