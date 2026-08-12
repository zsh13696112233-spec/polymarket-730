"""Renormalize FAK orders inserted after the first dust migration.

Revision ID: 0018_renormalize_fak_dust
Revises: 0017_normalize_fak_rounding_dust
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_renormalize_fak_dust"
down_revision: str | None = "0017_normalize_fak_rounding_dust"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Mark FAK fills with at most $0.50 of residual value as completed."""
    op.execute(
        sa.text(
            """
            UPDATE copy_orders
            SET status = 'filled', reason = NULL
            WHERE status = 'partially_filled'
              AND (
                (side = 'BUY' AND filled_usdc >= requested_usdc - 0.50)
                OR (
                  side = 'SELL'
                  AND limit_price > 0
                  AND filled_size >= requested_size - (0.50 / limit_price)
                )
              )
            """
        )
    )


def downgrade() -> None:
    # The prior partial status cannot be reconstructed once normalized.
    pass
