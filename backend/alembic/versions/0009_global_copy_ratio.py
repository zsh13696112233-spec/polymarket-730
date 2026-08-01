"""Add the global copy-trading ratio.

Revision ID: 0009_global_copy_ratio
Revises: 0008_redemption_profit
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_global_copy_ratio"
down_revision: str | None = "0008_redemption_profit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "global_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("copy_ratio_percent", sa.Numeric(5, 2), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_global_settings_singleton"),
        sa.CheckConstraint(
            "copy_ratio_percent >= 1 AND copy_ratio_percent <= 100",
            name="ck_global_settings_copy_ratio_percent",
        ),
    )
    op.execute(sa.text("INSERT INTO global_settings (id, copy_ratio_percent) VALUES (1, 10)"))


def downgrade() -> None:
    op.drop_table("global_settings")
