"""Initial wallet monitor schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NUMERIC = sa.Numeric(38, 18)


def upgrade() -> None:
    op.create_table(
        "watched_wallets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("address", sa.String(42), nullable=False),
        sa.Column("proxy_wallet", sa.String(42), nullable=False, unique=True),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("baseline_established", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("next_sync_at", sa.DateTime(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "current_positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "wallet_id",
            sa.Integer(),
            sa.ForeignKey("watched_wallets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("asset_id", sa.String(100), nullable=False),
        sa.Column("condition_id", sa.String(66), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(200), nullable=False),
        sa.Column("outcome_index", sa.Integer(), nullable=True),
        sa.Column("icon_url", sa.Text(), nullable=True),
        sa.Column("event_slug", sa.String(500), nullable=True),
        sa.Column("market_slug", sa.String(500), nullable=True),
        sa.Column("size", NUMERIC, nullable=False),
        sa.Column("avg_price", NUMERIC, nullable=False),
        sa.Column("current_price", NUMERIC, nullable=False),
        sa.Column("initial_value", NUMERIC, nullable=False),
        sa.Column("current_value", NUMERIC, nullable=False),
        sa.Column("cash_pnl", NUMERIC, nullable=False),
        sa.Column("percent_pnl", NUMERIC, nullable=False),
        sa.Column("total_bought", NUMERIC, nullable=False),
        sa.Column("realized_pnl", NUMERIC, nullable=False),
        sa.Column("end_date", sa.DateTime(), nullable=True),
        sa.Column("missing_count", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("wallet_id", "asset_id", name="uq_current_position_wallet_asset"),
    )
    op.create_index("ix_current_positions_wallet_id", "current_positions", ["wallet_id"])
    op.create_index("ix_current_positions_condition_id", "current_positions", ["condition_id"])
    op.create_index(
        "ix_current_positions_wallet_value",
        "current_positions",
        ["wallet_id", "current_value"],
    )
    op.create_table(
        "position_change_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "wallet_id",
            sa.Integer(),
            sa.ForeignKey("watched_wallets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("asset_id", sa.String(100), nullable=False),
        sa.Column("condition_id", sa.String(66), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(200), nullable=False),
        sa.Column("event_slug", sa.String(500), nullable=True),
        sa.Column("start_size", NUMERIC, nullable=False),
        sa.Column("latest_size", NUMERIC, nullable=False),
        sa.Column("before_avg_price", NUMERIC, nullable=False),
        sa.Column("after_avg_price", NUMERIC, nullable=False),
        sa.Column("latest_current_value", NUMERIC, nullable=False),
        sa.Column("first_changed_at", sa.DateTime(), nullable=False),
        sa.Column("last_changed_at", sa.DateTime(), nullable=False),
        sa.Column("hard_deadline_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("wallet_id", "asset_id", name="uq_candidate_wallet_asset"),
    )
    op.create_index(
        "ix_position_change_candidates_wallet_id",
        "position_change_candidates",
        ["wallet_id"],
    )
    op.create_index(
        "ix_candidates_due",
        "position_change_candidates",
        ["last_changed_at", "hard_deadline_at"],
    )
    op.create_table(
        "position_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "wallet_id",
            sa.Integer(),
            sa.ForeignKey("watched_wallets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("asset_id", sa.String(100), nullable=False),
        sa.Column("condition_id", sa.String(66), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(200), nullable=False),
        sa.Column("event_slug", sa.String(500), nullable=True),
        sa.Column("delta_size", NUMERIC, nullable=False),
        sa.Column("before_size", NUMERIC, nullable=False),
        sa.Column("after_size", NUMERIC, nullable=False),
        sa.Column("before_avg_price", NUMERIC, nullable=False),
        sa.Column("after_avg_price", NUMERIC, nullable=False),
        sa.Column("average_fill_price", NUMERIC, nullable=True),
        sa.Column("current_value", NUMERIC, nullable=False),
        sa.Column("reconciliation_status", sa.String(30), nullable=False),
        sa.Column("first_detected_at", sa.DateTime(), nullable=False),
        sa.Column("settled_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_position_events_wallet_id", "position_events", ["wallet_id"])
    op.create_index("ix_events_wallet_id_desc", "position_events", ["wallet_id", "id"])
    op.create_table(
        "position_event_fills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("position_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("size", NUMERIC, nullable=False),
        sa.Column("price", NUMERIC, nullable=False),
        sa.Column("amount", NUMERIC, nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("transaction_hash", sa.String(100), nullable=True),
        sa.UniqueConstraint("event_id", "fingerprint", name="uq_event_fill_fingerprint"),
    )
    op.create_index("ix_position_event_fills_event_id", "position_event_fills", ["event_id"])


def downgrade() -> None:
    op.drop_table("position_event_fills")
    op.drop_table("position_events")
    op.drop_table("position_change_candidates")
    op.drop_table("current_positions")
    op.drop_table("watched_wallets")
