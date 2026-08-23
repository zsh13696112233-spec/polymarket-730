from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from backend.models import (
    WhaleEntry,
    WhaleEntryRuleState,
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
                    profile_image_url="https://example.test/hedged-avatar.jpg",
                    profile_created_at=now - timedelta(days=2),
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
                    profile_created_at=now - timedelta(days=1),
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
                    follow_eligible=True,
                    position_checked_at=now,
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
                    follow_eligible=True,
                    position_checked_at=now,
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
                    follow_eligible=True,
                    position_checked_at=now,
                ),
            ]
        )
        await session.flush()
        entries = list((await session.scalars(select(WhaleEntry))).all())
        session.add_all(
            [
                WhaleEntryRuleState(
                    entry_id=entry.id,
                    rule_type="new_account",
                    active=True,
                    first_triggered_at=now,
                    last_qualified_at=now,
                    threshold_usdc_snapshot=Decimal("100000"),
                    registration_days_snapshot=7,
                )
                for entry in entries
            ]
            + [
                WhaleEntryRuleState(
                    entry_id=entries[0].id,
                    rule_type="large_amount",
                    active=True,
                    first_triggered_at=now,
                    last_qualified_at=now,
                    threshold_usdc_snapshot=Decimal("500000"),
                    registration_days_snapshot=None,
                )
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


async def move_directional_entry_to_settled_history(database) -> int:
    now = utcnow()
    async with database.sessions() as session:
        entry = await session.scalar(
            select(WhaleEntry).where(WhaleEntry.proxy_wallet == WALLET_DIRECTIONAL)
        )
        assert entry is not None
        state = await session.scalar(
            select(WhaleEntryRuleState).where(
                WhaleEntryRuleState.entry_id == entry.id,
                WhaleEntryRuleState.rule_type == "new_account",
            )
        )
        assert state is not None
        entry.status = "exited"
        entry.net_size = Decimal("0")
        entry.follow_eligible = False
        entry.follow_ineligible_reason = "market_closed"
        entry.settlement_price = Decimal("1")
        entry.settled_at = now
        state.active = False
        state.inactive_at = now
        state.inactive_reason = "market_closed"
        await session.commit()
        return entry.id


async def seed_whale_statistics(database) -> None:
    now = utcnow()
    async with database.sessions() as session:
        entries = list((await session.scalars(select(WhaleEntry).order_by(WhaleEntry.id))).all())
        assert len(entries) == 3
        entries[0].settlement_price = Decimal("1")
        entries[0].settled_at = now - timedelta(days=1)
        entries[1].settlement_price = Decimal("0")
        entries[1].settled_at = now - timedelta(days=1)
        entries[2].settlement_price = Decimal("0.5")
        entries[2].settled_at = now - timedelta(days=100)
        pending = WhaleEntry(
            proxy_wallet=WALLET_DIRECTIONAL,
            asset_id="pending-statistics-asset",
            condition_id=CONDITION_ID,
            outcome="Pending",
            outcome_index=2,
            gross_buy_usdc=Decimal("750000"),
            gross_buy_size=Decimal("1000000"),
            sold_size=Decimal("0"),
            sold_usdc=Decimal("0"),
            net_size=Decimal("1000000"),
            net_ratio=Decimal("100"),
            avg_buy_price=Decimal("0.75"),
            max_single_usdc=Decimal("750000"),
            trade_count=1,
            first_buy_at=now,
            last_buy_at=now,
            status="holding",
            hedged=False,
            window_start=now - timedelta(hours=24),
            computed_at=now,
            follow_eligible=False,
            position_checked_at=now,
        )
        session.add(pending)
        await session.flush()
        session.add(
            WhaleEntryRuleState(
                entry_id=pending.id,
                rule_type="new_account",
                active=True,
                first_triggered_at=now,
                last_qualified_at=now,
                threshold_usdc_snapshot=Decimal("100000"),
                registration_days_snapshot=7,
            )
        )
        await session.commit()


def test_whale_routes_return_503_when_environment_switch_is_off(app_client_factory):
    client, _ = app_client_factory([[]], whale_enabled=False)

    assert client.get("/api/whales/settings").status_code == 503
    assert client.get("/api/whales/markets").status_code == 503
    assert client.get("/api/whales/statistics").status_code == 503
    assert client.post("/api/whales/scan").status_code == 503


def test_whale_settings_read_update_syncs_thresholds_and_validates(app_client_factory):
    client, _ = app_client_factory([[]])

    initial = client.get("/api/whales/settings")
    assert initial.status_code == 200, initial.text
    assert initial.json()["cumulative_threshold_usdc"] == 100000.0
    assert initial.json()["single_trade_threshold_usdc"] == 100000.0
    assert initial.json()["new_account_threshold_usdc"] == 100000.0
    assert initial.json()["large_amount_threshold_usdc"] == 500000.0
    assert initial.json()["registration_window_days"] == 7

    updated = client.put(
        "/api/whales/settings",
        json={"new_account_threshold_usdc": 150000},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["new_account_threshold_usdc"] == 150000.0
    assert updated.json()["cumulative_threshold_usdc"] == 150000.0
    assert updated.json()["single_trade_threshold_usdc"] == 150000.0
    persisted = client.get("/api/whales/settings")
    assert persisted.json()["new_account_threshold_usdc"] == 150000.0
    assert persisted.json()["cumulative_threshold_usdc"] == 150000.0
    assert persisted.json()["single_trade_threshold_usdc"] == 150000.0

    legacy_single = client.put(
        "/api/whales/settings",
        json={"single_trade_threshold_usdc": 175000},
    )
    assert legacy_single.status_code == 200, legacy_single.text
    assert legacy_single.json()["new_account_threshold_usdc"] == 175000.0
    assert legacy_single.json()["cumulative_threshold_usdc"] == 175000.0
    assert legacy_single.json()["single_trade_threshold_usdc"] == 175000.0

    registration = client.put(
        "/api/whales/settings",
        json={"registration_window_days": 7},
    )
    assert registration.status_code == 200, registration.text
    assert registration.json()["registration_window_days"] == 7

    invalid_registration = client.put(
        "/api/whales/settings",
        json={"registration_window_days": 31},
    )
    assert invalid_registration.status_code == 422

    amounts = client.put(
        "/api/whales/settings",
        json={"default_follow_amount_usdc": 50, "max_follow_amount_usdc": 300},
    )
    assert amounts.status_code == 200, amounts.text
    assert amounts.json()["default_follow_amount_usdc"] == 50.0
    assert amounts.json()["max_follow_amount_usdc"] == 300.0

    invalid_amounts = client.put(
        "/api/whales/settings",
        json={"default_follow_amount_usdc": 301, "max_follow_amount_usdc": 300},
    )
    assert invalid_amounts.status_code == 422
    assert "默认买入金额" in invalid_amounts.text

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
    assert all("new_account" in entry["matched_rules"] for entry in entries)
    hedged_entries = [entry for entry in entries if entry["proxy_wallet"] == WALLET_HEDGED]
    assert len(hedged_entries) == 2
    assert all(
        entry["wallet_avatar_url"] == "https://example.test/hedged-avatar.jpg"
        for entry in hedged_entries
    )
    assert all(entry["hedged"] is True for entry in hedged_entries)
    assert any(entry["hedged"] is False for entry in entries)
    hedged_yes_summary = next(entry for entry in hedged_entries if entry["avg_buy_price"] == 0.6)
    assert hedged_yes_summary["net_size"] == 30000.0
    assert hedged_yes_summary["current_value_usdc"] == 18000.0
    directional = next(entry for entry in entries if entry["proxy_wallet"] == WALLET_DIRECTIONAL)
    assert directional["gross_buy_size"] == 25000.0
    assert directional["net_size"] == 22500.0
    assert directional["current_value_usdc"] == 13500.0

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

    large = client.get("/api/whales/markets?rule=large_amount")
    assert large.status_code == 200, large.text
    large_entries = [
        entry
        for item in large.json()["items"]
        for side in item["sides"]
        for entry in side["entries"]
    ]
    assert [entry["entry_id"] for entry in large_entries] == [hedged_yes["entry_id"]]
    assert set(large_entries[0]["matched_rules"]) == {"new_account", "large_amount"}


def test_whale_history_filters_rules_and_returns_settlement_pnl(app_client_factory):
    client, _ = app_client_factory([[]])
    client.portal.call(seed_two_sided_whale_market, client.app.state.database)
    entry_id = client.portal.call(
        move_directional_entry_to_settled_history, client.app.state.database
    )

    current = client.get("/api/whales/markets?rule=new_account")
    current_entry_ids = {
        entry["entry_id"]
        for market in current.json()["items"]
        for side in market["sides"]
        for entry in side["entries"]
    }
    history = client.get("/api/whales/history?rule=new_account&limit=1&offset=0")
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 1
    item = history.json()["items"][0]
    assert item["entry_id"] not in current_entry_ids
    assert item["rule_type"] == "new_account"
    assert item["inactive_reason"] == "market_closed"
    assert item["first_buy_at"] is not None
    assert item["settlement_price"] == 1.0
    assert item["hold_to_settlement_pnl_usdc"] == 10000.0

    large_history = client.get("/api/whales/history?rule=large_amount")
    assert large_history.status_code == 200
    assert large_history.json() == {"total": 0, "items": []}

    preview = client.post(
        "/api/whales/follow/preview",
        json={"entry_id": entry_id, "asset_id": ASSET_YES, "amount_usdc": 20},
    )
    assert preview.status_code == 409
    assert "历史记录" in preview.text


def test_whale_statistics_deduplicates_overlap_and_calculates_weighted_metrics(
    app_client_factory,
):
    client, _ = app_client_factory([[]])
    client.portal.call(seed_two_sided_whale_market, client.app.state.database)
    client.portal.call(seed_whale_statistics, client.app.state.database)

    response = client.get("/api/whales/statistics?range=all")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["coverage_start"] is not None
    assert payload["overall"]["settled_count"] == 3
    assert payload["overall"]["effective_sample_count"] == 2
    assert payload["overall"]["hit_count"] == 1
    assert payload["overall"]["miss_count"] == 1
    assert payload["overall"]["special_count"] == 1
    assert payload["overall"]["pending_count"] == 1
    assert payload["overall"]["hit_rate_percent"] == 50.0
    assert payload["overall"]["theoretical_cost_usdc"] == 45000.0
    assert payload["overall"]["theoretical_payout_usdc"] == 42500.0
    assert payload["overall"]["theoretical_pnl_usdc"] == -2500.0
    assert round(payload["overall"]["theoretical_roi_percent"], 4) == -5.5556
    assert payload["new_account"]["settled_count"] == 3
    assert payload["new_account"]["pending_count"] == 1
    assert payload["large_amount"]["settled_count"] == 1
    assert payload["large_amount"]["hit_rate_percent"] == 100.0
    assert payload["dual_match"]["settled_count"] == 1
    assert payload["dual_match"]["hit_count"] == 1

    recent = client.get("/api/whales/statistics?range=7d")
    assert recent.status_code == 200
    assert recent.json()["overall"]["settled_count"] == 2
    assert recent.json()["overall"]["special_count"] == 0
    assert recent.json()["overall"]["pending_count"] == 1
    assert recent.json()["trend"]
    assert all(item["key"].count("-") == 2 for item in recent.json()["trend"])


def test_whale_statistics_signal_filters_sort_and_validate(app_client_factory):
    client, _ = app_client_factory([[]])
    client.portal.call(seed_two_sided_whale_market, client.app.state.database)
    client.portal.call(seed_whale_statistics, client.app.state.database)

    hits = client.get(
        "/api/whales/statistics/signals?range=all&rule=both&result=hit"
        "&amount_band=lt_100k&sort=pnl_desc&limit=1&offset=0"
    )
    assert hits.status_code == 200, hits.text
    assert hits.json()["total"] == 1
    item = hits.json()["items"][0]
    assert item["result"] == "hit"
    assert set(item["matched_rules"]) == {"new_account", "large_amount"}
    assert item["theoretical_pnl_usdc"] == 12000.0
    assert item["wallet_age_days_at_trigger"] == 2
    assert item["polymarket_url"].endswith("/event/championship-final")

    specials = client.get("/api/whales/statistics/signals?result=special")
    assert specials.status_code == 200
    assert specials.json()["total"] == 1
    assert specials.json()["items"][0]["settlement_price"] == 0.5

    assert client.get("/api/whales/statistics?range=365d").status_code == 422
    assert client.get("/api/whales/statistics/signals?rule=unknown").status_code == 422
    assert client.get("/api/whales/statistics/signals?amount_band=unknown").status_code == 422
