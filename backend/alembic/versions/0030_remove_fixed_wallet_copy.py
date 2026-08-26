"""Remove fixed-wallet monitoring and copy trading.

Revision ID: 0030_remove_fixed_wallet_copy
Revises: 0029_wallet_trade_reconciliation_index
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_remove_fixed_wallet_copy"
down_revision: str | None = "0029_wallet_trade_reconciliation_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _count(connection: sa.Connection, statement: str) -> int:
    return int(connection.execute(sa.text(statement)).scalar() or 0)


def upgrade() -> None:
    connection = op.get_bind()
    active_positions = _count(
        connection,
        "SELECT COUNT(*) FROM copy_positions "
        "WHERE attributed_size > 0 OR status IN ('opening','open','closing','redeeming')",
    )
    active_orders = _count(
        connection,
        "SELECT COUNT(*) FROM copy_orders WHERE status IN ('open','submitted','pending')",
    )
    active_redemptions = _count(
        connection,
        "SELECT COUNT(*) FROM copy_redemptions "
        "WHERE status IN ('pending','submitted','unknown','retrying')",
    )
    if active_positions or active_orders or active_redemptions:
        raise RuntimeError("固定钱包跟单仍有活动仓位、订单或赎回，必须先完成资金收尾再迁移")

    copy_execution_ids = [
        int(row[0])
        for row in connection.execute(
            sa.text(
                "SELECT DISTINCT execution_id FROM copy_redemptions WHERE execution_id IS NOT NULL"
            )
        )
    ]

    with op.batch_alter_table("execution_accounts", recreate="always") as batch:
        batch.drop_column("wallet_id")

    for table_name in (
        "copy_fills",
        "copy_ledger",
        "copy_redemptions",
        "copy_orders",
        "copy_positions",
        "copy_subscriptions",
    ):
        op.drop_table(table_name)

    if copy_execution_ids:
        connection.execute(
            sa.text("DELETE FROM copy_redemption_executions WHERE id IN :ids").bindparams(
                sa.bindparam("ids", expanding=True)
            ),
            {"ids": copy_execution_ids},
        )
    op.rename_table("copy_redemption_executions", "redemption_executions")

    for table_name in (
        "position_event_fills",
        "position_overlap_alerts",
        "position_overlap_periods",
        "position_change_candidates",
        "current_positions",
        "wallet_trades",
        "position_events",
        "global_settings",
        "watched_wallets",
    ):
        op.drop_table(table_name)


def downgrade() -> None:
    raise RuntimeError("固定钱包监控与跟单历史已按产品要求永久删除；请使用迁移前备份恢复旧版本")
