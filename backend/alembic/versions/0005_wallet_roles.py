"""Distinguish the user's wallet from tracked wallets.

Revision ID: 0005_wallet_roles
Revises: 0004_purchase_date_history
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_wallet_roles"
down_revision: str | None = "0004_purchase_date_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("watched_wallets") as batch_op:
        batch_op.add_column(
            sa.Column(
                "wallet_role",
                sa.String(20),
                nullable=False,
                server_default="tracked",
            )
        )
        batch_op.create_check_constraint(
            "ck_watched_wallets_wallet_role",
            "wallet_role IN ('self', 'tracked')",
        )

    op.create_index(
        "uq_watched_wallets_single_self",
        "watched_wallets",
        ["wallet_role"],
        unique=True,
        sqlite_where=sa.text("wallet_role = 'self'"),
    )
    op.execute(sa.text("PRAGMA optimize"))


def downgrade() -> None:
    op.drop_index("uq_watched_wallets_single_self", table_name="watched_wallets")
    with op.batch_alter_table("watched_wallets") as batch_op:
        batch_op.drop_constraint("ck_watched_wallets_wallet_role", type_="check")
        batch_op.drop_column("wallet_role")
