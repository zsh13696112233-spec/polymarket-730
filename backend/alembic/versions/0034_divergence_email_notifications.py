"""Support market-level divergence email notifications.

Revision ID: 0034_divergence_email_notifications
Revises: 0033_decouple_email_settings
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_divergence_email_notifications"
down_revision: str | None = "0033_decouple_email_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("whale_email_deliveries") as batch:
        batch.add_column(
            sa.Column(
                "notification_kind",
                sa.String(length=20),
                nullable=False,
                server_default="entry",
            )
        )
        batch.add_column(sa.Column("condition_id", sa.String(length=66), nullable=True))
        batch.add_column(
            sa.Column("entry_ids_json", sa.Text(), nullable=False, server_default="[]")
        )
        batch.add_column(sa.Column("dedupe_key", sa.String(length=200), nullable=True))

    op.execute(
        """
        UPDATE whale_email_deliveries
        SET condition_id = (
                SELECT whale_entries.condition_id
                FROM whale_entries
                WHERE whale_entries.id = whale_email_deliveries.entry_id
            ),
            entry_ids_json = '[' || entry_id || ']',
            dedupe_key = 'entry:' || entry_id || ':' || rule_key
        """
    )

    with op.batch_alter_table("whale_email_deliveries") as batch:
        batch.alter_column("entry_id", existing_type=sa.Integer(), nullable=True)
        batch.alter_column(
            "notification_kind",
            existing_type=sa.String(length=20),
            server_default=None,
        )
        batch.alter_column(
            "condition_id",
            existing_type=sa.String(length=66),
            nullable=False,
        )
        batch.alter_column(
            "entry_ids_json",
            existing_type=sa.Text(),
            server_default=None,
        )
        batch.alter_column(
            "dedupe_key",
            existing_type=sa.String(length=200),
            nullable=False,
        )
        batch.create_check_constraint(
            "ck_whale_email_delivery_kind",
            "notification_kind IN ('entry','divergence')",
        )
        batch.create_unique_constraint(
            "uq_whale_email_delivery_dedupe_recipient",
            ["dedupe_key", "recipient_email"],
        )


def downgrade() -> None:
    op.execute("DELETE FROM whale_email_deliveries WHERE notification_kind = 'divergence'")
    with op.batch_alter_table("whale_email_deliveries") as batch:
        batch.drop_constraint(
            "uq_whale_email_delivery_dedupe_recipient",
            type_="unique",
        )
        batch.drop_constraint("ck_whale_email_delivery_kind", type_="check")
        batch.alter_column("entry_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_column("dedupe_key")
        batch.drop_column("entry_ids_json")
        batch.drop_column("condition_id")
        batch.drop_column("notification_kind")
