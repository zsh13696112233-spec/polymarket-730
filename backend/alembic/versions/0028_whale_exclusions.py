"""Add the managed whale exclusion list.

Revision ID: 0028_whale_exclusions
Revises: 0027_whale_statistics_settled_index
Create Date: 2026-08-23
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0028_whale_exclusions"
down_revision: str | None = "0027_whale_statistics_settled_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_EXCLUDED_WALLET = "0x6d20c35f65d9899b6d6b74f8466e824580f9a165"


def upgrade() -> None:
    exclusions = op.create_table(
        "whale_exclusions",
        sa.Column("proxy_wallet", sa.String(42), primary_key=True),
        sa.Column("label", sa.String(200)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.bulk_insert(
        exclusions,
        [
            {
                "proxy_wallet": DEFAULT_EXCLUDED_WALLET,
                "label": "Djdjdjekekek",
                "created_at": datetime(2026, 8, 23),
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("whale_exclusions")
