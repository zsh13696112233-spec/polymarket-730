"""Add low-price sizing and shared market caps for whale auto-follow.

Revision ID: 0040_whale_auto_follow_risk_controls
Revises: 0039_platform_managed_redemption
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_whale_auto_follow_risk_controls"
down_revision: str | None = "0039_platform_managed_redemption"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DECIMAL = sa.Numeric(38, 18)


def upgrade() -> None:
    with op.batch_alter_table("whale_settings") as batch:
        batch.add_column(
            sa.Column("new_account_auto_follow_low_price_max_price", DECIMAL, nullable=True)
        )
        batch.add_column(
            sa.Column("new_account_auto_follow_low_price_amount_usdc", DECIMAL, nullable=True)
        )
        batch.add_column(
            sa.Column("large_amount_auto_follow_low_price_max_price", DECIMAL, nullable=True)
        )
        batch.add_column(
            sa.Column("large_amount_auto_follow_low_price_amount_usdc", DECIMAL, nullable=True)
        )
        batch.add_column(
            sa.Column("auto_follow_market_max_purchase_count", sa.Integer(), nullable=True)
        )
        batch.add_column(sa.Column("auto_follow_market_max_amount_usdc", DECIMAL, nullable=True))
        batch.create_check_constraint(
            "ck_whale_settings_new_auto_low_price",
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
        )
        batch.create_check_constraint(
            "ck_whale_settings_large_auto_low_price",
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
        )
        batch.create_check_constraint(
            "ck_whale_settings_auto_market_caps",
            "(auto_follow_market_max_purchase_count IS NULL "
            "AND auto_follow_market_max_amount_usdc IS NULL) OR "
            "(auto_follow_market_max_purchase_count IS NOT NULL AND "
            "auto_follow_market_max_amount_usdc IS NOT NULL AND "
            "auto_follow_market_max_purchase_count > 0 "
            "AND auto_follow_market_max_amount_usdc > 0)",
        )

    with op.batch_alter_table("whale_auto_follow_decisions") as batch:
        batch.add_column(sa.Column("configured_low_price_max_price", DECIMAL, nullable=True))
        batch.add_column(sa.Column("configured_low_price_amount_usdc", DECIMAL, nullable=True))
        batch.add_column(sa.Column("selected_amount_usdc", DECIMAL, nullable=True))

    op.create_index(
        "ix_whale_orders_auto_market_usage",
        "whale_orders",
        ["condition_id", "source", "side", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_whale_orders_auto_market_usage", table_name="whale_orders")
    with op.batch_alter_table("whale_auto_follow_decisions") as batch:
        batch.drop_column("selected_amount_usdc")
        batch.drop_column("configured_low_price_amount_usdc")
        batch.drop_column("configured_low_price_max_price")
    with op.batch_alter_table("whale_settings") as batch:
        batch.drop_constraint("ck_whale_settings_auto_market_caps", type_="check")
        batch.drop_constraint("ck_whale_settings_large_auto_low_price", type_="check")
        batch.drop_constraint("ck_whale_settings_new_auto_low_price", type_="check")
        batch.drop_column("auto_follow_market_max_amount_usdc")
        batch.drop_column("auto_follow_market_max_purchase_count")
        batch.drop_column("large_amount_auto_follow_low_price_amount_usdc")
        batch.drop_column("large_amount_auto_follow_low_price_max_price")
        batch.drop_column("new_account_auto_follow_low_price_amount_usdc")
        batch.drop_column("new_account_auto_follow_low_price_max_price")
