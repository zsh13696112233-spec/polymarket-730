"""Add weekly email hit-rate summaries.

Revision ID: 0037_weekly_email_summary
Revises: 0036_whale_auto_follow_strategies
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_weekly_email_summary"
down_revision: str | None = "0036_whale_auto_follow_strategies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("email_settings") as batch:
        batch.add_column(
            sa.Column(
                "weekly_summary_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("weekly_summary_enabled_at", sa.DateTime(), nullable=True))

    with op.batch_alter_table("whale_email_deliveries") as batch:
        batch.drop_constraint("ck_whale_email_delivery_kind", type_="check")
        batch.create_check_constraint(
            "ck_whale_email_delivery_kind",
            "notification_kind IN ('entry','divergence','weekly_summary')",
        )


def downgrade() -> None:
    op.execute("DELETE FROM whale_email_deliveries WHERE notification_kind = 'weekly_summary'")
    with op.batch_alter_table("whale_email_deliveries") as batch:
        batch.drop_constraint("ck_whale_email_delivery_kind", type_="check")
        batch.create_check_constraint(
            "ck_whale_email_delivery_kind",
            "notification_kind IN ('entry','divergence')",
        )

    with op.batch_alter_table("email_settings") as batch:
        batch.drop_column("weekly_summary_enabled_at")
        batch.drop_column("weekly_summary_enabled")
