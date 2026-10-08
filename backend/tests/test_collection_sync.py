from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, inspect, select

from backend.collection_server import CollectionSettings, create_collection_app
from backend.collection_store import PublicDatabase, PublicStore, dumps
from backend.collection_sync import CollectionSyncScanner
from backend.config import Settings
from backend.db import Database
from backend.models import (
    CollectionCache,
    CollectionSyncState,
    WhaleAutoFollowDecision,
    WhaleBackfillSignalState,
    WhaleEntry,
    WhaleSettings,
    WhaleTrade,
)
from backend.polymarket import OfficialTag
from backend.tests.test_whale_scanner import PositionDiscoveryClient
from backend.time_utils import utcnow


class Upstream:
    def __init__(self):
        self.clients = {}
        self.calls = 0
        for index, category in enumerate(("sports", "esports", "politics")):
            client = PositionDiscoveryClient()
            client.condition_id = "0x" + str(index + 1) * 64
            client.asset_id = f"asset-{category}"
            client.wallet = "0x" + str(index + 1) * 40
            client.trade_timestamp = utcnow() - timedelta(seconds=30)
            self.clients[category] = client

    async def fetch_large_trades(self, **kwargs):
        self.calls += 1
        return [
            trade for client in self.clients.values() for trade in await client.fetch_large_trades()
        ]

    async def fetch_markets_with_tags(self, condition_ids):
        result = []
        for category, client in self.clients.items():
            if client.condition_id in condition_ids:
                market = (await client.fetch_markets_with_tags([client.condition_id]))[0]
                result.append(
                    replace(market, tags=(OfficialTag(id=category, slug=category, label=category),))
                )
        return result

    async def fetch_discovery_markets(self, **kwargs):
        return []

    async def fetch_public_profile(self, address):
        for client in self.clients.values():
            if client.wallet == address:
                return await client.fetch_public_profile(address)
        raise AssertionError("unexpected wallet")

    async def fetch_active_positions(self, user, condition_ids):
        for client in self.clients.values():
            if client.wallet == user:
                return await client.fetch_active_positions(user, condition_ids=condition_ids)
        raise AssertionError("unexpected wallet")


