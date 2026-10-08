"""Independent public collection schema; contains no personal tables."""

from alembic import op

revision = "0001_public"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE collection_batches (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, 
    completed_at DATETIME NOT NULL, 
    scope_version INTEGER NOT NULL, 
    coverage_json TEXT NOT NULL
)

""")
    op.execute("""
CREATE TABLE collection_events (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, 
    batch_id INTEGER NOT NULL, 
    condition_id VARCHAR(66) NOT NULL, 
    category VARCHAR(40) NOT NULL, 
    deleted BOOLEAN NOT NULL, 
    payload_json TEXT NOT NULL, 
    delta_json TEXT NOT NULL
)

""")
    op.execute("""CREATE INDEX ix_collection_events_condition_id
ON collection_events (condition_id)""")
    op.execute("""CREATE INDEX ix_collection_events_batch_id
ON collection_events (batch_id)""")
    op.execute("""
CREATE TABLE collection_meta (
    id INTEGER NOT NULL, 
    source_id VARCHAR(64) NOT NULL, 
    scope_version INTEGER NOT NULL, 
    categories_json TEXT NOT NULL, 
    cursor_at DATETIME, 
    incomplete_until DATETIME, 
    PRIMARY KEY (id)
)

""")
    op.execute("""
CREATE TABLE whale_backfill_signal_states (
    proxy_wallet VARCHAR(42) NOT NULL, 
    asset_id VARCHAR(100) NOT NULL, 
    condition_id VARCHAR(66) NOT NULL, 
    auto_follow_after DATETIME NOT NULL, 
    awaiting_new_buy BOOLEAN NOT NULL, 
    PRIMARY KEY (proxy_wallet, asset_id)
)

""")
    op.execute("""
CREATE TABLE whale_market_scan_pages (
    id INTEGER NOT NULL, 
    condition_id VARCHAR(66) NOT NULL, 
    payload_json TEXT NOT NULL, 
    PRIMARY KEY (id)
)

""")
    op.execute("""CREATE INDEX ix_whale_market_scan_pages_condition_id
ON whale_market_scan_pages (condition_id)""")
    op.execute("""
CREATE TABLE whale_market_scan_states (
    condition_id VARCHAR(66) NOT NULL, 
    coverage_start DATETIME, 
    coverage_end DATETIME, 
    batch_start DATETIME, 
    batch_end DATETIME, 
    pending_ranges_json TEXT NOT NULL, 
    last_checked_at DATETIME, 
    auto_follow_after DATETIME, 
    last_error TEXT, 
    PRIMARY KEY (condition_id)
)

""")
    op.execute("""
CREATE TABLE whale_markets (
    condition_id VARCHAR(66) NOT NULL, 
    title TEXT NOT NULL, 
    market_slug VARCHAR(500), 
    event_slug VARCHAR(500), 
    icon_url TEXT, 
    outcomes_json TEXT NOT NULL, 
    outcome_prices_json TEXT NOT NULL, 
    clob_token_ids_json TEXT NOT NULL, 
    tags_json TEXT NOT NULL, 
    closed BOOLEAN NOT NULL, 
    active BOOLEAN NOT NULL, 
    accepting_orders BOOLEAN NOT NULL, 
    neg_risk BOOLEAN NOT NULL, 
    end_date DATETIME, 
    end_date_is_date_only BOOLEAN NOT NULL, 
    liquidity NUMERIC(38, 18) NOT NULL, 
    volume_24h NUMERIC(38, 18) NOT NULL, 
    best_bid NUMERIC(38, 18), 
    best_ask NUMERIC(38, 18), 
    order_min_size NUMERIC(38, 18) NOT NULL, 
    tick_size NUMERIC(38, 18) NOT NULL, 
    fee_rate NUMERIC(38, 18) NOT NULL, 
    fee_exponent NUMERIC(38, 18) NOT NULL, 
    refreshed_at DATETIME NOT NULL, 
    PRIMARY KEY (condition_id)
)

""")
    op.execute("""CREATE INDEX ix_whale_markets_end_date
ON whale_markets (end_date)""")
    op.execute("""CREATE INDEX ix_whale_markets_refreshed
ON whale_markets (refreshed_at)""")
    op.execute("""
CREATE TABLE whale_trades (
    id INTEGER NOT NULL, 
    fingerprint VARCHAR(64) NOT NULL, 
    proxy_wallet VARCHAR(42) NOT NULL, 
    asset_id VARCHAR(100) NOT NULL, 
    condition_id VARCHAR(66) NOT NULL, 
    side VARCHAR(4) NOT NULL, 
    size NUMERIC(38, 18) NOT NULL, 
    price NUMERIC(38, 18) NOT NULL, 
    amount NUMERIC(38, 18) NOT NULL, 
    outcome VARCHAR(200) NOT NULL, 
    outcome_index INTEGER NOT NULL, 
    title TEXT NOT NULL, 
    market_slug VARCHAR(500) NOT NULL, 
    event_slug VARCHAR(500) NOT NULL, 
    icon_url TEXT, 
    display_name VARCHAR(200), 
    transaction_hash VARCHAR(100), 
    timestamp DATETIME NOT NULL, 
    imported_at DATETIME NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_whale_trades_fingerprint UNIQUE (fingerprint)
)

""")
    op.execute("""CREATE INDEX ix_whale_trades_condition_time
ON whale_trades (condition_id, timestamp)""")
    op.execute("""CREATE INDEX ix_whale_trades_time
ON whale_trades (timestamp)""")
    op.execute("""CREATE INDEX ix_whale_trades_wallet_asset_time
ON whale_trades (proxy_wallet, asset_id, timestamp)""")
    op.execute("""
CREATE TABLE whale_wallets (
    proxy_wallet VARCHAR(42) NOT NULL, 
    display_name VARCHAR(200), 
    pseudonym VARCHAR(200), 
    profile_image_url TEXT, 
    profile_created_at DATETIME, 
    verified_badge BOOLEAN NOT NULL, 
    taker_tier INTEGER, 
    taker_tier_name VARCHAR(50), 
    weighted_volume NUMERIC(38, 18), 
    profile_missing BOOLEAN NOT NULL, 
    refreshed_at DATETIME NOT NULL, 
    PRIMARY KEY (proxy_wallet)
)

""")


def downgrade():
    op.drop_table("whale_wallets")
    op.drop_table("whale_trades")
    op.drop_table("whale_markets")
    op.drop_table("whale_market_scan_states")
    op.drop_table("whale_market_scan_pages")
    op.drop_table("whale_backfill_signal_states")
    op.drop_table("collection_meta")
    op.drop_table("collection_events")
    op.drop_table("collection_batches")
