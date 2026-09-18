"""Add the independent automatic-follow weekly report delivery kind.

Revision ID: 0051_weekly_auto_follow_report
Revises: 0050_source_amount_tiers
"""

import sqlalchemy as sa
from alembic import op

revision = "0051_weekly_auto_follow_report"
down_revision = "0050_source_amount_tiers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("whale_email_deliveries") as batch:
        batch.alter_column(
            "notification_kind",
            existing_type=sa.String(20),
            type_=sa.String(40),
            existing_nullable=False,
        )
        batch.drop_constraint("ck_whale_email_delivery_kind", type_="check")
        batch.create_check_constraint(
            "ck_whale_email_delivery_kind",
            "notification_kind IN ('entry','divergence','weekly_summary',"
            "'weekly_auto_follow_report')",
        )


def downgrade() -> None:
    # Preserve report bodies and delivery history as ordinary weekly summaries.
    op.execute(
        "UPDATE whale_email_deliveries SET notification_kind='weekly_summary' "
        "WHERE notification_kind='weekly_auto_follow_report'"
    )
    with op.batch_alter_table("whale_email_deliveries") as batch:
        batch.drop_constraint("ck_whale_email_delivery_kind", type_="check")
        batch.create_check_constraint(
            "ck_whale_email_delivery_kind",
            "notification_kind IN ('entry','divergence','weekly_summary')",
        )
        batch.alter_column(
            "notification_kind",
            existing_type=sa.String(40),
            type_=sa.String(20),
            existing_nullable=False,
        )
