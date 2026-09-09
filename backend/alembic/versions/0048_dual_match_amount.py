"""Add optional dual-match automatic follow amount."""

import sqlalchemy as sa
from alembic import op

revision = "0048_dual_match_amount"
down_revision = "0047_backfill_signal_eligibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("whale_settings") as batch:
        batch.add_column(
            sa.Column("dual_match_auto_follow_amount_usdc", sa.Numeric(38, 18), nullable=True)
        )
        batch.create_check_constraint(
            "ck_whale_settings_dual_match_amount",
            "dual_match_auto_follow_amount_usdc IS NULL OR dual_match_auto_follow_amount_usdc > 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("whale_settings") as batch:
        batch.drop_constraint("ck_whale_settings_dual_match_amount", type_="check")
        batch.drop_column("dual_match_auto_follow_amount_usdc")
