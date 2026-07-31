"""Store onchain redemption events.

Revision ID: 0007_redemption_events
Revises: 0006_overlap_alerts
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_redemption_events"
down_revision: str | None = "0006_overlap_alerts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NUMERIC = sa.Numeric(38, 18)


def upgrade() -> None:
    with op.batch_alter_table("watched_wallets") as batch_op:
        batch_op.add_column(sa.Column("redemption_history_synced_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("redemption_history_error", sa.Text(), nullable=True))

    with op.batch_alter_table("position_events") as batch_op:
        batch_op.add_column(sa.Column("source_fingerprint", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("payout_amount", NUMERIC, nullable=True))
        batch_op.add_column(sa.Column("transaction_hash", sa.String(100), nullable=True))
        batch_op.create_unique_constraint(
            "uq_position_events_source_fingerprint",
            ["source_fingerprint"],
        )

    op.create_index(
        "ix_events_wallet_settled_desc",
        "position_events",
        ["wallet_id", "settled_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_events_wallet_settled_desc", table_name="position_events")
    with op.batch_alter_table("position_events") as batch_op:
        batch_op.drop_constraint(
            "uq_position_events_source_fingerprint",
            type_="unique",
        )
        batch_op.drop_column("transaction_hash")
        batch_op.drop_column("payout_amount")
        batch_op.drop_column("source_fingerprint")

    with op.batch_alter_table("watched_wallets") as batch_op:
        batch_op.drop_column("redemption_history_error")
        batch_op.drop_column("redemption_history_synced_at")
