"""Add the whale statistics settlement-time index.

Revision ID: 0027_whale_statistics_settled_index
Revises: 0026_whale_dual_rules_history
Create Date: 2026-08-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0027_whale_statistics_settled_index"
down_revision: str | None = "0026_whale_dual_rules_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_whale_entries_settled_at",
        "whale_entries",
        ["settled_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_whale_entries_settled_at", table_name="whale_entries")
