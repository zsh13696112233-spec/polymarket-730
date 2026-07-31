"""Store common-position periods and reduction alerts.

Revision ID: 0006_overlap_alerts
Revises: 0005_wallet_roles
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_overlap_alerts"
down_revision: str | None = "0005_wallet_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NUMERIC = sa.Numeric(38, 18)


def upgrade() -> None:
    op.create_table(
        "position_overlap_periods",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "my_wallet_id",
            sa.Integer(),
            sa.ForeignKey("watched_wallets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "tracked_wallet_id",
            sa.Integer(),
            sa.ForeignKey("watched_wallets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("asset_id", sa.String(100), nullable=False),
        sa.Column("condition_id", sa.String(66), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(200), nullable=False),
        sa.Column("event_slug", sa.String(500), nullable=True),
        sa.Column("market_slug", sa.String(500), nullable=True),
        sa.Column("last_my_size", NUMERIC, nullable=False),
        sa.Column("last_tracked_size", NUMERIC, nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "uq_overlap_periods_active_pair_asset",
        "position_overlap_periods",
        ["my_wallet_id", "tracked_wallet_id", "asset_id"],
        unique=True,
        sqlite_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "ix_overlap_periods_tracked_asset_window",
        "position_overlap_periods",
        ["tracked_wallet_id", "asset_id", "started_at", "ended_at"],
    )

    op.create_table(
        "position_overlap_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "period_id",
            sa.Integer(),
            sa.ForeignKey("position_overlap_periods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("position_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "my_wallet_id",
            sa.Integer(),
            sa.ForeignKey("watched_wallets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "tracked_wallet_id",
            sa.Integer(),
            sa.ForeignKey("watched_wallets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("event_id", name="uq_overlap_alerts_event"),
    )
    op.create_index(
        "ix_position_overlap_alerts_period_id",
        "position_overlap_alerts",
        ["period_id"],
    )
    op.create_index(
        "ix_overlap_alerts_wallet_pair_desc",
        "position_overlap_alerts",
        ["my_wallet_id", "tracked_wallet_id", "id"],
    )
    op.execute(sa.text("PRAGMA optimize"))


def downgrade() -> None:
    op.drop_table("position_overlap_alerts")
    op.drop_table("position_overlap_periods")
