"""Add configurable whale auto-follow strategies and durable decisions.

Revision ID: 0036_whale_auto_follow_strategies
Revises: 0035_whale_manual_position_adoption
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_whale_auto_follow_strategies"
down_revision: str | None = "0035_whale_manual_position_adoption"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DECIMAL = sa.Numeric(38, 18)


def upgrade() -> None:
    with op.batch_alter_table("whale_settings") as batch:
        batch.add_column(
            sa.Column(
                "new_account_auto_follow_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "new_account_auto_follow_amount_usdc",
                DECIMAL,
                nullable=False,
                server_default="5",
            )
        )
        batch.add_column(
            sa.Column(
                "new_account_auto_follow_min_price",
                DECIMAL,
                nullable=False,
                server_default="0.65",
            )
        )
        batch.add_column(
            sa.Column(
                "new_account_auto_follow_max_price",
                DECIMAL,
                nullable=False,
                server_default="0.80",
            )
        )
        batch.add_column(
            sa.Column(
                "new_account_auto_follow_categories_json",
                sa.Text(),
                nullable=False,
                server_default='["sports"]',
            )
        )
        batch.add_column(
            sa.Column(
                "large_amount_auto_follow_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "large_amount_auto_follow_amount_usdc",
                DECIMAL,
                nullable=False,
                server_default="10",
            )
        )
        batch.add_column(
            sa.Column(
                "large_amount_auto_follow_min_price",
                DECIMAL,
                nullable=False,
                server_default="0.60",
            )
        )
        batch.add_column(
            sa.Column(
                "large_amount_auto_follow_max_price",
                DECIMAL,
                nullable=False,
                server_default="0.80",
            )
        )
        batch.add_column(
            sa.Column(
                "large_amount_auto_follow_categories_json",
                sa.Text(),
                nullable=False,
                server_default='["sports"]',
            )
        )
        batch.create_check_constraint(
            "ck_whale_settings_new_auto_amount",
            "new_account_auto_follow_amount_usdc > 0",
        )
        batch.create_check_constraint(
            "ck_whale_settings_new_auto_prices",
            "new_account_auto_follow_min_price > 0 "
            "AND new_account_auto_follow_min_price <= new_account_auto_follow_max_price "
            "AND new_account_auto_follow_max_price < 1",
        )
        batch.create_check_constraint(
            "ck_whale_settings_large_auto_amount",
            "large_amount_auto_follow_amount_usdc > 0",
        )
        batch.create_check_constraint(
            "ck_whale_settings_large_auto_prices",
            "large_amount_auto_follow_min_price > 0 "
            "AND large_amount_auto_follow_min_price <= large_amount_auto_follow_max_price "
            "AND large_amount_auto_follow_max_price < 1",
        )

    with op.batch_alter_table("whale_orders") as batch:
        batch.add_column(
            sa.Column("source", sa.String(length=30), nullable=False, server_default="follow")
        )

    op.create_table(
        "whale_auto_follow_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entry_id",
            sa.Integer(),
            sa.ForeignKey("whale_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("proxy_wallet", sa.String(length=42), nullable=False),
        sa.Column("asset_id", sa.String(length=100), nullable=False),
        sa.Column("condition_id", sa.String(length=66), nullable=False),
        sa.Column("outcome", sa.String(length=200), nullable=False),
        sa.Column("outcome_index", sa.Integer(), nullable=True),
        sa.Column("matched_rules_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("selected_rule", sa.String(length=30), nullable=True),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("configured_amount_usdc", DECIMAL, nullable=True),
        sa.Column("configured_min_price", DECIMAL, nullable=True),
        sa.Column("configured_max_price", DECIMAL, nullable=True),
        sa.Column("observed_best_ask", DECIMAL, nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "buy_order_id",
            sa.Integer(),
            sa.ForeignKey("whale_orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "latest_sell_order_id",
            sa.Integer(),
            sa.ForeignKey("whale_orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "asset_id",
            "proxy_wallet",
            name="uq_whale_auto_decision_asset_wallet",
        ),
    )
    op.create_index(
        "ix_whale_auto_decision_status_created",
        "whale_auto_follow_decisions",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_whale_auto_decision_condition",
        "whale_auto_follow_decisions",
        ["condition_id"],
    )

    op.create_table(
        "whale_auto_market_locks",
        sa.Column("condition_id", sa.String(length=66), primary_key=True),
        sa.Column(
            "trigger_entry_id",
            sa.Integer(),
            sa.ForeignKey("whale_entries.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("trigger_wallet", sa.String(length=42), nullable=True),
        sa.Column("trigger_asset_id", sa.String(length=100), nullable=True),
        sa.Column("trigger_outcome", sa.String(length=200), nullable=True),
        sa.Column("trigger_amount_usdc", DECIMAL, nullable=True),
        sa.Column("trigger_rules_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "exit_status",
            sa.String(length=30),
            nullable=False,
            server_default="not_required",
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_whale_auto_market_lock_exit",
        "whale_auto_market_locks",
        ["exit_status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_whale_auto_market_lock_exit", table_name="whale_auto_market_locks")
    op.drop_table("whale_auto_market_locks")
    op.drop_index("ix_whale_auto_decision_condition", table_name="whale_auto_follow_decisions")
    op.drop_index(
        "ix_whale_auto_decision_status_created",
        table_name="whale_auto_follow_decisions",
    )
    op.drop_table("whale_auto_follow_decisions")
    with op.batch_alter_table("whale_orders") as batch:
        batch.drop_column("source")
    with op.batch_alter_table("whale_settings") as batch:
        batch.drop_constraint("ck_whale_settings_large_auto_prices", type_="check")
        batch.drop_constraint("ck_whale_settings_large_auto_amount", type_="check")
        batch.drop_constraint("ck_whale_settings_new_auto_prices", type_="check")
        batch.drop_constraint("ck_whale_settings_new_auto_amount", type_="check")
        batch.drop_column("large_amount_auto_follow_categories_json")
        batch.drop_column("large_amount_auto_follow_max_price")
        batch.drop_column("large_amount_auto_follow_min_price")
        batch.drop_column("large_amount_auto_follow_amount_usdc")
        batch.drop_column("large_amount_auto_follow_enabled")
        batch.drop_column("new_account_auto_follow_categories_json")
        batch.drop_column("new_account_auto_follow_max_price")
        batch.drop_column("new_account_auto_follow_min_price")
        batch.drop_column("new_account_auto_follow_amount_usdc")
        batch.drop_column("new_account_auto_follow_enabled")
