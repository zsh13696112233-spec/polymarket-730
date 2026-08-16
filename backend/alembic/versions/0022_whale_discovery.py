"""Add whale discovery and manually followed position tables.

Revision ID: 0022_whale_discovery
Revises: 0021_force_buy_overrides
Create Date: 2026-08-16
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0022_whale_discovery"
down_revision: str | None = "0021_force_buy_overrides"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DECIMAL = sa.Numeric(38, 18)
PERCENT = sa.Numeric(5, 2)


def upgrade() -> None:
    whale_settings = op.create_table(
        "whale_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("window_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("collect_filter_amount_usdc", DECIMAL, nullable=False, server_default="1000"),
        sa.Column("single_trade_threshold_usdc", DECIMAL, nullable=False, server_default="10000"),
        sa.Column("cumulative_threshold_usdc", DECIMAL, nullable=False, server_default="10000"),
        sa.Column("min_liquidity_usdc", DECIMAL, nullable=False, server_default="5000"),
        sa.Column("min_remaining_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("max_price_delta_cents", DECIMAL, nullable=False, server_default="5"),
        sa.Column("holding_ratio_threshold", PERCENT, nullable=False, server_default="80"),
        sa.Column("exited_ratio_threshold", PERCENT, nullable=False, server_default="20"),
        sa.Column("scan_interval_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("profile_cache_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("trade_retention_hours", sa.Integer(), nullable=False, server_default="72"),
        sa.Column("max_follow_amount_usdc", DECIMAL, nullable=False, server_default="200"),
        sa.Column("follow_slippage_cents", DECIMAL, nullable=False, server_default="3"),
        sa.Column("sell_slippage_cents", DECIMAL, nullable=False, server_default="3"),
        sa.Column("auto_redeem", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_scan_at", sa.DateTime(), nullable=True),
        sa.Column("last_scan_error", sa.Text(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_whale_settings_singleton"),
        sa.CheckConstraint(
            "single_trade_threshold_usdc >= collect_filter_amount_usdc",
            name="ck_whale_settings_single_threshold",
        ),
        sa.CheckConstraint(
            "cumulative_threshold_usdc >= collect_filter_amount_usdc",
            name="ck_whale_settings_cumulative_threshold",
        ),
        sa.CheckConstraint(
            "exited_ratio_threshold >= 0 "
            "AND exited_ratio_threshold < holding_ratio_threshold "
            "AND holding_ratio_threshold <= 100",
            name="ck_whale_settings_ratio_thresholds",
        ),
        sa.CheckConstraint(
            "max_follow_amount_usdc > 0",
            name="ck_whale_settings_max_follow_amount",
        ),
    )

    op.create_table(
        "whale_trades",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("proxy_wallet", sa.String(42), nullable=False),
        sa.Column("asset_id", sa.String(100), nullable=False),
        sa.Column("condition_id", sa.String(66), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("size", DECIMAL, nullable=False),
        sa.Column("price", DECIMAL, nullable=False),
        sa.Column("amount", DECIMAL, nullable=False),
        sa.Column("outcome", sa.String(200), nullable=False),
        sa.Column("outcome_index", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("market_slug", sa.String(500), nullable=False),
        sa.Column("event_slug", sa.String(500), nullable=False),
        sa.Column("icon_url", sa.Text(), nullable=True),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("transaction_hash", sa.String(100), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("fingerprint", name="uq_whale_trades_fingerprint"),
    )
    op.create_index("ix_whale_trades_time", "whale_trades", ["timestamp"])
    op.create_index(
        "ix_whale_trades_wallet_asset_time",
        "whale_trades",
        ["proxy_wallet", "asset_id", "timestamp"],
    )
    op.create_index(
        "ix_whale_trades_condition_time",
        "whale_trades",
        ["condition_id", "timestamp"],
    )

    op.create_table(
        "whale_markets",
        sa.Column("condition_id", sa.String(66), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("market_slug", sa.String(500), nullable=True),
        sa.Column("event_slug", sa.String(500), nullable=True),
        sa.Column("icon_url", sa.Text(), nullable=True),
        sa.Column("outcomes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("outcome_prices_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("clob_token_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("tags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("closed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("accepting_orders", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("neg_risk", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("end_date", sa.DateTime(), nullable=True),
        sa.Column("end_date_is_date_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("liquidity", DECIMAL, nullable=False, server_default="0"),
        sa.Column("volume_24h", DECIMAL, nullable=False, server_default="0"),
        sa.Column("best_bid", DECIMAL, nullable=True),
        sa.Column("best_ask", DECIMAL, nullable=True),
        sa.Column("order_min_size", DECIMAL, nullable=False, server_default="5"),
        sa.Column("tick_size", DECIMAL, nullable=False, server_default="0.01"),
        sa.Column("fee_rate", DECIMAL, nullable=False, server_default="0"),
        sa.Column("fee_exponent", DECIMAL, nullable=False, server_default="0"),
        sa.Column("refreshed_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_whale_markets_refreshed", "whale_markets", ["refreshed_at"])
    op.create_index("ix_whale_markets_end_date", "whale_markets", ["end_date"])

    op.create_table(
        "whale_tags",
        sa.Column("id", sa.String(20), primary_key=True),
        sa.Column("slug", sa.String(200), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("market_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("refreshed_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("slug", name="uq_whale_tags_slug"),
    )

    op.create_table(
        "whale_wallets",
        sa.Column("proxy_wallet", sa.String(42), primary_key=True),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("pseudonym", sa.String(200), nullable=True),
        sa.Column("profile_created_at", sa.DateTime(), nullable=True),
        sa.Column("verified_badge", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("taker_tier", sa.Integer(), nullable=True),
        sa.Column("taker_tier_name", sa.String(50), nullable=True),
        sa.Column("weighted_volume", DECIMAL, nullable=True),
        sa.Column("profile_missing", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("refreshed_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "whale_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("proxy_wallet", sa.String(42), nullable=False),
        sa.Column("asset_id", sa.String(100), nullable=False),
        sa.Column("condition_id", sa.String(66), nullable=False),
        sa.Column("outcome", sa.String(200), nullable=False),
        sa.Column("outcome_index", sa.Integer(), nullable=False),
        sa.Column("gross_buy_usdc", DECIMAL, nullable=False),
        sa.Column("gross_buy_size", DECIMAL, nullable=False),
        sa.Column("sold_size", DECIMAL, nullable=False, server_default="0"),
        sa.Column("sold_usdc", DECIMAL, nullable=False, server_default="0"),
        sa.Column("net_size", DECIMAL, nullable=False),
        sa.Column("net_ratio", PERCENT, nullable=False),
        sa.Column("avg_buy_price", DECIMAL, nullable=False),
        sa.Column("max_single_usdc", DECIMAL, nullable=False),
        sa.Column("trade_count", sa.Integer(), nullable=False),
        sa.Column("first_buy_at", sa.DateTime(), nullable=False),
        sa.Column("last_buy_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("hedged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("window_start", sa.DateTime(), nullable=False),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("proxy_wallet", "asset_id", name="uq_whale_entries_wallet_asset"),
        sa.CheckConstraint(
            "net_ratio >= 0 AND net_ratio <= 100",
            name="ck_whale_entries_net_ratio_percent",
        ),
    )
    op.create_index("ix_whale_entries_condition", "whale_entries", ["condition_id"])
    op.create_index(
        "ix_whale_entries_status_amount",
        "whale_entries",
        ["status", "gross_buy_usdc"],
    )

    op.create_table(
        "whale_follow_positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("asset_id", sa.String(100), nullable=False),
        sa.Column("condition_id", sa.String(66), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(200), nullable=False),
        sa.Column("outcome_index", sa.Integer(), nullable=True),
        sa.Column("neg_risk", sa.Boolean(), nullable=True),
        sa.Column("market_slug", sa.String(500), nullable=True),
        sa.Column("event_slug", sa.String(500), nullable=True),
        sa.Column("icon_url", sa.Text(), nullable=True),
        sa.Column("source_wallet", sa.String(42), nullable=True),
        sa.Column("source_whale_avg_price", DECIMAL, nullable=True),
        sa.Column("cycle_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("size", DECIMAL, nullable=False, server_default="0"),
        sa.Column("cost_usdc", DECIMAL, nullable=False, server_default="0"),
        sa.Column("lifetime_bought_size", DECIMAL, nullable=False, server_default="0"),
        sa.Column("lifetime_bought_usdc", DECIMAL, nullable=False, server_default="0"),
        sa.Column("lifetime_sold_size", DECIMAL, nullable=False, server_default="0"),
        sa.Column("lifetime_sold_usdc", DECIMAL, nullable=False, server_default="0"),
        sa.Column("lifetime_fee_usdc", DECIMAL, nullable=False, server_default="0"),
        sa.Column("realized_pnl", DECIMAL, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="opening"),
        sa.Column("opened_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("asset_id", "cycle_no", name="uq_whale_positions_asset_cycle"),
    )
    op.create_index("ix_whale_positions_status", "whale_follow_positions", ["status"])
    op.create_index("ix_whale_positions_condition", "whale_follow_positions", ["condition_id"])

    op.create_table(
        "whale_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "position_id",
            sa.Integer(),
            sa.ForeignKey("whale_follow_positions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("entry_id", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("source_wallet", sa.String(42), nullable=True),
        sa.Column("asset_id", sa.String(100), nullable=False),
        sa.Column("condition_id", sa.String(66), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(200), nullable=False),
        sa.Column("outcome_index", sa.Integer(), nullable=True),
        sa.Column("neg_risk", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("requested_size", DECIMAL, nullable=False, server_default="0"),
        sa.Column("requested_usdc", DECIMAL, nullable=False, server_default="0"),
        sa.Column("limit_price", DECIMAL, nullable=False),
        sa.Column("reference_price", DECIMAL, nullable=True),
        sa.Column("whale_avg_price", DECIMAL, nullable=True),
        sa.Column("filled_size", DECIMAL, nullable=False, server_default="0"),
        sa.Column("filled_usdc", DECIMAL, nullable=False, server_default="0"),
        sa.Column("fee_usdc", DECIMAL, nullable=False, server_default="0"),
        sa.Column("status", sa.String(30), nullable=False, server_default="planned"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("signed_order_hash", sa.String(100), nullable=True),
        sa.Column(
            "execution_provider", sa.String(30), nullable=False, server_default="unified_sdk"
        ),
        sa.Column("external_order_id", sa.String(200), nullable=True),
        sa.Column("external_trade_id", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_whale_orders_idempotency"),
    )
    op.create_index(
        "ix_whale_orders_position_created", "whale_orders", ["position_id", "created_at"]
    )
    op.create_index("ix_whale_orders_status_updated", "whale_orders", ["status", "updated_at"])

    op.create_table(
        "whale_fills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("whale_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("external_trade_id", sa.String(200), nullable=True),
        sa.Column("transaction_hash", sa.String(100), nullable=True),
        sa.Column("bucket_index", sa.Integer(), nullable=True),
        sa.Column("settlement_status", sa.String(30), nullable=True),
        sa.Column("size", DECIMAL, nullable=False),
        sa.Column("price", DECIMAL, nullable=False),
        sa.Column("amount", DECIMAL, nullable=False),
        sa.Column("fee_usdc", DECIMAL, nullable=False, server_default="0"),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("fingerprint", name="uq_whale_fills_fingerprint"),
    )
    op.create_index("ix_whale_fills_order_id", "whale_fills", ["order_id"])

    op.create_table(
        "whale_follow_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "position_id",
            sa.Integer(),
            sa.ForeignKey("whale_follow_positions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("whale_orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("size", DECIMAL, nullable=False, server_default="0"),
        sa.Column("price", DECIMAL, nullable=True),
        sa.Column("amount_usdc", DECIMAL, nullable=False, server_default="0"),
        sa.Column("fee_usdc", DECIMAL, nullable=False, server_default="0"),
        sa.Column("realized_pnl", DECIMAL, nullable=False, server_default="0"),
        sa.Column("transaction_hash", sa.String(100), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_whale_ledger_position_time",
        "whale_follow_ledger",
        ["position_id", "timestamp"],
    )
    op.create_index("ix_whale_ledger_time", "whale_follow_ledger", ["timestamp"])

    op.create_table(
        "whale_redemptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "position_id",
            sa.Integer(),
            sa.ForeignKey("whale_follow_positions.id", ondelete="RESTRICT"),
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
        sa.UniqueConstraint("position_id", name="uq_whale_redemptions_position"),
    )

    now = datetime.now(UTC).replace(tzinfo=None)
    op.bulk_insert(
        whale_settings,
        [
            {
                "id": 1,
                "enabled": True,
                "window_hours": 24,
                "collect_filter_amount_usdc": 1000,
                "single_trade_threshold_usdc": 10000,
                "cumulative_threshold_usdc": 10000,
                "min_liquidity_usdc": 5000,
                "min_remaining_minutes": 30,
                "max_price_delta_cents": 5,
                "holding_ratio_threshold": 80,
                "exited_ratio_threshold": 20,
                "scan_interval_seconds": 60,
                "profile_cache_hours": 24,
                "trade_retention_hours": 72,
                "max_follow_amount_usdc": 200,
                "follow_slippage_cents": 3,
                "sell_slippage_cents": 3,
                "auto_redeem": True,
                "last_scan_at": None,
                "last_scan_error": None,
                "consecutive_failures": 0,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )


def downgrade() -> None:
    connection = op.get_bind()
    open_positions = connection.scalar(
        sa.text("SELECT COUNT(*) FROM whale_follow_positions WHERE size > 0")
    )
    if int(open_positions or 0) > 0:
        raise RuntimeError("存在未平仓的巨鲸跟单持仓，拒绝删除真实资金记录")

    op.drop_table("whale_redemptions")
    op.drop_table("whale_follow_ledger")
    op.drop_table("whale_fills")
    op.drop_table("whale_orders")
    op.drop_table("whale_follow_positions")
    op.drop_table("whale_entries")
    op.drop_table("whale_wallets")
    op.drop_table("whale_tags")
    op.drop_table("whale_markets")
    op.drop_table("whale_trades")
    op.drop_table("whale_settings")
