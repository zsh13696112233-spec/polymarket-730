"""Add a per-subscription threshold for following large position increases.

Revision ID: 0015_large_increase_following
Revises: 0014_live_only_multi_wallet
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_large_increase_following"
down_revision: str | None = "0014_live_only_multi_wallet"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DECIMAL = sa.Numeric(38, 18)


def upgrade() -> None:
    with op.batch_alter_table("copy_subscriptions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "large_increase_threshold_usdc",
                DECIMAL,
                nullable=False,
                server_default="100",
            )
        )
        batch_op.create_check_constraint(
            "ck_copy_subscriptions_large_increase_threshold",
            "large_increase_threshold_usdc > 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("copy_subscriptions") as batch_op:
        batch_op.drop_constraint(
            "ck_copy_subscriptions_large_increase_threshold",
            type_="check",
        )
        batch_op.drop_column("large_increase_threshold_usdc")
