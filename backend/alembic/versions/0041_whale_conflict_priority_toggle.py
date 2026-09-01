"""Add the whale conflict-priority strategy toggle.

Revision ID: 0041_whale_conflict_priority_toggle
Revises: 0040_whale_auto_follow_risk_controls
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041_whale_conflict_priority_toggle"
down_revision: str | None = "0040_whale_auto_follow_risk_controls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("whale_settings") as batch:
        batch.add_column(
            sa.Column(
                "large_amount_conflict_priority_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("whale_settings") as batch:
        batch.drop_column("large_amount_conflict_priority_enabled")
