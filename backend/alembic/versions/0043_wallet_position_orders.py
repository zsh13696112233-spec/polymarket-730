"""Persist wallet identity and time in force for position management orders."""

import sqlalchemy as sa
from alembic import op

revision = "0043_wallet_position_orders"
down_revision = "0042_whale_scan_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("whale_orders") as batch:
        batch.add_column(sa.Column("execution_wallet", sa.String(42), nullable=True))
        batch.add_column(
            sa.Column("order_type", sa.String(3), nullable=False, server_default="FAK")
        )


def downgrade() -> None:
    with op.batch_alter_table("whale_orders") as batch:
        batch.drop_column("order_type")
        batch.drop_column("execution_wallet")
