"""Add the wallet trade reconciliation index.

Revision ID: 0029_wallet_trade_reconciliation_index
Revises: 0028_whale_exclusions
Create Date: 2026-08-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0029_wallet_trade_reconciliation_index"
down_revision: str | None = "0028_whale_exclusions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_wallet_trades_wallet_condition_time",
        "wallet_trades",
        ["wallet_id", "condition_id", "timestamp", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_wallet_trades_wallet_condition_time",
        table_name="wallet_trades",
    )
