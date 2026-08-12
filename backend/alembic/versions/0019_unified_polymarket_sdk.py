"""Add unified SDK execution audit fields.

Revision ID: 0019_unified_polymarket_sdk
Revises: 0018_renormalize_fak_dust
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_unified_polymarket_sdk"
down_revision: str | None = "0018_renormalize_fak_dust"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DECIMAL = sa.Numeric(38, 18)


def upgrade() -> None:
    op.add_column("copy_orders", sa.Column("execution_provider", sa.String(30), nullable=True))
    op.add_column("copy_fills", sa.Column("transaction_hash", sa.String(100), nullable=True))
    op.add_column("copy_fills", sa.Column("bucket_index", sa.Integer(), nullable=True))
    op.add_column("copy_fills", sa.Column("settlement_status", sa.String(30), nullable=True))

    op.create_table(
        "copy_redemption_executions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("wallet_address", sa.String(42), nullable=False),
        sa.Column("condition_id", sa.String(66), nullable=False),
        sa.Column("method", sa.String(50), nullable=True),
        sa.Column("execution_provider", sa.String(30), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("estimated_payout_usdc", DECIMAL, nullable=True),
        sa.Column("actual_pusd_delta", DECIMAL, nullable=True),
        sa.Column("before_outcome_balance", DECIMAL, nullable=True),
        sa.Column("after_outcome_balance", DECIMAL, nullable=True),
        sa.Column("before_pusd_balance", DECIMAL, nullable=True),
        sa.Column("after_pusd_balance", DECIMAL, nullable=True),
        sa.Column("relayer_transaction_id", sa.String(200), nullable=True),
        sa.Column("transaction_hash", sa.String(100), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "wallet_address",
            "condition_id",
            name="uq_copy_redemption_execution_wallet_condition",
        ),
    )
    op.add_column("copy_redemptions", sa.Column("execution_id", sa.Integer(), nullable=True))
    op.add_column("copy_redemptions", sa.Column("execution_provider", sa.String(30), nullable=True))
    with op.batch_alter_table("copy_redemptions") as batch:
        batch.create_foreign_key(
            "fk_copy_redemptions_execution",
            "copy_redemption_executions",
            ["execution_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.execute("UPDATE copy_orders SET execution_provider = 'legacy'")
    op.execute("UPDATE copy_redemptions SET execution_provider = 'legacy'")
    op.execute(
        """
        UPDATE copy_orders
        SET status = 'manual_review',
            reason = CASE
                WHEN reason IS NULL OR reason = ''
                    THEN '统一 SDK 迁移：旧订单停止自动对账，转人工检查'
                ELSE reason || '；统一 SDK 迁移：旧订单停止自动对账，转人工检查'
            END
        WHERE status = 'reconciliation_pending'
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("copy_redemptions") as batch:
        batch.drop_constraint("fk_copy_redemptions_execution", type_="foreignkey")
    op.drop_column("copy_redemptions", "execution_provider")
    op.drop_column("copy_redemptions", "execution_id")
    op.drop_table("copy_redemption_executions")
    op.drop_column("copy_fills", "settlement_status")
    op.drop_column("copy_fills", "bucket_index")
    op.drop_column("copy_fills", "transaction_hash")
    op.drop_column("copy_orders", "execution_provider")
