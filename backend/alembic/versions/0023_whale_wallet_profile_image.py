"""Store whale wallet profile images.

Revision ID: 0023_whale_wallet_profile_image
Revises: 0022_whale_discovery
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_whale_wallet_profile_image"
down_revision: str | None = "0022_whale_discovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("whale_wallets", sa.Column("profile_image_url", sa.Text(), nullable=True))
    # Force one profile refresh so existing wallets receive an avatar promptly.
    op.execute("UPDATE whale_wallets SET refreshed_at = '1970-01-01 00:00:00'")


def downgrade() -> None:
    op.drop_column("whale_wallets", "profile_image_url")
