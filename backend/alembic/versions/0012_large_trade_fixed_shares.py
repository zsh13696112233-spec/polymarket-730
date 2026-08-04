"""Add configurable fixed-share following for large trades.

Revision ID: 0012_large_trade_fixed_shares
Revises: 0011_fast_copy_trading
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_large_trade_fixed_shares"
down_revision: str | None = "0011_fast_copy_trading"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DECIMAL = sa.Numeric(38, 18)


def upgrade() -> None:
    op.add_column(
        "copy_subscriptions",
        sa.Column(
            "large_trade_threshold_usdc",
            DECIMAL,
            nullable=False,
            server_default="100",
        ),
    )
    op.add_column(
        "copy_subscriptions",
        sa.Column(
            "large_trade_fixed_shares",
            DECIMAL,
            nullable=False,
            server_default="5",
        ),
    )


def downgrade() -> None:
    op.drop_column("copy_subscriptions", "large_trade_fixed_shares")
    op.drop_column("copy_subscriptions", "large_trade_threshold_usdc")
