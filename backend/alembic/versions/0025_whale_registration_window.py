"""Add the configurable whale registration window.

Revision ID: 0025_whale_registration_window
Revises: 0024_whale_default_follow_amount
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_whale_registration_window"
down_revision: str | None = "0024_whale_default_follow_amount"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE whale_settings SET window_hours = 24")
    op.add_column(
        "whale_settings",
        sa.Column(
            "registration_window_days",
            sa.Integer(),
            nullable=False,
            server_default="3",
        ),
    )


def downgrade() -> None:
    op.drop_column("whale_settings", "registration_window_days")
