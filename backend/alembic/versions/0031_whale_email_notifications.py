"""Add whale email notification configuration and delivery outbox.

Revision ID: 0031_whale_email_notifications
Revises: 0030_remove_fixed_wallet_copy
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_whale_email_notifications"
down_revision: str | None = "0030_remove_fixed_wallet_copy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "whale_settings",
        sa.Column(
            "email_notifications_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_table(
        "whale_email_recipients",
        sa.Column("email", sa.String(length=320), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "whale_email_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("rule_key", sa.String(length=80), nullable=False),
        sa.Column("rules_json", sa.Text(), nullable=False),
        sa.Column("recipient_email", sa.String(length=320), nullable=False),
        sa.Column("market_title", sa.Text(), nullable=False),
        sa.Column("wallet_label", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime()),
        sa.Column("locked_at", sa.DateTime()),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime()),
        sa.CheckConstraint(
            "status IN ('pending','sending','retrying','sent','failed')",
            name="ck_whale_email_delivery_status",
        ),
        sa.ForeignKeyConstraint(["entry_id"], ["whale_entries.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "entry_id",
            "rule_key",
            "recipient_email",
            name="uq_whale_email_delivery_trigger_recipient",
        ),
    )
    op.create_index(
        "ix_whale_email_delivery_due",
        "whale_email_deliveries",
        ["status", "next_attempt_at"],
    )
    op.create_index(
        "ix_whale_email_delivery_created",
        "whale_email_deliveries",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_whale_email_delivery_created", table_name="whale_email_deliveries")
    op.drop_index("ix_whale_email_delivery_due", table_name="whale_email_deliveries")
    op.drop_table("whale_email_deliveries")
    op.drop_table("whale_email_recipients")
    op.drop_column("whale_settings", "email_notifications_enabled")
