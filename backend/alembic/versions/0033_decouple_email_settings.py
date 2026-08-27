"""Move SMTP configuration out of whale settings.

Revision ID: 0033_decouple_email_settings
Revises: 0032_system_smtp_settings
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_decouple_email_settings"
down_revision: str | None = "0032_system_smtp_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SMTP_COLUMNS = (
    "smtp_host",
    "smtp_port",
    "smtp_security",
    "smtp_username",
    "smtp_from_email",
    "smtp_from_name",
    "smtp_keychain_service",
    "smtp_keychain_account",
)


def upgrade() -> None:
    op.create_table(
        "email_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("notifications_enabled", sa.Boolean(), nullable=False),
        sa.Column("smtp_host", sa.String(length=255), nullable=True),
        sa.Column("smtp_port", sa.Integer(), nullable=False),
        sa.Column("smtp_security", sa.String(length=20), nullable=False),
        sa.Column("smtp_username", sa.String(length=320), nullable=True),
        sa.Column("smtp_from_email", sa.String(length=320), nullable=True),
        sa.Column("smtp_from_name", sa.String(length=200), nullable=False),
        sa.Column("smtp_keychain_service", sa.String(length=200), nullable=True),
        sa.Column("smtp_keychain_account", sa.String(length=320), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_email_settings_singleton"),
    )
    op.execute(
        """
        INSERT INTO email_settings (
            id, notifications_enabled, smtp_host, smtp_port, smtp_security, smtp_username,
            smtp_from_email, smtp_from_name, smtp_keychain_service,
            smtp_keychain_account, created_at, updated_at
        )
        SELECT 1, email_notifications_enabled, smtp_host, smtp_port, smtp_security, smtp_username,
               smtp_from_email, smtp_from_name, smtp_keychain_service,
               smtp_keychain_account, created_at, updated_at
        FROM whale_settings WHERE id = 1
        """
    )
    op.rename_table("whale_email_recipients", "email_recipients")
    with op.batch_alter_table("whale_settings") as batch:
        for name in reversed(SMTP_COLUMNS):
            batch.drop_column(name)
        batch.drop_column("email_notifications_enabled")


def downgrade() -> None:
    op.rename_table("email_recipients", "whale_email_recipients")
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
    with op.batch_alter_table("whale_settings") as batch:
        batch.add_column(
            sa.Column(
                "email_notifications_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        for column in columns:
            batch.add_column(column)
    op.execute(
        """
        UPDATE whale_settings SET
            email_notifications_enabled = (
                SELECT notifications_enabled FROM email_settings WHERE id = 1
            ),
            smtp_host = (SELECT smtp_host FROM email_settings WHERE id = 1),
            smtp_port = (SELECT smtp_port FROM email_settings WHERE id = 1),
            smtp_security = (SELECT smtp_security FROM email_settings WHERE id = 1),
            smtp_username = (SELECT smtp_username FROM email_settings WHERE id = 1),
            smtp_from_email = (SELECT smtp_from_email FROM email_settings WHERE id = 1),
            smtp_from_name = (SELECT smtp_from_name FROM email_settings WHERE id = 1),
            smtp_keychain_service = (SELECT smtp_keychain_service FROM email_settings WHERE id = 1),
            smtp_keychain_account = (SELECT smtp_keychain_account FROM email_settings WHERE id = 1)
        WHERE id = 1
        """
    )
    op.drop_table("email_settings")
