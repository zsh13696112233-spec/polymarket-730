"""Remove paper copy trading and allow multiple live subscriptions.

Revision ID: 0014_live_only_multi_wallet
Revises: 0013_simple_copy_trading_v2
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_live_only_multi_wallet"
down_revision: str | None = "0013_simple_copy_trading_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Paper data is intentionally removed instead of archived. Real rehearsal
    # orders have no subscription and use mode='live', so they are preserved.
    op.execute(
        """
        DELETE FROM copy_redemptions
        WHERE copy_position_id IN (
            SELECT id FROM copy_positions
            WHERE subscription_id IN (
                SELECT id FROM copy_subscriptions WHERE mode = 'paper'
            )
        )
        """
    )
    op.execute(
        """
        DELETE FROM copy_ledger
        WHERE subscription_id IN (
            SELECT id FROM copy_subscriptions WHERE mode = 'paper'
        )
        """
    )
    op.execute(
        """
        DELETE FROM copy_fills
        WHERE order_id IN (SELECT id FROM copy_orders WHERE mode = 'paper')
        """
    )
    op.execute("DELETE FROM copy_orders WHERE mode = 'paper'")
    op.execute(
        """
        DELETE FROM copy_positions
        WHERE subscription_id IN (
            SELECT id FROM copy_subscriptions WHERE mode = 'paper'
        )
        """
    )
    op.execute("DELETE FROM copy_subscriptions WHERE mode = 'paper'")

    op.drop_index("uq_copy_subscriptions_single_live", table_name="copy_subscriptions")
    with op.batch_alter_table("copy_subscriptions") as batch_op:
        batch_op.drop_column("mode")
    with op.batch_alter_table("copy_orders") as batch_op:
        batch_op.drop_column("mode")


def downgrade() -> None:
    with op.batch_alter_table("copy_orders") as batch_op:
        batch_op.add_column(sa.Column("mode", sa.String(20), nullable=False, server_default="live"))
    with op.batch_alter_table("copy_subscriptions") as batch_op:
        batch_op.add_column(sa.Column("mode", sa.String(20), nullable=False, server_default="live"))

    # The legacy schema allowed only one enabled live strategy. Preserve the
    # oldest one and disable the rest before restoring that constraint.
    op.execute(
        """
        UPDATE copy_subscriptions
        SET state = 'disabled'
        WHERE state != 'disabled'
          AND id NOT IN (
              SELECT id FROM copy_subscriptions
              WHERE state != 'disabled'
              ORDER BY id ASC
              LIMIT 1
          )
        """
    )
    op.create_index(
        "uq_copy_subscriptions_single_live",
        "copy_subscriptions",
        ["mode"],
        unique=True,
        sqlite_where=sa.text("mode = 'live' AND state != 'disabled'"),
    )
