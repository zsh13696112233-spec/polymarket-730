"""Normalize FAK orders completed within exchange rounding precision.

Revision ID: 0017_normalize_fak_rounding_dust
Revises: 0016_copy_order_amount_snapshots
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_normalize_fak_rounding_dust"
down_revision: str | None = "0016_copy_order_amount_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Mark only non-executable FAK rounding remainders as completed."""
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
                  AND filled_size >= requested_size - (0.50 / limit_price)
                )
              )
            """
        )
    )


def downgrade() -> None:
    # The prior partial status cannot be reconstructed once normalized.
    pass
