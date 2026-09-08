"""Scope historical buy eligibility to wallets/assets and recover deferred signals."""

import sqlalchemy as sa
from alembic import op

revision = "0047_backfill_signal_eligibility"
down_revision = "0046_whale_market_scan_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "whale_backfill_signal_states",
        sa.Column("proxy_wallet", sa.String(42), primary_key=True),
        sa.Column("asset_id", sa.String(100), primary_key=True),
        sa.Column("condition_id", sa.String(66), nullable=False),
        sa.Column("auto_follow_after", sa.DateTime(), nullable=False),
        sa.Column("awaiting_new_buy", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    # 0046 did not persist fill provenance. Conservatively retain the historical
    # guard for fills imported after its market cutoff, plus undecided trade
    # entries. The latest market cutoff may have overwritten the original one,
    # so trigger time cannot reliably identify affected entries. Recovery only
    # permits a future new buy; never replay recorded buys on upgrade.
    op.execute(
        sa.text("""
        INSERT INTO whale_backfill_signal_states
            (proxy_wallet, asset_id, condition_id, auto_follow_after, awaiting_new_buy)
        SELECT affected.proxy_wallet, affected.asset_id, affected.condition_id,
               MAX(affected.cutoff),
               NOT EXISTS (
                   SELECT 1 FROM whale_auto_follow_decisions d
                   WHERE d.proxy_wallet = affected.proxy_wallet AND d.asset_id = affected.asset_id
               )
        FROM (
            SELECT t.proxy_wallet, t.asset_id, t.condition_id, s.auto_follow_after AS cutoff
            FROM whale_trades t JOIN whale_market_scan_states s ON s.condition_id = t.condition_id
            WHERE s.auto_follow_after IS NOT NULL
              AND t.timestamp <= s.auto_follow_after AND t.imported_at >= s.auto_follow_after
            UNION ALL
            SELECT e.proxy_wallet, e.asset_id, e.condition_id, s.auto_follow_after AS cutoff
            FROM whale_entries e
            JOIN whale_market_scan_states s ON s.condition_id = e.condition_id
            JOIN whale_entry_rule_states r ON r.entry_id = e.id
            WHERE s.auto_follow_after IS NOT NULL
              AND e.discovery_source = 'trades'
              AND NOT EXISTS (
                  SELECT 1 FROM whale_auto_follow_decisions d
                  WHERE d.proxy_wallet = e.proxy_wallet AND d.asset_id = e.asset_id
              )
        ) affected
        GROUP BY affected.proxy_wallet, affected.asset_id, affected.condition_id
    """)
    )
    op.execute(
        sa.text("""
        UPDATE whale_backfill_signal_states AS g
        SET auto_follow_after = (
            SELECT MAX(observed.timestamp) FROM (
                SELECT g.auto_follow_after AS timestamp
                UNION ALL
                SELECT t.timestamp FROM whale_trades t
                WHERE t.proxy_wallet = g.proxy_wallet AND t.asset_id = g.asset_id
                UNION ALL
                SELECT e.last_buy_at FROM whale_entries e
                WHERE e.proxy_wallet = g.proxy_wallet AND e.asset_id = g.asset_id
            ) observed
        )
    """)
    )


def downgrade() -> None:
    op.drop_table("whale_backfill_signal_states")
