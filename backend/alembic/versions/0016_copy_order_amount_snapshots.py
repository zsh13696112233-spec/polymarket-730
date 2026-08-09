"""Store source and proportional amounts for copy orders.

Revision ID: 0016_copy_order_amount_snapshots
Revises: 0015_large_increase_following
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_copy_order_amount_snapshots"
down_revision: str | None = "0015_large_increase_following"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DECIMAL = sa.Numeric(38, 18)


def upgrade() -> None:
    with op.batch_alter_table("copy_orders") as batch_op:
        batch_op.add_column(sa.Column("leader_purchase_usdc", DECIMAL, nullable=True))
        batch_op.add_column(sa.Column("proportional_target_usdc", DECIMAL, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("copy_orders") as batch_op:
        batch_op.drop_column("proportional_target_usdc")
        batch_op.drop_column("leader_purchase_usdc")
