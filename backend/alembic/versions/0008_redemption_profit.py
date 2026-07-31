"""Store FIFO cost basis for redemption events.

Revision ID: 0008_redemption_profit
Revises: 0007_redemption_events
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_redemption_profit"
down_revision: str | None = "0007_redemption_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NUMERIC = sa.Numeric(38, 18)


def upgrade() -> None:
    with op.batch_alter_table("position_events") as batch_op:
        batch_op.add_column(sa.Column("redemption_cost_basis", NUMERIC, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("position_events") as batch_op:
        batch_op.drop_column("redemption_cost_basis")
