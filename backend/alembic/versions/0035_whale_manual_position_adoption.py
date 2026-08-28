"""Adopt manual execution-wallet trades into whale follow positions.

Revision ID: 0035_whale_manual_position_adoption
Revises: 0034_divergence_email_notifications
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_whale_manual_position_adoption"
down_revision: str | None = "0034_divergence_email_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("whale_follow_ledger") as batch:
        batch.add_column(
            sa.Column(
                "source",
                sa.String(length=30),
                nullable=False,
                server_default="follow",
            )
        )
        batch.add_column(sa.Column("external_event_key", sa.String(length=128), nullable=True))
        batch.create_unique_constraint(
            "uq_whale_ledger_external_event",
            ["external_event_key"],
        )

    op.execute(
        """
        UPDATE whale_follow_ledger
        SET source = CASE
            WHEN type IN ('redeem', 'resolved_loss') THEN 'auto_redeem'
            WHEN type = 'dust_writeoff' THEN 'reconciliation'
            ELSE 'follow'
        END
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("whale_follow_ledger") as batch:
        batch.drop_constraint("uq_whale_ledger_external_event", type_="unique")
        batch.drop_column("external_event_key")
        batch.drop_column("source")
