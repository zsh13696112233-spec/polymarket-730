"""Persist discovery provenance and verified position exposure."""

import sqlalchemy as sa
from alembic import op

revision = "0045_whale_position_quality"
down_revision = "0044_whale_monitor_categories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("whale_entries") as batch:
        batch.add_column(
            sa.Column("discovery_source", sa.String(20), nullable=False, server_default="trades")
        )
        batch.add_column(sa.Column("position_cost_usdc", sa.Numeric(38, 18), nullable=True))
        batch.add_column(sa.Column("opposite_size", sa.Numeric(38, 18), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("whale_entries") as batch:
        batch.drop_column("opposite_size")
        batch.drop_column("position_cost_usdc")
        batch.drop_column("discovery_source")
