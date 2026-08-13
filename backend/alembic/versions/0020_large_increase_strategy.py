"""Add the two-tier large increase copy strategy.

Revision ID: 0020_large_increase_strategy
Revises: 0019_unified_polymarket_sdk
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_large_increase_strategy"
down_revision: str | None = "0019_unified_polymarket_sdk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DECIMAL = sa.Numeric(38, 18)
PERCENT = sa.Numeric(5, 2)


def upgrade() -> None:
    with op.batch_alter_table("copy_subscriptions") as batch:
        batch.add_column(
            sa.Column("strategy_mode", sa.String(20), nullable=False, server_default="normal")
        )
        batch.add_column(
            sa.Column("base_entry_threshold_usdc", DECIMAL, nullable=False, server_default="100")
        )
        batch.add_column(
            sa.Column("base_entry_ratio_percent", PERCENT, nullable=False, server_default="10")
        )
        batch.add_column(
            sa.Column("tier_one_threshold_usdc", DECIMAL, nullable=False, server_default="50000")
        )
        batch.add_column(
            sa.Column("tier_one_ratio_percent", PERCENT, nullable=False, server_default="0.1")
        )
        batch.add_column(
            sa.Column("tier_two_threshold_usdc", DECIMAL, nullable=False, server_default="100000")
        )
        batch.add_column(
            sa.Column("tier_two_ratio_percent", PERCENT, nullable=False, server_default="0.2")
        )
        batch.create_check_constraint(
            "ck_copy_subscriptions_strategy_mode", "strategy_mode IN ('normal', 'large_increase')"
        )
        batch.create_check_constraint(
            "ck_copy_subscriptions_base_entry",
            "base_entry_threshold_usdc > 0 AND base_entry_ratio_percent > 0 "
            "AND base_entry_ratio_percent <= 100",
        )
        batch.create_check_constraint(
            "ck_copy_subscriptions_tier_one",
            "tier_one_threshold_usdc > 0 AND tier_one_ratio_percent > 0 "
            "AND tier_one_ratio_percent <= 100",
        )
        batch.create_check_constraint(
            "ck_copy_subscriptions_tier_two",
            "tier_two_threshold_usdc > tier_one_threshold_usdc "
            "AND tier_two_ratio_percent > 0 AND tier_two_ratio_percent <= 100",
        )


def downgrade() -> None:
    with op.batch_alter_table("copy_subscriptions") as batch:
        batch.drop_constraint("ck_copy_subscriptions_tier_two", type_="check")
        batch.drop_constraint("ck_copy_subscriptions_tier_one", type_="check")
        batch.drop_constraint("ck_copy_subscriptions_base_entry", type_="check")
        batch.drop_constraint("ck_copy_subscriptions_strategy_mode", type_="check")
        batch.drop_column("tier_two_ratio_percent")
        batch.drop_column("tier_two_threshold_usdc")
        batch.drop_column("tier_one_ratio_percent")
        batch.drop_column("tier_one_threshold_usdc")
        batch.drop_column("base_entry_ratio_percent")
        batch.drop_column("base_entry_threshold_usdc")
        batch.drop_column("strategy_mode")
