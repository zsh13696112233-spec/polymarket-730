"""Persist bounded market backfill progress and unpublished trade pages."""

import sqlalchemy as sa
from alembic import op

revision = "0046_whale_market_scan_progress"
down_revision = "0045_whale_position_quality"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "whale_market_scan_states",
        sa.Column("condition_id", sa.String(66), primary_key=True),
        sa.Column("coverage_start", sa.DateTime(), nullable=True),
        sa.Column("coverage_end", sa.DateTime(), nullable=True),
        sa.Column("batch_start", sa.DateTime(), nullable=True),
        sa.Column("batch_end", sa.DateTime(), nullable=True),
        sa.Column("pending_ranges_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("auto_follow_after", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_table(
        "whale_market_scan_pages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("condition_id", sa.String(66), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_whale_market_scan_pages_condition_id", "whale_market_scan_pages", ["condition_id"]
    )


def downgrade() -> None:
    op.drop_table("whale_market_scan_pages")
    op.drop_table("whale_market_scan_states")
