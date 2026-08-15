"""Link one-shot force-buy orders to skipped source orders.

Revision ID: 0021_force_buy_overrides
Revises: 0020_large_increase_strategy
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_force_buy_overrides"
down_revision: str | None = "0020_large_increase_strategy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("copy_orders") as batch:
        batch.add_column(sa.Column("override_of_order_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_copy_orders_override_of_order",
            "copy_orders",
            ["override_of_order_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_unique_constraint("uq_copy_orders_override_of_order", ["override_of_order_id"])


def downgrade() -> None:
    with op.batch_alter_table("copy_orders") as batch:
        batch.drop_constraint("uq_copy_orders_override_of_order", type_="unique")
        batch.drop_constraint("fk_copy_orders_override_of_order", type_="foreignkey")
        batch.drop_column("override_of_order_id")
