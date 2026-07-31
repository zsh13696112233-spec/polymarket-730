"""Store wallet trades for purchase-date position views.

Revision ID: 0004_purchase_date_history
Revises: 0003_remove_phantom_zero_events
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_purchase_date_history"
down_revision: str | None = "0003_remove_phantom_zero_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NUMERIC = sa.Numeric(38, 18)


def upgrade() -> None:
    with op.batch_alter_table("watched_wallets") as batch_op:
        batch_op.add_column(sa.Column("trade_history_synced_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("trade_history_error", sa.Text(), nullable=True))

    op.create_table(
        "wallet_trades",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "wallet_id",
            sa.Integer(),
            sa.ForeignKey("watched_wallets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("asset_id", sa.String(100), nullable=False),
        sa.Column("condition_id", sa.String(66), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("size", NUMERIC, nullable=False),
        sa.Column("price", NUMERIC, nullable=False),
        sa.Column("amount", NUMERIC, nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("transaction_hash", sa.String(100), nullable=True),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("fingerprint", name="uq_wallet_trade_fingerprint"),
    )
    op.create_index("ix_wallet_trades_wallet_id", "wallet_trades", ["wallet_id"])
    op.create_index("ix_wallet_trades_condition_id", "wallet_trades", ["condition_id"])
    op.create_index("ix_wallet_trades_timestamp", "wallet_trades", ["timestamp"])
    op.create_index(
        "ix_wallet_trades_wallet_asset_time",
        "wallet_trades",
        ["wallet_id", "asset_id", "timestamp"],
    )


def downgrade() -> None:
    op.drop_table("wallet_trades")
    with op.batch_alter_table("watched_wallets") as batch_op:
        batch_op.drop_column("trade_history_error")
        batch_op.drop_column("trade_history_synced_at")
