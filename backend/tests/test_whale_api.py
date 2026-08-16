from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

from backend.models import (
    WhaleEntry,
    WhaleMarket,
    WhaleSettings,
    WhaleTag,
    WhaleTrade,
    WhaleWallet,
)
from backend.monitor import utcnow

CONDITION_ID = "0x" + "a" * 64
ASSET_YES = "100000000000000000001"
ASSET_NO = "100000000000000000002"
WALLET_HEDGED = "0x1111111111111111111111111111111111111111"
WALLET_DIRECTIONAL = "0x2222222222222222222222222222222222222222"


async def seed_two_sided_whale_market(database) -> None:
    now = utcnow()
    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        assert settings is not None
        settings.last_scan_at = now
        settings.updated_at = now
        session.add_all(
            [
                WhaleMarket(
                    condition_id=CONDITION_ID,
                    title="总决赛谁会获胜？",
                    market_slug="championship-winner",
                    event_slug="championship-final",
                    icon_url="https://example.test/final.png",
                    outcomes_json=json.dumps(["Yes", "No"]),
                    outcome_prices_json=json.dumps(["0.60", "0.40"]),
                    clob_token_ids_json=json.dumps([ASSET_YES, ASSET_NO]),
                    tags_json=json.dumps([{"id": "64", "slug": "esports", "label": "Esports"}]),
                    closed=False,
                    active=True,
                    accepting_orders=True,
                    neg_risk=False,
                    end_date=now + timedelta(hours=4),
                    end_date_is_date_only=False,
                    liquidity=Decimal("25000"),
                    volume_24h=Decimal("80000"),
                    best_bid=Decimal("0.59"),
                    best_ask=Decimal("0.61"),
                    order_min_size=Decimal("5"),
                    tick_size=Decimal("0.01"),
                    fee_rate=Decimal("0.05"),
                    fee_exponent=Decimal("1"),
                    refreshed_at=now,
                ),
                WhaleTag(
                    id="64",
                    slug="esports",
                    label="Esports",
                    market_count=1,
                    refreshed_at=now,
                ),
                WhaleWallet(
                    proxy_wallet=WALLET_HEDGED,
                    display_name="双边钱包",
                    pseudonym="Hedged Whale",
                    profile_created_at=now - timedelta(days=120),
                    verified_badge=True,
                    taker_tier=2,
                    taker_tier_name="Tier 2",
                    weighted_volume=Decimal("500000"),
                    profile_missing=False,
                    refreshed_at=now,
                ),
                WhaleWallet(
                    proxy_wallet=WALLET_DIRECTIONAL,
                    display_name="方向钱包",
                    pseudonym="Directional Whale",
                    profile_created_at=now - timedelta(days=30),
                    verified_badge=False,
                    taker_tier=1,
                    taker_tier_name="Tier 1",
                    weighted_volume=Decimal("120000"),
                    profile_missing=False,
                    refreshed_at=now,
                ),
                WhaleEntry(
                    proxy_wallet=WALLET_HEDGED,
                    asset_id=ASSET_YES,
                    condition_id=CONDITION_ID,
                    outcome="Yes",
                    outcome_index=0,
                    gross_buy_usdc=Decimal("18000"),
                    gross_buy_size=Decimal("30000"),
                    sold_size=Decimal("0"),
                    sold_usdc=Decimal("0"),
                    net_size=Decimal("30000"),
                    net_ratio=Decimal("100"),
                    avg_buy_price=Decimal("0.60"),
                    max_single_usdc=Decimal("18000"),
                    trade_count=1,
                    first_buy_at=now - timedelta(minutes=20),
                    last_buy_at=now - timedelta(minutes=20),
                    status="holding",
                    hedged=True,
                    window_start=now - timedelta(hours=24),
                    computed_at=now,
                ),
                WhaleEntry(
                    proxy_wallet=WALLET_HEDGED,
                    asset_id=ASSET_NO,
                    condition_id=CONDITION_ID,
                    outcome="No",
                    outcome_index=1,
                    gross_buy_usdc=Decimal("12000"),
                    gross_buy_size=Decimal("30000"),
                    sold_size=Decimal("0"),
                    sold_usdc=Decimal("0"),
                    net_size=Decimal("30000"),
                    net_ratio=Decimal("100"),
                    avg_buy_price=Decimal("0.40"),
                    max_single_usdc=Decimal("12000"),
                    trade_count=1,
                    first_buy_at=now - timedelta(minutes=18),
                    last_buy_at=now - timedelta(minutes=18),
                    status="holding",
                    hedged=True,
                    window_start=now - timedelta(hours=24),
                    computed_at=now,
                ),
                WhaleEntry(
                    proxy_wallet=WALLET_DIRECTIONAL,
                    asset_id=ASSET_YES,
                    condition_id=CONDITION_ID,
                    outcome="Yes",
                    outcome_index=0,
                    gross_buy_usdc=Decimal("15000"),
                    gross_buy_size=Decimal("25000"),
                    sold_size=Decimal("2500"),
                    sold_usdc=Decimal("1500"),
                    net_size=Decimal("22500"),
                    net_ratio=Decimal("90"),
                    avg_buy_price=Decimal("0.60"),
                    max_single_usdc=Decimal("15000"),
                    trade_count=1,
                    first_buy_at=now - timedelta(minutes=15),
                    last_buy_at=now - timedelta(minutes=15),
                    status="holding",
                    hedged=False,
                    window_start=now - timedelta(hours=24),
                    computed_at=now,
                ),
            ]
        )
        session.add_all(
            [
                WhaleTrade(
                    fingerprint="a" * 64,
                    proxy_wallet=WALLET_HEDGED,
                    asset_id=ASSET_YES,
                    condition_id=CONDITION_ID,
                    side="BUY",
                    size=Decimal("30000"),
                    price=Decimal("0.60"),
                    amount=Decimal("18000"),
                    outcome="Yes",
                    outcome_index=0,
                    title="总决赛谁会获胜？",
                    market_slug="championship-winner",
                    event_slug="championship-final",
                    icon_url="https://example.test/final.png",
                    display_name="双边钱包",
                    transaction_hash="0xhedgedyes",
                    timestamp=now - timedelta(minutes=20),
                    imported_at=now,
                ),
                WhaleTrade(
                    fingerprint="b" * 64,
                    proxy_wallet=WALLET_HEDGED,
                    asset_id=ASSET_NO,
                    condition_id=CONDITION_ID,
                    side="BUY",
                    size=Decimal("30000"),
                    price=Decimal("0.40"),
                    amount=Decimal("12000"),
                    outcome="No",
                    outcome_index=1,
                    title="总决赛谁会获胜？",
                    market_slug="championship-winner",
                    event_slug="championship-final",
                    icon_url="https://example.test/final.png",
                    display_name="双边钱包",
                    transaction_hash="0xhedgedno",
                    timestamp=now - timedelta(minutes=18),
                    imported_at=now,
                ),
                WhaleTrade(
                    fingerprint="c" * 64,
                    proxy_wallet=WALLET_DIRECTIONAL,
                    asset_id=ASSET_YES,
                    condition_id=CONDITION_ID,
                    side="BUY",
                    size=Decimal("25000"),
                    price=Decimal("0.60"),
                    amount=Decimal("15000"),
                    outcome="Yes",
                    outcome_index=0,
                    title="总决赛谁会获胜？",
                    market_slug="championship-winner",
                    event_slug="championship-final",
                    icon_url="https://example.test/final.png",
                    display_name="方向钱包",
                    transaction_hash="0xdirectional",
                    timestamp=now - timedelta(minutes=15),
                    imported_at=now,
                ),
            ]
        )
        await session.commit()


