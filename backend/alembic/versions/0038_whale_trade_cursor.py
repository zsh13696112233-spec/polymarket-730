"""Persist the successful whale trade scan cursor.

Revision ID: 0038_whale_trade_cursor
Revises: 0037_weekly_email_summary
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_whale_trade_cursor"
down_revision: str | None = "0037_weekly_email_summary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("whale_settings") as batch:
        batch.add_column(sa.Column("last_trade_cursor_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("whale_settings") as batch:
        batch.drop_column("last_trade_cursor_at")