@pytest.fixture
async def environment():
    async with AsyncExitStack() as stack:
        upstream = Upstream()
        app = create_collection_app(
            CollectionSettings(database_url="sqlite+aiosqlite:///:memory:"),
            client=upstream,
            start_worker=False,
        )
        await stack.enter_async_context(app.router.lifespan_context(app))
        http = await stack.enter_async_context(
            httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://server")
        )
        databases = []

        async def local(categories):
            database = Database(
                Settings(
                    database_url="sqlite+aiosqlite:///:memory:",
                    trading_enabled=False,
                    start_monitor=False,
                )
            )
            await database.initialize()
            databases.append(database)
            now = utcnow()
            async with database.sessions() as session:
                session.add(
                    WhaleSettings(
                        id=1,
                        monitor_categories_json=dumps(categories),
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.add(CollectionSyncState(id=1, host="127.0.0.1"))
                await session.commit()
            scanner = CollectionSyncScanner(
                database=database,
                client=SimpleNamespace(),
                settings=database.settings,
                sync_http=http,
            )
            return database, scanner

        yield app, upstream, http, local
        for database in databases:
            await database.close()


async def counts(database, model):
    async with database.sessions() as session:
        return await session.scalar(select(func.count()).select_from(model))


async def test_two_clients_filter_and_never_start_upstream(environment):
    app, upstream, http, local = environment
    await app.state.worker.tick()
    status = (await http.get("/api/collection/v1/status")).json()
    assert status["supported_categories"] == ["sports", "esports"]
    assert upstream.calls == 1
    db1, scanner1 = await local(["sports"])
    db2, scanner2 = await local(["esports"])
    assert await scanner1.tick()
    assert await scanner2.tick()
    assert upstream.calls == 1
    for database, expected in ((db1, "sports"), (db2, "esports")):
        async with database.sessions() as session:
            trades = list((await session.scalars(select(WhaleTrade))).all())
            assert [row.asset_id for row in trades] == [f"asset-{expected}"]
            cache = await session.scalar(select(CollectionCache))
            assert cache.category == expected
        assert await counts(database, WhaleEntry) == 1
        assert await counts(database, WhaleAutoFollowDecision) == 0
    # Polling an unchanged watermark does not collect or create another trade/decision.
    assert await scanner1.tick()
    assert await counts(db1, WhaleTrade) == 1
    assert upstream.calls == 1


async def test_baseline_does_not_consume_next_real_buy(environment):
    app, upstream, _http, local = environment
    await app.state.worker.tick()
    database, scanner = await local(["sports"])
    assert await scanner.tick()
    assert await counts(database, WhaleAutoFollowDecision) == 0
    client = upstream.clients["sports"]
    client.trade_timestamp = utcnow() + timedelta(seconds=1)
    # Move both clocks forward past the baseline without sleeping.
    baseline = client.trade_timestamp - timedelta(seconds=2)
    async with database.sessions() as session:
        gate = await session.get(WhaleBackfillSignalState, (client.wallet, client.asset_id))
        gate.auto_follow_after = baseline
        await session.commit()
    client.trade_timestamp = utcnow()
    client.transaction_hash = "new-buy"
    await app.state.worker.tick()
    assert await scanner.tick()
    assert await counts(database, WhaleAutoFollowDecision) == 1
    assert await scanner.tick()
    assert await counts(database, WhaleAutoFollowDecision) == 1


async def test_fixed_snapshot_pages_and_empty_filtered_watermark(environment):
    app, _upstream, http, _local = environment
    batch = await app.state.worker.tick()
    status = (await http.get("/api/collection/v1/status")).json()
    params = dict(
        categories="sports,esports", source_id=status["source_id"], watermark=batch, limit=1
    )
    first = (await http.get("/api/collection/v1/snapshot", params=params)).json()
    assert not first["complete"]
    second_batch = await app.state.worker.tick()
    second = (
        await http.get(
            "/api/collection/v1/snapshot", params=params | {"after": first["next_after"]}
        )
    ).json()
    assert second["complete"] and second["batch"] == batch
    assert first["events"][0]["condition_id"] != second["events"][0]["condition_id"]
    result = await app.state.store.page(
        categories=["sports"],
        source_id=status["source_id"],
        watermark=second_batch,
        cursor=second_batch,
    )
    assert result["events"] == [] and result["scanned_through"] == second_batch
    # Incremental fills do not resend the already imported trade detail.
    changes = await app.state.store.page(
        categories=["sports"], source_id=status["source_id"], watermark=second_batch, cursor=batch
    )
    assert changes["events"] == []
    assert changes["scanned_through"] == second_batch


@pytest.mark.parametrize("change", ["subscription", "rules"])
async def test_late_response_cannot_commit_after_configuration_change(environment, change):
    app, _upstream, _http, local = environment
    await app.state.worker.tick()
    database, scanner = await local(["sports"])
    started, release = asyncio.Event(), asyncio.Event()
    original = scanner._pages

    async def delayed(*args, **kwargs):
        result = await original(*args, **kwargs)
        started.set()
        await release.wait()
        return result

    scanner._pages = delayed
    task = asyncio.create_task(scanner.tick())
    await started.wait()
    async with database.sessions() as session:
        row = await session.get(CollectionSyncState, 1)
        settings = await session.get(WhaleSettings, 1)
        if change == "subscription":
            row.subscription_version += 1
            settings.monitor_categories_json = '["esports"]'
        else:
            settings.registration_window_days = 3
            settings.updated_at = utcnow()
        await session.commit()
    release.set()
    await task
    assert await counts(database, WhaleTrade) == 0
    async with database.sessions() as session:
        assert (await session.get(CollectionSyncState, 1)).categories_json == "{}"


async def test_restart_and_source_change_preserve_personal_history(environment):
    app, upstream, _http, local = environment
    await app.state.worker.tick()
    database, scanner = await local(["sports"])
    await scanner.tick()
    client = upstream.clients["sports"]
    client.trade_timestamp = utcnow()
    client.transaction_hash = "offline-buy"
    await app.state.worker.tick()
    scanner.recovering = True
    await scanner.tick()
    assert await counts(database, WhaleAutoFollowDecision) == 0
    async with database.sessions() as session:
        entry = await session.scalar(select(WhaleEntry))
        original_id = entry.id
        state = await session.get(CollectionSyncState, 1)
        state.source_id = "replaced"
        await session.commit()
    await scanner.tick()
    async with database.sessions() as session:
        assert (await session.scalar(select(WhaleEntry))).id == original_id
    assert await counts(database, WhaleAutoFollowDecision) == 0


async def test_failed_positions_not_empty_and_coverage_blocks_decisions(environment):
    app, upstream, _http, local = environment
    upstream.clients["sports"].position_error = True
    await app.state.worker.tick()
    database, scanner = await local(["sports"])
    await scanner.tick()
    async with database.sessions() as session:
        cached = await session.scalar(select(CollectionCache))
        payload = json.loads(cached.payload_json)
        assert payload["positions"][upstream.clients["sports"].wallet]["ok"] is False
        entry = await session.scalar(select(WhaleEntry))
        assert entry.follow_ineligible_reason == "position_check_failed"
    assert await counts(database, WhaleAutoFollowDecision) == 0


async def test_public_schema_is_independent_and_routes_read_only(tmp_path):
    database = PublicDatabase(
        Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'public.db'}")
    )
    store = PublicStore(database, ("sports", "esports"))
    await store.initialize()
    async with database.engine.connect() as connection:
        tables = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
    assert "collection_alembic_version" in tables
    assert not {
        "execution_accounts",
        "whale_settings",
        "whale_orders",
        "email_settings",
        "collection_sync_state",
    } & set(tables)
    await store.initialize()
    await database.close()
    app = create_collection_app(start_worker=False, client=SimpleNamespace())
    assert {route.path for route in app.routes} == {
        "/api/collection/v1/status",
        "/api/collection/v1/snapshot",
        "/api/collection/v1/changes",
    }
    assert all(route.methods == {"GET"} for route in app.routes)


async def test_mid_page_failure_commits_neither_data_nor_cursor(environment):
    app, _upstream, _http, local = environment
    await app.state.worker.tick()
    database, scanner = await local(["sports"])
    request = scanner._request

    async def broken(base, path, **params):
        result = await request(base, path, **params)
        if path == "snapshot":
            if params["after"]:
                raise httpx.ReadError("interrupted")
            result["complete"] = False
        return result

    scanner._request = broken
    await scanner.tick()
    assert await counts(database, WhaleTrade) == 0
    async with database.sessions() as session:
        state = await session.get(CollectionSyncState, 1)
        assert not json.loads(state.categories_json)["sports"].get("cursor")
    scanner._request = request
    await scanner.tick()
    assert await counts(database, WhaleTrade) == 1
    assert await counts(database, WhaleAutoFollowDecision) == 0


async def test_add_cancel_category_preserves_history_and_other_ready_scope(environment):
    app, upstream, _http, local = environment
    await app.state.worker.tick()
    database, scanner = await local(["sports"])
    await scanner.tick()
    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        settings.monitor_categories_json = '["sports","esports"]'
        state = await session.get(CollectionSyncState, 1)
        state.subscription_version += 1
        await session.commit()
    upstream.clients["sports"].trade_timestamp = utcnow()
    upstream.clients["sports"].transaction_hash = "new-sports-buy"
    await app.state.worker.tick()
    await scanner.tick()
    async with database.sessions() as session:
        decisions = list((await session.scalars(select(WhaleAutoFollowDecision))).all())
        assert [row.asset_id for row in decisions] == ["asset-sports"]
        settings = await session.get(WhaleSettings, 1)
        settings.monitor_categories_json = '["sports"]'
        state = await session.get(CollectionSyncState, 1)
        state.subscription_version += 1
        await session.commit()
    before = await counts(database, WhaleEntry)
    await app.state.worker.tick()
    await scanner.tick()
    assert await counts(database, WhaleEntry) == before
    async with database.sessions() as session:
        state = await session.get(CollectionSyncState, 1)
        assert set(json.loads(state.categories_json)) == {"sports"}


async def test_scope_version_removal_pauses_without_deleting_personal_entries(environment):
    app, _upstream, _http, local = environment
    await app.state.worker.tick()
    database, scanner = await local(["sports"])
    await scanner.tick()
    old_count = await counts(database, WhaleEntry)
    app.state.store.categories = ("esports",)
    await app.state.store.initialize()
    await scanner.tick()
    async with database.sessions() as session:
        state = await session.get(CollectionSyncState, 1)
        status = json.loads(state.categories_json)["sports"]
        assert not status["ready"] and "不支持" in status["reason"]
    assert await counts(database, WhaleEntry) == old_count
    assert await counts(database, WhaleAutoFollowDecision) == 0


async def test_expired_cursor_rebuilds_snapshot_without_buy(environment):
    from backend.models import CollectionBatch

    app, upstream, _http, local = environment
    await app.state.worker.tick()
    database, scanner = await local(["sports"])
    await scanner.tick()
    async with app.state.store.database.sessions() as session:
        batch = await session.get(CollectionBatch, 1)
        batch.completed_at = utcnow() - timedelta(hours=73)
        await session.commit()
    upstream.clients["sports"].trade_timestamp = utcnow()
    upstream.clients["sports"].transaction_hash = "missed-buy"
    await app.state.worker.tick()
    await scanner.tick()
    assert await counts(database, WhaleTrade) == 2
    assert await counts(database, WhaleAutoFollowDecision) == 0


async def test_market_reclassification_is_historical_then_next_buy_can_decide(environment):
    from backend.models import WhaleMarket

    app, upstream, _http, local = environment
    await app.state.worker.tick()
    database, scanner = await local(["esports"])
    await scanner.tick()
    sports = upstream.clients["sports"]
    # Move an existing sports market into esports; no new trade occurred.
    fetch = upstream.fetch_markets_with_tags

    async def corrected(condition_ids):
        return [
            replace(market, tags=(OfficialTag(id="esports", slug="esports", label="Esports"),))
            for market in await fetch(condition_ids)
        ]

    upstream.fetch_markets_with_tags = corrected
    async with app.state.store.database.sessions() as session:
        market = await session.get(WhaleMarket, sports.condition_id)
        market.refreshed_at = utcnow() - timedelta(minutes=5)
        await session.commit()
    # The market backfill endpoint must obey its condition filter.
    feed = upstream.fetch_large_trades

    async def filtered(**kwargs):
        items = await feed(**kwargs)
        return [
            item
            for item in items
            if not kwargs.get("condition_ids") or item.condition_id in kwargs["condition_ids"]
        ]

    upstream.fetch_large_trades = filtered
    await app.state.worker.tick()
    await scanner.tick()
    assert await counts(database, WhaleEntry) == 2
    assert await counts(database, WhaleAutoFollowDecision) == 0
    sports.trade_timestamp = utcnow()
    sports.transaction_hash = "after-correction"
    await app.state.worker.tick()
    await scanner.tick()
    async with database.sessions() as session:
        decisions = list((await session.scalars(select(WhaleAutoFollowDecision))).all())
        assert [row.asset_id for row in decisions] == [sports.asset_id]


async def test_incomplete_market_coverage_keeps_first_decision_available(environment):
    from backend.models import WhaleMarketScanState

    app, upstream, _http, local = environment
    sports = upstream.clients["sports"]
    async with app.state.store.database.sessions() as session:
        session.add(
            WhaleMarketScanState(
                condition_id=sports.condition_id, batch_end=utcnow(), last_error="incomplete"
            )
        )
        await session.commit()
    await app.state.worker.tick()
    database, scanner = await local(["sports"])
    await scanner.tick()
    assert await counts(database, WhaleAutoFollowDecision) == 0
    async with app.state.store.database.sessions() as session:
        state = await session.get(WhaleMarketScanState, sports.condition_id)
        state.batch_end = None
        state.last_error = None
        await session.commit()
    # Completion is recovery, not a new buy.
    await app.state.worker.tick()
    await scanner.tick()
    assert await counts(database, WhaleAutoFollowDecision) == 0
    sports.trade_timestamp = utcnow()
    sports.transaction_hash = "after-completion"
    await app.state.worker.tick()
    await scanner.tick()
    assert await counts(database, WhaleAutoFollowDecision) == 1


async def test_collection_connection_failure_does_not_call_local_public_client(environment):
    app, _upstream, _http, local = environment
    await app.state.worker.tick()
    database, scanner = await local(["sports"])
    await scanner.tick()

    async def offline(*_args, **_kwargs):
        raise httpx.ConnectError("offline")

    scanner._request = offline
    assert not await scanner.tick()
    assert await counts(database, WhaleAutoFollowDecision) == 0
    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        assert "服务器连接失败" in settings.last_scan_error


@pytest.mark.parametrize("existing", [False, True])
async def test_local_migration_preserves_selection_and_ids(tmp_path, existing):
    import sqlite3

    from alembic import command
    from alembic.config import Config

    path = tmp_path / "local.db"
    url = f"sqlite+aiosqlite:///{path}"

    def prepare_existing():
        config = Config("backend/alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "0051_weekly_auto_follow_report")
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE whale_settings SET monitor_categories_json=?, last_scan_error=?",
                ('["politics"]', "keep this"),
            )

    if existing:
        await asyncio.to_thread(prepare_existing)
    database = Database(Settings(database_url=url, trading_enabled=False, start_monitor=False))
    await database.initialize()
    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        assert json.loads(settings.monitor_categories_json) == (
            ["politics"] if existing else ["sports", "esports"]
        )
        if existing:
            assert settings.last_scan_error == "keep this"
        session.add(CollectionSyncState(id=1, host="127.0.0.1"))
        await session.commit()
    await database.close()
    await database.initialize()
    async with database.sessions() as session:
        assert (await session.get(CollectionSyncState, 1)).host == "127.0.0.1"
    await database.close()


async def test_protocol_limits_and_decimal_utc_contract(environment):
    app, upstream, http, _local = environment
    await app.state.worker.tick()
    status = (await http.get("/api/collection/v1/status")).json()
    params = dict(
        source_id=status["source_id"], categories="sports", watermark=status["latest_batch"]
    )
    for invalid in ({"limit": 201}, {"limit": 0}, {"after": -1}):
        assert (
            await http.get("/api/collection/v1/snapshot", params=params | invalid)
        ).status_code == 422
    assert (
        await http.get("/api/collection/v1/snapshot", params=params | {"categories": "politics"})
    ).status_code == 400
    result = (await http.get("/api/collection/v1/snapshot", params=params)).json()
    payload = result["events"][0]["payload"]
    assert isinstance(payload["trades"][0]["amount"], str)
    assert payload["trades"][0]["timestamp"].endswith("Z")
    # Filtering precedes public persistence; mixed-feed political fills are not stored.
    async with app.state.store.database.sessions() as session:
        assets = list(await session.scalars(select(WhaleTrade.asset_id)))
        assert upstream.clients["politics"].asset_id not in assets


async def test_opposite_positions_and_successful_empty_are_distinct(environment):
    app, upstream, _http, local = environment
    source = upstream.clients["sports"]
    fetch = source.fetch_active_positions

    async def opposing(user, condition_ids):
        positions = await fetch(user, condition_ids=condition_ids)
        return positions + [
            replace(positions[0], asset_id="asset-no", outcome="No", outcome_index=1)
        ]

    source.fetch_active_positions = opposing
    await app.state.worker.tick()
    database, scanner = await local(["sports"])
    await scanner.tick()
    async with database.sessions() as session:
        cached = await session.scalar(select(CollectionCache))
        result = json.loads(cached.payload_json)["positions"][source.wallet]
        assert result["ok"] and len(result["positions"]) == 2
        assert (await session.scalar(select(WhaleEntry))).hedged
    source.fetch_active_positions = fetch
    source.position_available = False
    app.state.worker._sources[(source.wallet, source.condition_id)]["checked_at"] = (
        utcnow() - timedelta(seconds=121)
    )
    await app.state.worker.tick()
    await scanner.tick()
    async with database.sessions() as session:
        cached = await session.scalar(select(CollectionCache))
        result = json.loads(cached.payload_json)["positions"][source.wallet]
        assert result["ok"] and result["positions"] == []
        assert (await session.scalar(select(WhaleEntry))).status == "exited"


async def test_position_discoveries_persist_between_focus_visits(environment):
    from decimal import Decimal

    from sqlalchemy import delete

    from backend.polymarket import WhaleMarketPositionSnapshot

    app, upstream, _http, _local = environment
    source = upstream.clients["sports"]
    discovery = WhaleMarketPositionSnapshot(
        proxy_wallet=source.wallet,
        asset_id=source.asset_id,
        condition_id=source.condition_id,
        outcome="Yes",
        outcome_index=0,
        size=Decimal("240000"),
        total_bought=Decimal("300000"),
        avg_price=Decimal("0.5"),
        current_price=Decimal("0.6"),
        current_value=Decimal("144000"),
        display_name="Whale",
        profile_image_url=None,
        verified_badge=False,
    )
    collector = app.state.worker.collector
    visited = False

    async def visit(*_args, **_kwargs):
        nonlocal visited
        collector._discovery_position_results = (
            {} if visited else {source.condition_id: [discovery]}
        )
        result = [] if visited else [discovery]
        visited = True
        return result, []

    collector._supplement_discovery = visit
    await app.state.worker.tick()
    async with app.state.store.database.sessions() as session:
        await session.execute(
            delete(WhaleTrade).where(WhaleTrade.condition_id == source.condition_id)
        )
        await session.commit()
    original = upstream.fetch_large_trades

    async def without_old_trade(**kwargs):
        return [
            item for item in await original(**kwargs) if item.condition_id != source.condition_id
        ]

    upstream.fetch_large_trades = without_old_trade
    batch = await app.state.worker.tick()
    status = await app.state.store.status()
    page = await app.state.store.page(
        categories=["sports"], source_id=status["source_id"], watermark=batch, snapshot=True
    )
    payload = page["events"][0]["payload"]
    assert payload["trades"] == []
    assert len(payload["discoveries"]) == 1
    assert payload["positions"][source.wallet]["ok"]


async def test_enrichment_bounds_large_backlog_and_groups_markets(environment):
    app, upstream, _http, _local = environment
    worker = app.state.worker
    now = utcnow()
    wallets = [f"wallet-{i:04}" for i in range(1200)]
    targets = {f"market-{i}": set(wallets) for i in range(3)}
    versions = {(wallet, market): "1" for market in targets for wallet in wallets}
    live_wallet = wallets[-1]
    last_buys = {(live_wallet, market): now for market in targets}
    calls = []

    async def profiles(*args, **kwargs):
        pass

    async def positions(user, condition_ids):
        calls.append((user, condition_ids))
        return []

    worker.collector._refresh_wallets = profiles
    upstream.fetch_active_positions = positions
    await worker._enrich(targets, versions, last_buys, [], now)
    assert len(calls) == 12
    assert live_wallet in {user for user, _ in calls}
    assert all(len(conditions) == 3 for _, conditions in calls)
    assert len(worker._sources) == 36
    assert all(result["ok"] for result in worker._sources.values())
    assert worker._source(wallets[100], "market-0", versions, wallets)["ok"] is False
    before = {user for user, _ in calls}
    calls.clear()
    await worker._enrich(targets, versions, last_buys, [], now)
    assert not before & {user for user, _ in calls}
    versions[(live_wallet, "market-0")] = "2"
    calls.clear()
    await worker._enrich(targets, versions, last_buys, [], now)
    assert (live_wallet, ["market-0"]) in calls


async def test_slow_enrichment_publishes_pending_without_false_empty(environment, monkeypatch):
    app, upstream, http, local = environment
    monkeypatch.setattr("backend.collection_server.ENRICHMENT_BUDGET_SECONDS", 0.02)
    monkeypatch.setattr("backend.collection_server.HISTORY_BUDGET_SECONDS", 0.02)

    async def hang(*args, **kwargs):
        await asyncio.Event().wait()

    upstream.fetch_active_positions = hang
    upstream.fetch_public_profile = hang
    upstream.fetch_discovery_markets = hang
    await asyncio.wait_for(app.state.worker.tick(), timeout=2)
    status = (await http.get("/api/collection/v1/status")).json()
    assert status["latest_batch"] == 1
    assert status["runtime"]["pending_sources"] == 2
    assert status["runtime"]["ready_sources"] == 0
    database, scanner = await local(["sports"])
    await scanner.tick()
    async with database.sessions() as session:
        payload = json.loads((await session.scalar(select(CollectionCache))).payload_json)
        source = payload["positions"][upstream.clients["sports"].wallet]
        assert not source["ok"] and source["status"] == "pending"
        assert source["checked_at"] is None
    assert await counts(database, WhaleAutoFollowDecision) == 0


async def test_live_buy_waits_for_verification_without_consuming_decision(environment):
    app, upstream, _http, local = environment
    await app.state.worker.tick()
    database, scanner = await local(["sports"])
    await scanner.tick()
    client = upstream.clients["sports"]
    async with database.sessions() as session:
        gate = await session.get(WhaleBackfillSignalState, (client.wallet, client.asset_id))
        gate.auto_follow_after = utcnow() - timedelta(seconds=1)
        await session.commit()
    client.trade_timestamp = utcnow()
    client.transaction_hash = "live-awaiting-position"
    client.position_error = True
    await app.state.worker.tick()
    await scanner.tick()
    assert await counts(database, WhaleAutoFollowDecision) == 0
    client.position_error = False
    await app.state.worker.tick()
    await scanner.tick()
    assert await counts(database, WhaleAutoFollowDecision) == 1
    await scanner.tick()
    assert await counts(database, WhaleAutoFollowDecision) == 1


async def test_initial_pending_verification_never_buys_history(environment):
    app, upstream, _http, local = environment
    client = upstream.clients["sports"]
    client.position_error = True
    await app.state.worker.tick()
    database, scanner = await local(["sports"])
    await scanner.tick()
    client.position_error = False
    await app.state.worker.tick()
    await scanner.tick()
    assert await counts(database, WhaleAutoFollowDecision) == 0


@pytest.mark.parametrize(
    ("coverage", "age", "expected", "absent"),
    [
        (False, 0, "数据覆盖尚未完整，可继续同步查看", "未更新"),
        (True, 181, "最新采集批次已超过 180 秒未更新", "覆盖"),
        (False, 181, "最新采集批次已超过 180 秒未更新", "已过期"),
    ],
)
def test_sync_pause_reason_distinguishes_coverage_and_age(coverage, age, expected, absent):
    from backend.collection_sync import sync_pause_reason

    reason = sync_pause_reason(coverage, (utcnow() - timedelta(seconds=age)).isoformat() + "Z")
    assert expected in reason and "自动买入暂停" in reason
    assert absent not in reason


async def test_coverage_message_matches_on_new_and_unchanged_batches(environment):
    app, _upstream, _http, local = environment
    from backend.models import CollectionMeta

    async with app.state.store.database.sessions() as session:
        meta = await session.get(CollectionMeta, 1)
        meta.incomplete_until = utcnow() + timedelta(hours=24)
        await session.commit()
    await app.state.worker.tick()
    database, scanner = await local(["sports"])
    for _ in range(2):
        await scanner.tick()
        async with database.sessions() as session:
            message = (await session.get(WhaleSettings, 1)).last_scan_error
        assert "体育：数据覆盖尚未完整，可继续同步查看" in message
        assert "过期" not in message and "sports" not in message


async def test_sync_monitor_records_requests_and_merges_status_success(environment):
    from backend.whale_requests import WhaleRequestMonitor

    app, _upstream, _http, local = environment
    await app.state.worker.tick()
    _database, scanner = await local(["sports"])
    scanner.request_monitor = WhaleRequestMonitor()
    for _ in range(3):
        await scanner.tick()
    records = await scanner.request_monitor.snapshot()
    statuses = [row for row in records if row.url.endswith("/status")]
    assert len(statuses) == 1 and statuses[0].request_count == 3
    snapshots = [row for row in records if row.url.endswith("/snapshot")]
    assert len(snapshots) == 1 and snapshots[0].query_params["categories"] == "sports"
    assert all(row.source == "collection" and row.http_status == 200 for row in records)
    assert all(row.duration_ms is not None for row in records)
    await app.state.worker.tick()
    await scanner.tick()
    assert any(row.url.endswith("/changes") for row in await scanner.request_monitor.snapshot())


@pytest.mark.parametrize("failure", ["timeout", "connect", "http", "json", "protocol", "watermark"])
async def test_sync_monitor_preserves_failure_after_recovery(environment, failure):
    from backend.whale_requests import WhaleRequestMonitor, capture_whale_requests

    _app, _upstream, _http, local = environment
    _database, scanner = await local(["sports"])
    monitor = WhaleRequestMonitor()

    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("sensitive response", request=request)
        if failure == "connect":
            raise httpx.ConnectError("sensitive response", request=request)
        if failure == "http":
            return httpx.Response(503, text="sensitive response")
        if failure == "json":
            return httpx.Response(200, text="sensitive response")
        return httpx.Response(200, json={"protocol_version": 2 if failure == "protocol" else 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        scanner.http = client
        with capture_whale_requests(monitor, "test"):
            with pytest.raises((httpx.HTTPError, ValueError, KeyError)):
                if failure == "watermark":
                    await scanner._pages(
                        "http://server",
                        {"source_id": "expected", "scope_version": 1, "latest_batch": 1},
                        "sports",
                        0,
                        True,
                    )
                else:
                    await scanner._request("http://server", "status")
    failed = (await monitor.snapshot())[0]
    assert failed.status == "failed" and failed.duration_ms is not None
    assert "sensitive" not in failed.error_message
    assert failed.response_excerpt is None
    assert failed.http_status == (
        None if failure in {"timeout", "connect"} else 503 if failure == "http" else 200
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"protocol_version": 1}))
    ) as client:
        scanner.http = client
        with capture_whale_requests(monitor, "test"):
            await scanner._request("http://server", "status")
    assert any(row.id == failed.id and row.status == "failed" for row in await monitor.snapshot())


async def test_manual_connection_check_is_monitored(monkeypatch):
    from backend.collection_api import check_connection
    from backend.schemas import CollectionConnectionUpdate
    from backend.whale_requests import WhaleRequestMonitor

    monitor = WhaleRequestMonitor()
    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        "backend.collection_api.httpx.AsyncClient",
        lambda **kwargs: client_type(
            **kwargs,
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"protocol_version": 1})
            ),
        ),
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(whale_request_monitor=monitor))
    )
    result = await check_connection(
        CollectionConnectionUpdate(host="127.0.0.1", port=8731), request
    )
    assert result["protocol_version"] == 1
    record = (await monitor.snapshot())[0]
    assert record.source == "collection" and record.status == "success"
    assert record.scan_id.startswith("connection-")
