"""Persist wallet take-profit policy, protection and execution snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0049_auto_take_profit"
down_revision = "0048_dual_match_amount"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "take_profit_policies",
        sa.Column("wallet", sa.String(42), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("threshold_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "threshold_percent > 0 AND threshold_percent < 100", name="ck_take_profit_threshold"
        ),
    )
    op.create_table(
        "take_profit_protections",
        sa.Column("wallet", sa.String(42), primary_key=True),
        sa.Column("asset_id", sa.String(100), primary_key=True),
        sa.Column("condition_id", sa.String(66), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("present", sa.Boolean(), nullable=False),
        sa.Column("rebuy_blocked", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "take_profit_executions",
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("whale_orders.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("threshold_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("unit_cost", sa.Numeric(38, 18), nullable=False),
        sa.Column("cost_source", sa.String(30), nullable=False),
        sa.Column("payout_rate", sa.Numeric(38, 18), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("take_profit_executions")
    op.drop_table("take_profit_protections")
    op.drop_table("take_profit_policies")
