from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from functools import partial

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.main import create_app
from backend.models import PositionChangeCandidate
from backend.monitor import utcnow
from backend.polymarket import PolymarketAPIError, SettlementEvidence, TradeSnapshot
from backend.tests.conftest import (
    TEST_ADDRESS,
    FakePolymarketClient,
    position,
)


def add_wallet(client):
    response = client.post(
        "/api/wallets",
        json={"address": TEST_ADDRESS},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def candidate_first_changed_at(database, wallet_id: int):
    async with database.sessions() as session:
        candidate = await session.scalar(
            select(PositionChangeCandidate).where(PositionChangeCandidate.wallet_id == wallet_id)
        )
        assert candidate is not None
        return candidate.first_changed_at


async def candidate_count(database, wallet_id: int) -> int:
    async with database.sessions() as session:
        return len(
            (
                await session.scalars(
                    select(PositionChangeCandidate).where(
                        PositionChangeCandidate.wallet_id == wallet_id
                    )
                )
            ).all()
        )


async def insert_phantom_candidate(database, wallet_id: int, item, changed_at) -> None:
    async with database.sessions() as session:
        session.add(
            PositionChangeCandidate(
                wallet_id=wallet_id,
                asset_id=item.asset_id,
                condition_id=item.condition_id,
                title=item.title,
                outcome=item.outcome,
                event_slug=item.event_slug,
                start_size=Decimal("427.438300000000002799"),
                latest_size=Decimal("427.4383"),
                before_avg_price=item.avg_price,
                after_avg_price=item.avg_price,
                latest_current_value=item.current_value,
                first_changed_at=changed_at,
                last_changed_at=changed_at,
                hard_deadline_at=changed_at + timedelta(seconds=180),
            )
        )
        await session.commit()


def test_initial_snapshot_is_sorted_and_does_not_create_events(app_client_factory):
    low = position(asset_id="low", current_value="2")
    high = position(asset_id="high", current_value="20")
    client, _ = app_client_factory([[low, high]])

    wallet = add_wallet(client)
    assert set(wallet) == {
        "id",
        "address",
        "proxy_wallet",
        "label",
        "enabled",
        "status",
        "last_success_at",
        "last_error",
        "created_at",
    }
    response = client.get("/api/positions", params={"wallet_id": wallet["id"]})
    assert response.status_code == 200
    body = response.json()
    assert [item["asset_id"] for item in body["items"]] == ["high", "low"]
    assert body["summary"] == {
        "current_value": 22.0,
        "initial_value": 8.0,
        "cash_pnl": 2.0,
        "count": 2,
    }
    assert body["stale"] is False
    events = client.get("/api/position-events", params={"wallet_id": wallet["id"]}).json()
    assert events == {"items": [], "next_cursor": None}


def test_price_only_update_is_silent(app_client_factory):
    initial = position(size="10", current_price="0.50")
    repriced = position(size="10", current_price="0.70")
    client, _ = app_client_factory([[initial], [repriced]])
    wallet = add_wallet(client)

    response = client.post(f"/api/wallets/{wallet['id']}/sync")
    assert response.status_code == 200
    events = client.get("/api/position-events", params={"wallet_id": wallet["id"]}).json()
    assert events["items"] == []
    positions = client.get("/api/positions", params={"wallet_id": wallet["id"]}).json()
    assert positions["items"][0]["current_price"] == 0.7


def test_purchase_dates_preserve_daily_buy_lots_and_fifo_reductions(
    app_client_factory,
):
    active = position(
        size="120",
        avg_price="0.458333333333333333",
        current_price="0.80",
    )
    client, fake = app_client_factory([[], [active]])
    wallet = add_wallet(client)
    fake.trades = [
        TradeSnapshot(
            asset_id=active.asset_id,
            condition_id=active.condition_id,
            side="BUY",
            size=Decimal("100"),
            price=Decimal("0.40"),
            timestamp=datetime(2026, 7, 27, 17, 0),
            transaction_hash="0xbuy-one",
        ),
        TradeSnapshot(
            asset_id=active.asset_id,
            condition_id=active.condition_id,
            side="BUY",
            size=Decimal("50"),
            price=Decimal("0.60"),
            timestamp=datetime(2026, 7, 28, 17, 0),
            transaction_hash="0xbuy-two",
        ),
        TradeSnapshot(
            asset_id=active.asset_id,
            condition_id=active.condition_id,
            side="SELL",
            size=Decimal("30"),
            price=Decimal("0.70"),
            timestamp=datetime(2026, 7, 29, 1, 0),
            transaction_hash="0xsell",
        ),
    ]

    assert client.post(f"/api/wallets/{wallet['id']}/sync").status_code == 200
    body = client.get("/api/positions", params={"wallet_id": wallet["id"]}).json()

    assert body["purchase_dates"] == ["2026-07-29", "2026-07-28"]
    assert body["purchase_history_complete"] is True
    assert body["purchase_history_error"] is None
    lots = body["items"][0]["purchase_lots"]
    assert [lot["purchase_date"] for lot in lots] == ["2026-07-29", "2026-07-28"]
    assert lots[0]["size"] == pytest.approx(50)
    assert lots[0]["avg_price"] == pytest.approx(0.6)
    assert lots[0]["initial_value"] == pytest.approx(30)
    assert lots[0]["current_value"] == pytest.approx(40)
    assert lots[0]["cash_pnl"] == pytest.approx(10)
    assert lots[0]["percent_pnl"] == pytest.approx(100 / 3)
    assert lots[1]["size"] == pytest.approx(70)
    assert lots[1]["avg_price"] == pytest.approx(0.4)
    assert lots[1]["initial_value"] == pytest.approx(28)
    assert lots[1]["current_value"] == pytest.approx(56)
    assert lots[1]["cash_pnl"] == pytest.approx(28)
    assert lots[1]["percent_pnl"] == pytest.approx(100)


def test_purchase_history_is_marked_incomplete_without_matching_trades(
    app_client_factory,
):
    client, _ = app_client_factory([[position(size="10")]])
    wallet = add_wallet(client)

    body = client.get("/api/positions", params={"wallet_id": wallet["id"]}).json()

    assert body["purchase_dates"] == []
    assert body["purchase_history_complete"] is False
    assert body["items"][0]["purchase_lots"] == []


def test_sqlite_numeric_round_trip_does_not_create_phantom_change(
    app_client_factory,
):
    stable = position(
        size="427.4383",
        avg_price="0.421",
        current_price="0.512",
    )
    client, _ = app_client_factory(
        [[stable], [stable], [stable]],
        quiet_window_seconds=45.0,
        hard_window_seconds=180.0,
    )
    wallet = add_wallet(client)
    portal = client.portal
    assert portal is not None
    database = client.app.state.database
    monitor = client.app.state.monitor

    assert client.post(f"/api/wallets/{wallet['id']}/sync").json()["status"] == "ok"
    assert client.post(f"/api/wallets/{wallet['id']}/sync").json()["status"] == "ok"
    assert portal.call(candidate_count, database, wallet["id"]) == 0
    assert (
        portal.call(
            partial(
                monitor.finalize_due_candidates,
                wallet["id"],
                now=utcnow() + timedelta(seconds=181),
            )
        )
        == 0
    )

    # Also defend against a phantom candidate already persisted by an older build.
    changed_at = utcnow()
    portal.call(
        insert_phantom_candidate,
        database,
        wallet["id"],
        stable,
        changed_at,
    )
    assert portal.call(candidate_count, database, wallet["id"]) == 1
    assert (
        portal.call(
            partial(
                monitor.finalize_due_candidates,
                wallet["id"],
                now=changed_at + timedelta(seconds=181),
            )
        )
        == 0
    )
    assert portal.call(candidate_count, database, wallet["id"]) == 0
    events = client.get("/api/position-events", params={"wallet_id": wallet["id"]}).json()
    assert events["items"] == []


def test_increase_creates_one_event_with_expandable_fills(app_client_factory):
    initial = position(size="10", avg_price="0.40")
    increased = position(size="15", avg_price="0.433333333333333333")
    client, fake = app_client_factory([[initial], [increased]])
    wallet = add_wallet(client)
    condition_id = initial.condition_id
    fake.trades = [
        TradeSnapshot(
            asset_id=initial.asset_id,
            condition_id=condition_id,
            side="BUY",
            size=Decimal("2"),
            price=Decimal("0.49"),
            timestamp=utcnow(),
            transaction_hash="0xabc",
        ),
        TradeSnapshot(
            asset_id=initial.asset_id,
            condition_id=condition_id,
            side="BUY",
            size=Decimal("3"),
            price=Decimal("0.51"),
            timestamp=utcnow(),
            transaction_hash="0xabc",
        ),
    ]

    assert client.post(f"/api/wallets/{wallet['id']}/sync").status_code == 200
    body = client.get("/api/position-events", params={"wallet_id": wallet["id"]}).json()
    assert len(body["items"]) == 1
    event = body["items"][0]
    assert event["type"] == "increased"
    assert event["before_size"] == 10.0
    assert event["after_size"] == 15.0
    assert event["delta_size"] == 5.0
    assert event["reconciliation_status"] == "matched"
    assert event["average_fill_price"] == 0.502
    assert len(event["fills"]) == 2
    assert event["fills"][0]["amount"] == 0.98


def test_new_position_after_baseline_creates_opened_event(app_client_factory):
    opened = position(asset_id="new", size="4", avg_price="0.25", current_price="0.30")
    client, fake = app_client_factory([[], [opened]])
    wallet = add_wallet(client)
    fake.trades = [
        TradeSnapshot(
            asset_id=opened.asset_id,
            condition_id=opened.condition_id,
            side="BUY",
            size=Decimal("4"),
            price=Decimal("0.25"),
            timestamp=utcnow(),
            transaction_hash="0xopen",
        )
    ]

    response = client.post(f"/api/wallets/{wallet['id']}/sync")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    events = client.get("/api/position-events", params={"wallet_id": wallet["id"]}).json()
    assert events["items"][0]["type"] == "opened"
    assert events["items"][0]["before_size"] == 0.0
    assert events["items"][0]["after_size"] == 4.0
    positions = client.get("/api/positions", params={"wallet_id": wallet["id"]}).json()
    assert len(positions["items"]) == 1
    assert positions["items"][0]["asset_id"] == "new"


def test_overlapping_windows_assign_each_fill_to_only_one_event(app_client_factory):
    initial = position(size="100", avg_price="0.40")
    first_increase = position(size="105", avg_price="0.404761904761904762")
    second_increase = position(size="110", avg_price="0.409090909090909091")
    client, fake = app_client_factory([[initial]], quiet_window_seconds=45.0)
    wallet = add_wallet(client)
    monitor = client.app.state.monitor
    portal = client.portal
    assert portal is not None
    first_detected = utcnow()
    first_fill = TradeSnapshot(
        asset_id=initial.asset_id,
        condition_id=initial.condition_id,
        side="BUY",
        size=Decimal("5"),
        price=Decimal("0.50"),
        timestamp=first_detected,
        transaction_hash="0x1",
    )
    second_detected = first_detected + timedelta(seconds=60)
    second_fill = TradeSnapshot(
        asset_id=initial.asset_id,
        condition_id=initial.condition_id,
        side="BUY",
        size=Decimal("5"),
        price=Decimal("0.60"),
        timestamp=second_detected,
        transaction_hash="0x2",
    )
    no_settlement = SettlementEvidence(frozenset(), frozenset(), frozenset())

    portal.call(
        partial(
            monitor._apply_snapshot,
            wallet["id"],
            [first_increase],
            evidence=no_settlement,
            observed_at=first_detected,
        )
    )
    fake.trades = [first_fill]
    assert (
        portal.call(
            partial(
                monitor.finalize_due_candidates,
                wallet["id"],
                now=first_detected + timedelta(seconds=46),
            )
        )
        == 1
    )

    portal.call(
        partial(
            monitor._apply_snapshot,
            wallet["id"],
            [second_increase],
            evidence=no_settlement,
            observed_at=second_detected,
        )
    )
    # The second 120-second lookback deliberately contains both fills.
    fake.trades = [first_fill, second_fill]
    assert (
        portal.call(
            partial(
                monitor.finalize_due_candidates,
                wallet["id"],
                now=second_detected + timedelta(seconds=46),
            )
        )
        == 1
    )

    events = client.get("/api/position-events", params={"wallet_id": wallet["id"]}).json()["items"]
    assert len(events) == 2
    assert events[0]["reconciliation_status"] == "matched"
    assert events[1]["reconciliation_status"] == "matched"
    assert [fill["transaction_hash"] for fill in events[0]["fills"]] == ["0x2"]
    assert [fill["transaction_hash"] for fill in events[1]["fills"]] == ["0x1"]


def test_pending_reduction_survives_market_settlement(app_client_factory):
    initial = position(size="100", avg_price="0.40")
    reduced = position(size="50", avg_price="0.40")
    client, fake = app_client_factory([[initial]], quiet_window_seconds=45.0)
    wallet = add_wallet(client)
    monitor = client.app.state.monitor
    portal = client.portal
    assert portal is not None
    detected_at = utcnow()
    no_settlement = SettlementEvidence(frozenset(), frozenset(), frozenset())
    resolved = SettlementEvidence(
        frozenset({initial.condition_id}),
        frozenset(),
        frozenset(),
    )
    fake.trades = [
        TradeSnapshot(
            asset_id=initial.asset_id,
            condition_id=initial.condition_id,
            side="SELL",
            size=Decimal("50"),
            price=Decimal("0.50"),
            timestamp=detected_at,
            transaction_hash="0xreduce",
        )
    ]

    portal.call(
        partial(
            monitor._apply_snapshot,
            wallet["id"],
            [reduced],
            evidence=no_settlement,
            observed_at=detected_at,
        )
    )
    # First complete miss is only a warning; the second is confirmed settlement.
    portal.call(
        partial(
            monitor._apply_snapshot,
            wallet["id"],
            [],
            evidence=no_settlement,
            observed_at=detected_at + timedelta(seconds=15),
        )
    )
    portal.call(
        partial(
            monitor._apply_snapshot,
            wallet["id"],
            [],
            evidence=resolved,
            observed_at=detected_at + timedelta(seconds=30),
        )
    )
    assert (
        portal.call(
            partial(
                monitor.finalize_due_candidates,
                wallet["id"],
                now=detected_at + timedelta(seconds=46),
            )
        )
        == 1
    )

    events = client.get("/api/position-events", params={"wallet_id": wallet["id"]}).json()["items"]
    assert len(events) == 1
    assert events[0]["type"] == "decreased"
    assert events[0]["before_size"] == 100.0
    assert events[0]["after_size"] == 50.0
    assert events[0]["delta_size"] == -50.0
    assert events[0]["reconciliation_status"] == "matched"
    assert [fill["transaction_hash"] for fill in events[0]["fills"]] == ["0xreduce"]
    positions = client.get("/api/positions", params={"wallet_id": wallet["id"]}).json()
    assert positions["summary"]["count"] == 0


def test_pending_candidate_survives_app_restart(settings_factory):
    initial = position(size="10", avg_price="0.40")
    increased = position(size="15", avg_price="0.433333333333333333")
    settings = settings_factory(
        quiet_window_seconds=45.0,
        hard_window_seconds=180.0,
    )
    fake = FakePolymarketClient([[initial], [increased]])
    first_app = create_app(settings=settings, client=fake)  # type: ignore[arg-type]

    with TestClient(first_app) as first_client:
        wallet = add_wallet(first_client)
        assert first_client.post(f"/api/wallets/{wallet['id']}/sync").status_code == 200
        assert (
            first_client.get(
                "/api/position-events",
                params={"wallet_id": wallet["id"]},
            ).json()["items"]
            == []
        )
        first_monitor = first_app.state.monitor
        portal = first_client.portal
        assert portal is not None
        first_changed_at = portal.call(
            candidate_first_changed_at,
            first_app.state.database,
            wallet["id"],
        )

    fake.trades = [
        TradeSnapshot(
            asset_id=initial.asset_id,
            condition_id=initial.condition_id,
            side="BUY",
            size=Decimal("5"),
            price=Decimal("0.50"),
            timestamp=first_changed_at,
            transaction_hash="0xrestart",
        )
    ]
    second_app = create_app(settings=settings, client=fake)  # type: ignore[arg-type]
    with TestClient(second_app) as second_client:
        second_monitor = second_app.state.monitor
        assert second_monitor is not first_monitor
        portal = second_client.portal
        assert portal is not None
        settle_at = first_changed_at + timedelta(seconds=46)
        assert (
            portal.call(
                partial(
                    second_monitor.finalize_due_candidates,
                    wallet["id"],
                    now=settle_at,
                )
            )
            == 1
        )
        # The candidate was deleted transactionally; repeating finalization is a no-op.
        assert (
            portal.call(
                partial(
                    second_monitor.finalize_due_candidates,
                    wallet["id"],
                    now=settle_at + timedelta(seconds=1),
                )
            )
            == 0
        )
        events = second_client.get(
            "/api/position-events",
            params={"wallet_id": wallet["id"]},
        ).json()["items"]
        assert len(events) == 1
        assert events[0]["type"] == "increased"
        assert events[0]["reconciliation_status"] == "matched"
        assert [fill["transaction_hash"] for fill in events[0]["fills"]] == ["0xrestart"]


def test_trade_failure_waits_for_hard_deadline(app_client_factory):
    initial = position(size="10", avg_price="0.40")
    increased = position(size="15", avg_price="0.433333333333333333")
    client, fake = app_client_factory(
        [[initial]],
        quiet_window_seconds=45.0,
        hard_window_seconds=180.0,
    )
    wallet = add_wallet(client)
    monitor = client.app.state.monitor
    portal = client.portal
    assert portal is not None
    detected_at = utcnow()
    portal.call(
        partial(
            monitor._apply_snapshot,
            wallet["id"],
            [increased],
            evidence=SettlementEvidence(frozenset(), frozenset(), frozenset()),
            observed_at=detected_at,
        )
    )
    fake.trade_error = PolymarketAPIError("trades unavailable")

    assert (
        portal.call(
            partial(
                monitor.finalize_due_candidates,
                wallet["id"],
                now=detected_at + timedelta(seconds=46),
            )
        )
        == 0
    )
    assert (
        client.get(
            "/api/position-events",
            params={"wallet_id": wallet["id"]},
        ).json()["items"]
        == []
    )
    assert (
        portal.call(
            partial(
                monitor.finalize_due_candidates,
                wallet["id"],
                now=detected_at + timedelta(seconds=181),
            )
        )
        == 1
    )
    events = client.get("/api/position-events", params={"wallet_id": wallet["id"]}).json()["items"]
    assert len(events) == 1
    assert events[0]["reconciliation_status"] == "unavailable"
    assert events[0]["fills"] == []


def test_position_must_be_missing_twice_before_close(app_client_factory):
    initial = position(size="10")
    client, fake = app_client_factory([[initial], [], []])
    wallet = add_wallet(client)
    fake.trades = [
        TradeSnapshot(
            asset_id=initial.asset_id,
            condition_id=initial.condition_id,
            side="SELL",
            size=Decimal("10"),
            price=Decimal("0.50"),
            timestamp=utcnow(),
            transaction_hash="0xsell",
        )
    ]

    client.post(f"/api/wallets/{wallet['id']}/sync")
    after_one = client.get("/api/positions", params={"wallet_id": wallet["id"]}).json()
    assert after_one["summary"]["count"] == 1
    assert fake.settlement_calls == []

    client.post(f"/api/wallets/{wallet['id']}/sync")
    after_two = client.get("/api/positions", params={"wallet_id": wallet["id"]}).json()
    assert after_two["summary"]["count"] == 0
    assert fake.settlement_calls == [[initial.condition_id]]
    event = client.get("/api/position-events", params={"wallet_id": wallet["id"]}).json()["items"][
        0
    ]
    assert event["type"] == "closed"
    assert event["reconciliation_status"] == "matched"


def test_resolved_market_disappearance_does_not_create_close_event(
    app_client_factory,
):
    initial = position(size="10")
    client, fake = app_client_factory([[initial], [], []])
    fake.evidence = SettlementEvidence(
        resolved_condition_ids=frozenset({initial.condition_id}),
        redeemable_asset_ids=frozenset(),
        non_trade_condition_ids=frozenset(),
    )
    wallet = add_wallet(client)

    client.post(f"/api/wallets/{wallet['id']}/sync")
    client.post(f"/api/wallets/{wallet['id']}/sync")
    events = client.get("/api/position-events", params={"wallet_id": wallet["id"]}).json()
    assert events["items"] == []
    positions = client.get("/api/positions", params={"wallet_id": wallet["id"]}).json()
    assert positions["summary"]["count"] == 0


def test_remote_failure_keeps_last_positions_and_marks_stale(app_client_factory):
    initial = position(size="10")
    client, _ = app_client_factory([[initial], PolymarketAPIError("temporary upstream failure")])
    wallet = add_wallet(client)

    result = client.post(f"/api/wallets/{wallet['id']}/sync")
    assert result.status_code == 200
    assert result.json()["status"] == "error"
    assert "temporary upstream failure" in result.json()["last_error"]
    positions = client.get("/api/positions", params={"wallet_id": wallet["id"]}).json()
    assert positions["summary"]["count"] == 1
    assert positions["stale"] is True


def test_disable_preserves_data_but_blocks_manual_sync(app_client_factory):
    client, _ = app_client_factory([[position()]])
    wallet = add_wallet(client)
    response = client.delete(f"/api/wallets/{wallet['id']}")
    assert response.status_code == 204
    assert client.post(f"/api/wallets/{wallet['id']}/sync").status_code == 409
    assert (
        client.get("/api/positions", params={"wallet_id": wallet["id"]}).json()["summary"]["count"]
        == 1
    )


def test_profile_url_is_accepted(app_client_factory):
    client, _ = app_client_factory([[]])
    response = client.post(
        "/api/wallets",
        json={"address": f"https://polymarket.com/profile/{TEST_ADDRESS}", "label": " 跟单 "},
    )
    assert response.status_code == 201
    assert response.json()["label"] == "跟单"
