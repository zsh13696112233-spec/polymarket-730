"""Add the default whale follow amount.

Revision ID: 0024_whale_default_follow_amount
Revises: 0023_whale_wallet_profile_image
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_whale_default_follow_amount"
down_revision: str | None = "0023_whale_wallet_profile_image"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DECIMAL = sa.Numeric(38, 18)


def upgrade() -> None:
    op.add_column(
        "whale_settings",
        sa.Column(
            "default_follow_amount_usdc",
            DECIMAL,
            nullable=False,
            server_default="20",
        ),
    )


def downgrade() -> None:
    op.drop_column("whale_settings", "default_follow_amount_usdc")
