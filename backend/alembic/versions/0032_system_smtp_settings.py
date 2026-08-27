"""Store editable SMTP settings and a Keychain reference.

Revision ID: 0032_system_smtp_settings
Revises: 0031_whale_email_notifications
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_system_smtp_settings"
down_revision: str | None = "0031_whale_email_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = (
        sa.Column("smtp_host", sa.String(length=255), nullable=True),
        sa.Column("smtp_port", sa.Integer(), nullable=False, server_default="465"),
        sa.Column("smtp_security", sa.String(length=20), nullable=False, server_default="ssl"),
        sa.Column("smtp_username", sa.String(length=320), nullable=True),
        sa.Column("smtp_from_email", sa.String(length=320), nullable=True),
        sa.Column(
            "smtp_from_name",
            sa.String(length=200),
            nullable=False,
            server_default="PolyCopy",
        ),
        sa.Column("smtp_keychain_service", sa.String(length=200), nullable=True),
        sa.Column("smtp_keychain_account", sa.String(length=320), nullable=True),
    )
    for column in columns:
        op.add_column("whale_settings", column)


def downgrade() -> None:
    for name in (
        "smtp_keychain_account",
        "smtp_keychain_service",
        "smtp_from_name",
        "smtp_from_email",
        "smtp_username",
        "smtp_security",
        "smtp_port",
        "smtp_host",
    ):
        op.drop_column("whale_settings", name)
