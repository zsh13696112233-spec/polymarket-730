"""Add independent source amount tiers and decision snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0050_source_amount_tiers"
down_revision = "0049_auto_take_profit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for prefix in ("new_account", "large_amount"):
        op.add_column(
            "whale_settings",
            sa.Column(
                f"{prefix}_auto_follow_source_tiers_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        op.add_column(
            "whale_settings",
            sa.Column(
                f"{prefix}_auto_follow_source_tiers_json",
                sa.Text(),
                nullable=False,
                server_default="[]",
            ),
        )
    for name in ("source_buy_amount_usdc", "source_tier_min_usdc"):
        op.add_column(
            "whale_auto_follow_decisions", sa.Column(name, sa.Numeric(38, 18), nullable=True)
        )
    for name in ("amount_basis", "conflict_rule"):
        op.add_column("whale_auto_follow_decisions", sa.Column(name, sa.String(30), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("whale_auto_follow_decisions") as batch:
        for name in (
            "source_buy_amount_usdc",
            "source_tier_min_usdc",
            "amount_basis",
            "conflict_rule",
        ):
            batch.drop_column(name)
    with op.batch_alter_table("whale_settings") as batch:
        for prefix in ("new_account", "large_amount"):
            batch.drop_column(f"{prefix}_auto_follow_source_tiers_json")
            batch.drop_column(f"{prefix}_auto_follow_source_tiers_enabled")
