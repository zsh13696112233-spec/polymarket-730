"""Add dual whale rules and durable discovery history.

Revision ID: 0026_whale_dual_rules_history
Revises: 0025_whale_registration_window
Create Date: 2026-08-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_whale_dual_rules_history"
down_revision: str | None = "0025_whale_registration_window"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DECIMAL = sa.Numeric(38, 18)


def upgrade() -> None:
    op.add_column(
        "whale_settings",
        sa.Column("new_account_threshold_usdc", DECIMAL, nullable=False, server_default="100000"),
    )
    op.add_column(
        "whale_settings",
        sa.Column("large_amount_threshold_usdc", DECIMAL, nullable=False, server_default="500000"),
    )
    op.execute(
        """
        UPDATE whale_settings
        SET registration_window_days = 7,
            single_trade_threshold_usdc = 100000,
            cumulative_threshold_usdc = 100000,
            new_account_threshold_usdc = 100000,
            large_amount_threshold_usdc = 500000
        WHERE id = 1
        """
    )
    with op.batch_alter_table("whale_settings") as batch:
        batch.create_check_constraint(
            "ck_whale_settings_new_account_threshold",
            "new_account_threshold_usdc >= collect_filter_amount_usdc",
        )
        batch.create_check_constraint(
            "ck_whale_settings_large_amount_threshold",
            "large_amount_threshold_usdc >= collect_filter_amount_usdc",
        )

    op.add_column(
        "whale_entries",
        sa.Column("follow_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("whale_entries", sa.Column("follow_ineligible_reason", sa.Text()))
    op.add_column("whale_entries", sa.Column("position_checked_at", sa.DateTime()))
    op.add_column("whale_entries", sa.Column("settlement_price", DECIMAL))
    op.add_column("whale_entries", sa.Column("settled_at", sa.DateTime()))

    op.create_table(
        "whale_entry_rule_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entry_id",
            sa.Integer(),
            sa.ForeignKey("whale_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rule_type", sa.String(30), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("first_triggered_at", sa.DateTime(), nullable=False),
        sa.Column("last_qualified_at", sa.DateTime(), nullable=False),
        sa.Column("inactive_at", sa.DateTime()),
        sa.Column("inactive_reason", sa.String(50)),
        sa.Column("threshold_usdc_snapshot", DECIMAL, nullable=False),
        sa.Column("registration_days_snapshot", sa.Integer()),
        sa.UniqueConstraint("entry_id", "rule_type", name="uq_whale_entry_rule_state"),
        sa.CheckConstraint(
            "rule_type IN ('new_account', 'large_amount')",
            name="ck_whale_entry_rule_state_type",
        ),
    )
    op.create_index(
        "ix_whale_entry_rule_active",
        "whale_entry_rule_states",
        ["rule_type", "active", "last_qualified_at"],
    )
    op.execute(
        """
        INSERT INTO whale_entry_rule_states
            (entry_id, rule_type, active, first_triggered_at, last_qualified_at,
             inactive_at, inactive_reason, threshold_usdc_snapshot,
             registration_days_snapshot)
        SELECT id, 'new_account',
               CASE WHEN status != 'exited' AND net_size > 0 THEN 1 ELSE 0 END,
               computed_at, computed_at,
               CASE WHEN status = 'exited' OR net_size <= 0 THEN computed_at ELSE NULL END,
               CASE WHEN status = 'exited' OR net_size <= 0 THEN 'position_exited' ELSE NULL END,
               100000, 7
        FROM whale_entries
        """
    )
    op.execute(
        """
        UPDATE whale_entries
        SET follow_eligible = CASE WHEN status != 'exited' AND net_size > 0 THEN 1 ELSE 0 END,
            follow_ineligible_reason = CASE
                WHEN status = 'exited' OR net_size <= 0 THEN 'position_exited'
                ELSE NULL
            END,
            position_checked_at = computed_at
        """
    )


def downgrade() -> None:
    op.drop_index("ix_whale_entry_rule_active", table_name="whale_entry_rule_states")
    op.drop_table("whale_entry_rule_states")
    op.drop_column("whale_entries", "settled_at")
    op.drop_column("whale_entries", "settlement_price")
    op.drop_column("whale_entries", "position_checked_at")
    op.drop_column("whale_entries", "follow_ineligible_reason")
    op.drop_column("whale_entries", "follow_eligible")
    with op.batch_alter_table("whale_settings") as batch:
        batch.drop_constraint("ck_whale_settings_large_amount_threshold", type_="check")
        batch.drop_constraint("ck_whale_settings_new_account_threshold", type_="check")
    op.drop_column("whale_settings", "large_amount_threshold_usdc")
    op.drop_column("whale_settings", "new_account_threshold_usdc")
