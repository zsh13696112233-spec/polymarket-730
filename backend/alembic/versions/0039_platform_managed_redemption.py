"""Default to Polymarket-managed automatic redemption.

Revision ID: 0039_platform_managed_redemption
Revises: 0038_whale_trade_cursor
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_platform_managed_redemption"
down_revision: str | None = "0038_whale_trade_cursor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing installations previously enabled the local executor implicitly.
    # Disable it before exposing the setting as an explicit fallback opt-in.
    op.execute("UPDATE execution_accounts SET auto_redeem = 0")
    with op.batch_alter_table("execution_accounts") as batch:
        batch.alter_column(
            "auto_redeem",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.false(),
        )


def downgrade() -> None:
    # Preserve the safe, user-visible value while restoring the legacy default.
    with op.batch_alter_table("execution_accounts") as batch:
        batch.alter_column(
            "auto_redeem",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.true(),
        )
