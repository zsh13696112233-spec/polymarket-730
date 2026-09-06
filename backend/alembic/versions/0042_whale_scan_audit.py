"""Add durable whale scan audit and coverage state.

Revision ID: 0042_whale_scan_audit
Revises: 0041_whale_conflict_priority_toggle
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042_whale_scan_audit"
down_revision: str | None = "0041_whale_conflict_priority_toggle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("whale_settings") as batch:
        batch.add_column(sa.Column("coverage_incomplete_until", sa.DateTime(), nullable=True))

    op.create_table(
        "whale_scan_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scan_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("requested_start", sa.DateTime(), nullable=True),
        sa.Column("requested_end", sa.DateTime(), nullable=True),
        sa.Column("oldest_trade_at", sa.DateTime(), nullable=True),
        sa.Column("newest_trade_at", sa.DateTime(), nullable=True),
        sa.Column("collected_trade_count", sa.Integer(), nullable=False),
        sa.Column("page_limit_hit", sa.Boolean(), nullable=False),
        sa.Column("coverage_complete", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'degraded', 'failed')",
            name="ck_whale_scan_runs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scan_id", name="uq_whale_scan_runs_scan_id"),
    )
    op.create_index("ix_whale_scan_runs_started", "whale_scan_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_whale_scan_runs_started", table_name="whale_scan_runs")
    op.drop_table("whale_scan_runs")
    with op.batch_alter_table("whale_settings") as batch:
        batch.drop_column("coverage_incomplete_until")