def test_whale_routes_return_503_when_environment_switch_is_off(app_client_factory):
    client, _ = app_client_factory([[]], whale_enabled=False)

    assert client.get("/api/whales/settings").status_code == 503
    assert client.get("/api/whales/markets").status_code == 503
    assert client.post("/api/whales/scan").status_code == 503


def test_whale_settings_read_update_syncs_thresholds_and_validates(app_client_factory):
    client, _ = app_client_factory([[]])

    initial = client.get("/api/whales/settings")
    assert initial.status_code == 200, initial.text
    assert initial.json()["cumulative_threshold_usdc"] == 10000.0
    assert initial.json()["single_trade_threshold_usdc"] == 10000.0

    updated = client.put(
        "/api/whales/settings",
        json={"cumulative_threshold_usdc": 50000},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["cumulative_threshold_usdc"] == 50000.0
    assert updated.json()["single_trade_threshold_usdc"] == 50000.0
    persisted = client.get("/api/whales/settings")
    assert persisted.json()["cumulative_threshold_usdc"] == 50000.0
    assert persisted.json()["single_trade_threshold_usdc"] == 50000.0

    below_collection = client.put(
        "/api/whales/settings",
        json={"cumulative_threshold_usdc": 500},
    )
    assert below_collection.status_code == 422
    assert "采集金额阈值" in below_collection.text

    invalid_ratios = client.put(
        "/api/whales/settings",
        json={"holding_ratio_threshold": 80, "exited_ratio_threshold": 90},
    )
    assert invalid_ratios.status_code == 422


def test_whale_markets_empty_response_has_stable_shape(app_client_factory):
    client, _ = app_client_factory([[]])

    response = client.get("/api/whales/markets")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["items"] == []
    assert payload["total"] == 0
    assert payload["stale"] is True
    assert payload["generated_at"].endswith("Z")
    assert payload["window_start"].endswith("Z")


def test_whale_market_api_merges_both_sides_and_returns_entry_trades(app_client_factory):
    client, _ = app_client_factory([[]])
    client.portal.call(seed_two_sided_whale_market, client.app.state.database)

    response = client.get("/api/whales/markets?tag_slug=esports")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    market = payload["items"][0]
    assert market["condition_id"] == CONDITION_ID
    assert market["both_sides"] is True
    assert [side["outcome_index"] for side in market["sides"]] == [0, 1]
    assert market["whale_wallet_count"] == 2
    entries = [entry for side in market["sides"] for entry in side["entries"]]
    hedged_entries = [entry for entry in entries if entry["proxy_wallet"] == WALLET_HEDGED]
    assert len(hedged_entries) == 2
    assert all(entry["hedged"] is True for entry in hedged_entries)
    assert any(entry["hedged"] is False for entry in entries)

    tags = client.get("/api/whales/tags")
    assert tags.status_code == 200
    assert tags.json() == [{"id": "64", "slug": "esports", "label": "Esports", "market_count": 1}]

    detail = client.get(f"/api/whales/markets/{CONDITION_ID}")
    assert detail.status_code == 200, detail.text
    detail_entries = [entry for side in detail.json()["sides"] for entry in side["entries"]]
    hedged_yes = next(
        entry
        for entry in detail_entries
        if entry["proxy_wallet"] == WALLET_HEDGED and entry["avg_buy_price"] == 0.6
    )
    assert len(hedged_yes["trades"]) == hedged_yes["trade_count"] == 1
    assert hedged_yes["trades"][0]["transaction_hash"] == "0xhedgedyes"
    assert hedged_yes["trades"][0]["condition_id"] == CONDITION_ID
