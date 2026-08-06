from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from functools import partial

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.main import create_app
from backend.models import PositionChangeCandidate, PositionOverlapPeriod
from backend.monitor import utcnow
from backend.polymarket import (
    PolymarketAPIError,
    RedemptionSnapshot,
    SettlementEvidence,
    TradeSnapshot,
)
from backend.tests.conftest import (
    TEST_ADDRESS,
    FakePolymarketClient,
    position,
)

MY_ADDRESS = "0x1111111111111111111111111111111111111111"
OTHER_ADDRESS = "0x2222222222222222222222222222222222222222"


def add_wallet(client):
    response = client.post(
        "/api/wallets",
        json={"address": TEST_ADDRESS},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_global_copy_ratio_defaults_validates_and_persists(settings_factory):
    settings = settings_factory()
    fake = FakePolymarketClient([[]])

    with TestClient(create_app(settings=settings, client=fake)) as client:  # type: ignore[arg-type]
        assert client.get("/api/settings").json() == {"copy_ratio_percent": 10.0}
        assert client.put(
            "/api/settings",
            json={"copy_ratio_percent": 12.34},
        ).json() == {"copy_ratio_percent": 12.34}
        assert (
            client.put(
                "/api/settings",
                json={"copy_ratio_percent": 1},
            ).status_code
            == 200
        )
        assert (
            client.put(
                "/api/settings",
                json={"copy_ratio_percent": 100},
            ).status_code
            == 200
        )
        assert (
            client.put(
                "/api/settings",
                json={"copy_ratio_percent": 0.99},
            ).status_code
            == 422
        )
        assert (
            client.put(
                "/api/settings",
                json={"copy_ratio_percent": 100.01},
            ).status_code
            == 422
        )
        assert (
            client.put(
                "/api/settings",
                json={"copy_ratio_percent": 12.345},
            ).status_code
            == 422
        )
        assert (
            client.put(
                "/api/settings",
                json={"copy_ratio_percent": 37.5},
            ).status_code
            == 200
        )

    with TestClient(create_app(settings=settings, client=fake)) as restarted:  # type: ignore[arg-type]
        assert restarted.get("/api/settings").json() == {"copy_ratio_percent": 37.5}


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


async def overlap_period_windows(
    database,
    my_wallet_id: int,
    tracked_wallet_id: int,
    asset_id: str,
):
    async with database.sessions() as session:
        periods = list(
            (
                await session.scalars(
                    select(PositionOverlapPeriod)
                    .where(
                        PositionOverlapPeriod.my_wallet_id == my_wallet_id,
                        PositionOverlapPeriod.tracked_wallet_id == tracked_wallet_id,
                        PositionOverlapPeriod.asset_id == asset_id,
                    )
                    .order_by(PositionOverlapPeriod.id.asc())
                )
            ).all()
        )
        return [(period.started_at, period.ended_at) for period in periods]


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


def redemption(
    *,
    condition_id: str,
    title: str,
    timestamp: datetime,
    size: str,
    transaction_hash: str,
) -> RedemptionSnapshot:
    return RedemptionSnapshot(
        asset_id="",
        condition_id=condition_id,
        title=title,
        outcome="Yes",
        outcome_index=0,
        event_slug=f"event-{condition_id[-4:]}",
        market_slug=f"market-{condition_id[-4:]}",
        size=Decimal(size),
        usdc_size=Decimal(size),
        timestamp=timestamp,
        transaction_hash=transaction_hash,
    )


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
        "wallet_role",
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


@pytest.mark.parametrize(
    ("my_size", "tracked_size", "expected_percent", "expected_my_ratio", "expected_tracked_ratio"),
    [
        ("10", "100", 10, 1, 10),
        ("200", "100", 200, 2, 1),
        ("100", "100", 100, 1, 1),
        ("0.00001", "100", 0.00001, 1, 10_000_000),
    ],
)
def test_position_overlaps_calculate_share_ratios(
    app_client_factory,
    my_size,
    tracked_size,
    expected_percent,
    expected_my_ratio,
    expected_tracked_ratio,
):
    tracked_position = position(size=tracked_size)
    my_position = position(size=my_size)
    client, _ = app_client_factory([[tracked_position], [my_position]])

    tracked_wallet = add_wallet(client)
    my_wallet_response = client.put(
        "/api/my-wallet",
        json={"address": MY_ADDRESS, "label": "我的主钱包"},
    )
    assert my_wallet_response.status_code == 200
    my_wallet = my_wallet_response.json()
    assert my_wallet["wallet_role"] == "self"

    response = client.get(
        "/api/position-overlaps",
        params={"tracked_wallet_id": tracked_wallet["id"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["overlap_count"] == 1
    assert body["my_wallet_id"] == my_wallet["id"]
    overlap = body["items"][0]
    assert overlap["my_to_tracked_percent"] == pytest.approx(expected_percent)
    assert overlap["my_ratio"] == pytest.approx(expected_my_ratio)
    assert overlap["tracked_ratio"] == pytest.approx(expected_tracked_ratio)

    detail_response = client.get(
        f"/api/position-overlaps/{tracked_position.asset_id}",
        params={"tracked_wallet_id": tracked_wallet["id"]},
    )
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["mine"]["size"] == pytest.approx(float(my_size))
    assert detail["tracked"]["size"] == pytest.approx(float(tracked_size))
    assert detail["my_wallet"]["label"] == "我的主钱包"


def test_overlap_requires_the_same_asset_not_only_the_same_market(
    app_client_factory,
):
    tracked_position = position(
        asset_id="yes-asset",
        condition_id="0x" + "a" * 64,
        outcome="Yes",
    )
    opposite_position = position(
        asset_id="no-asset",
        condition_id=tracked_position.condition_id,
        outcome="No",
    )
    client, _ = app_client_factory([[tracked_position], [opposite_position]])
    tracked_wallet = add_wallet(client)
    assert client.put("/api/my-wallet", json={"address": MY_ADDRESS}).status_code == 200

    body = client.get(
        "/api/position-overlaps",
        params={"tracked_wallet_id": tracked_wallet["id"]},
    ).json()
    assert body["overlap_count"] == 0
    assert body["items"] == []


def test_setting_and_replacing_my_wallet_preserves_old_wallet_data(
    app_client_factory,
):
    client, _ = app_client_factory([[position()], [], []])
    assert client.get("/api/my-wallet").json() is None
    tracked_wallet = add_wallet(client)

    promoted = client.put(
        "/api/my-wallet",
        json={"address": TEST_ADDRESS},
    )
    assert promoted.status_code == 200
    assert promoted.json()["id"] == tracked_wallet["id"]
    assert promoted.json()["wallet_role"] == "self"
    assert (
        client.post(
            "/api/wallets",
            json={"address": TEST_ADDRESS},
        ).status_code
        == 409
    )

    replacement = client.put(
        "/api/my-wallet",
        json={"address": OTHER_ADDRESS, "label": "新的我的钱包"},
    )
    assert replacement.status_code == 200
    assert replacement.json()["wallet_role"] == "self"
    assert replacement.json()["label"] == "新的我的钱包"
    assert client.get("/api/my-wallet").json()["id"] == replacement.json()["id"]

    wallets = client.get("/api/wallets").json()
    old_wallet = next(item for item in wallets if item["id"] == tracked_wallet["id"])
    assert old_wallet["wallet_role"] == "tracked"
    assert old_wallet["enabled"] is False
    assert (
        client.get(
            "/api/positions",
            params={"wallet_id": old_wallet["id"]},
        ).json()["summary"]["count"]
        == 1
    )
    assert sum(item["wallet_role"] == "self" for item in wallets) == 1


def test_common_position_reduction_creates_readable_alert(
    app_client_factory,
):
    initial = position(size="100")
    reduced = position(size="60")
    mine = position(size="10")
    client, _ = app_client_factory([[initial], [mine], [reduced]])
    tracked_wallet = add_wallet(client)
    assert client.put("/api/my-wallet", json={"address": MY_ADDRESS}).status_code == 200

    assert client.post(f"/api/wallets/{tracked_wallet['id']}/sync").status_code == 200
    response = client.get(
        "/api/overlap-alerts",
        params={"tracked_wallet_id": tracked_wallet["id"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["unread_count"] == 1
    assert len(body["items"]) == 1
    alert = body["items"][0]
    assert alert["type"] == "decreased"
    assert alert["before_size"] == 100
    assert alert["after_size"] == 60
    assert alert["delta_size"] == -40
    assert alert["read_at"] is None
    assert alert["copy_recommendation"] == {
        "action": "sell",
        "ratio_percent": 10.0,
        "shares": 4.0,
        "estimated_usdc": None,
    }

    marked = client.post(f"/api/overlap-alerts/{alert['id']}/read")
    assert marked.status_code == 200
    assert marked.json()["read_at"] is not None
    refreshed = client.get(
        "/api/overlap-alerts",
        params={"tracked_wallet_id": tracked_wallet["id"]},
    ).json()
    assert refreshed["unread_count"] == 0
    assert refreshed["items"][0]["read_at"] is not None

    assert client.put("/api/my-wallet", json={"address": OTHER_ADDRESS}).status_code == 200
    replacement_view = client.get(
        "/api/overlap-alerts",
        params={"tracked_wallet_id": tracked_wallet["id"]},
    ).json()
    assert replacement_view == {"items": [], "unread_count": 0}


def test_common_position_increase_creates_alert(
    app_client_factory,
):
    initial = position(size="100")
    increased = position(size="140")
    mine = position(size="10")
    client, _ = app_client_factory([[initial], [mine], [increased]])
    tracked_wallet = add_wallet(client)
    assert client.put("/api/my-wallet", json={"address": MY_ADDRESS}).status_code == 200

    assert client.post(f"/api/wallets/{tracked_wallet['id']}/sync").status_code == 200
    body = client.get(
        "/api/overlap-alerts",
        params={"tracked_wallet_id": tracked_wallet["id"]},
    ).json()

    assert body["unread_count"] == 1
    assert len(body["items"]) == 1
    alert = body["items"][0]
    assert alert["type"] == "increased"
    assert alert["before_size"] == 100
    assert alert["after_size"] == 140
    assert alert["delta_size"] == 40


def test_common_position_reopening_creates_a_new_period(
    app_client_factory,
):
    tracked = position(size="100")
    mine = position(size="10")
    client, _ = app_client_factory([[tracked], [mine], [], [], [mine]])
    tracked_wallet = add_wallet(client)
    my_wallet = client.put("/api/my-wallet", json={"address": MY_ADDRESS}).json()

    client.post(f"/api/wallets/{my_wallet['id']}/sync")
    client.post(f"/api/wallets/{my_wallet['id']}/sync")
    client.post(f"/api/wallets/{my_wallet['id']}/sync")

    portal = client.portal
    assert portal is not None
    windows = portal.call(
        overlap_period_windows,
        client.app.state.database,
        my_wallet["id"],
        tracked_wallet["id"],
        tracked.asset_id,
    )
    assert len(windows) == 2
    assert windows[0][1] is not None
    assert windows[1][1] is None


def test_common_position_close_creates_alert(app_client_factory):
    initial = position(size="100")
    mine = position(size="10")
    client, _ = app_client_factory([[initial], [mine], [], []])
    tracked_wallet = add_wallet(client)
    assert client.put("/api/my-wallet", json={"address": MY_ADDRESS}).status_code == 200

    client.post(f"/api/wallets/{tracked_wallet['id']}/sync")
    client.post(f"/api/wallets/{tracked_wallet['id']}/sync")
    alerts = client.get(
        "/api/overlap-alerts",
        params={"tracked_wallet_id": tracked_wallet["id"]},
    ).json()
    assert alerts["unread_count"] == 1
    assert alerts["items"][0]["type"] == "closed"
    assert alerts["items"][0]["before_size"] == 100
    assert alerts["items"][0]["after_size"] == 0
    assert alerts["items"][0]["copy_recommendation"]["action"] == "sell"
    assert alerts["items"][0]["copy_recommendation"]["shares"] == 10


def test_settlement_does_not_create_common_position_alert(
    app_client_factory,
):
    initial = position(size="100")
    mine = position(size="10")
    client, fake = app_client_factory([[initial], [mine], [], []])
    fake.evidence = SettlementEvidence(
        resolved_condition_ids=frozenset({initial.condition_id}),
        redeemable_asset_ids=frozenset(),
        non_trade_condition_ids=frozenset(),
    )
    tracked_wallet = add_wallet(client)
    assert client.put("/api/my-wallet", json={"address": MY_ADDRESS}).status_code == 200
    client.post(f"/api/wallets/{tracked_wallet['id']}/sync")
    client.post(f"/api/wallets/{tracked_wallet['id']}/sync")
    alerts = client.get(
        "/api/overlap-alerts",
        params={"tracked_wallet_id": tracked_wallet["id"]},
    ).json()
    assert alerts == {"items": [], "unread_count": 0}


def test_no_alert_when_my_position_ends_before_tracked_reduction(
    app_client_factory,
):
    initial = position(size="100")
    mine = position(size="10")
    reduced = position(size="60")
    client, _ = app_client_factory([[initial], [mine], [], [], [reduced]])
    tracked_wallet = add_wallet(client)
    my_wallet = client.put("/api/my-wallet", json={"address": MY_ADDRESS}).json()

    client.post(f"/api/wallets/{my_wallet['id']}/sync")
    client.post(f"/api/wallets/{my_wallet['id']}/sync")
    client.post(f"/api/wallets/{tracked_wallet['id']}/sync")

    alerts = client.get(
        "/api/overlap-alerts",
        params={"tracked_wallet_id": tracked_wallet["id"]},
    ).json()
    assert alerts == {"items": [], "unread_count": 0}


def test_alert_survives_when_my_position_ends_after_change_detection(
    app_client_factory,
):
    initial = position(size="100")
    mine = position(size="10")
    reduced = position(size="60")
    client, _ = app_client_factory(
        [[initial], [mine], [reduced], [], []],
        quiet_window_seconds=45.0,
    )
    tracked_wallet = add_wallet(client)
    my_wallet = client.put("/api/my-wallet", json={"address": MY_ADDRESS}).json()
    detected_at = utcnow()

    client.post(f"/api/wallets/{tracked_wallet['id']}/sync")
    client.post(f"/api/wallets/{my_wallet['id']}/sync")
    client.post(f"/api/wallets/{my_wallet['id']}/sync")

    portal = client.portal
    assert portal is not None
    assert (
        portal.call(
            partial(
                client.app.state.monitor.finalize_due_candidates,
                tracked_wallet["id"],
                now=detected_at + timedelta(seconds=46),
            )
        )
        == 1
    )
    alerts = client.get(
        "/api/overlap-alerts",
        params={"tracked_wallet_id": tracked_wallet["id"]},
    ).json()
    assert alerts["unread_count"] == 1
    assert alerts["items"][0]["type"] == "decreased"


def test_alert_limit_and_mark_all_read(app_client_factory):
    initial = position(size="100")
    mine = position(size="10")
    first_reduction = position(size="80")
    second_reduction = position(size="60")
    client, _ = app_client_factory([[initial], [mine], [first_reduction], [second_reduction]])
    tracked_wallet = add_wallet(client)
    assert client.put("/api/my-wallet", json={"address": MY_ADDRESS}).status_code == 200
    client.post(f"/api/wallets/{tracked_wallet['id']}/sync")
    client.post(f"/api/wallets/{tracked_wallet['id']}/sync")

    limited = client.get(
        "/api/overlap-alerts",
        params={"tracked_wallet_id": tracked_wallet["id"], "limit": 1},
    ).json()
    assert len(limited["items"]) == 1
    assert limited["unread_count"] == 2

    response = client.post(
        "/api/overlap-alerts/read-all",
        params={"tracked_wallet_id": tracked_wallet["id"]},
    )
    assert response.status_code == 204
    refreshed = client.get(
        "/api/overlap-alerts",
        params={"tracked_wallet_id": tracked_wallet["id"]},
    ).json()
    assert refreshed["unread_count"] == 0
    assert all(item["read_at"] is not None for item in refreshed["items"])


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
    assert body["opened_dates"] == ["2026-07-28"]
    assert body["purchase_history_complete"] is True
    assert body["purchase_history_error"] is None
    assert body["items"][0]["first_opened_at"] == "2026-07-27T17:00:00Z"
    assert body["items"][0]["first_opened_at_source"] == "trade"
    assert body["items"][0]["opened_date"] == "2026-07-28"
    assert body["items"][0]["cycle_history_complete"] is True
    assert [trade["type"] for trade in body["items"][0]["cycle_trades"]] == [
        "opened",
        "increased",
        "decreased",
    ]
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
    assert len(body["opened_dates"]) == 1
    assert body["purchase_history_complete"] is False
    assert body["items"][0]["first_opened_at"] is not None
    assert body["items"][0]["first_opened_at_source"] == "first_seen"
    assert body["items"][0]["cycle_history_complete"] is False
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


def test_redemptions_backfill_from_wallet_creation_and_use_time_cursor(
    app_client_factory,
):
    client, fake = app_client_factory([[]])
    now = utcnow()
    fake.redemptions = [
        redemption(
            condition_id="0x" + "a" * 64,
            title="添加钱包之前的赎回",
            timestamp=now - timedelta(days=1),
            size="12",
            transaction_hash="0xoldredeem",
        ),
        redemption(
            condition_id="0x" + "b" * 64,
            title="较早赎回",
            timestamp=now + timedelta(seconds=1),
            size="25.5",
            transaction_hash="0xredeem1",
        ),
        redemption(
            condition_id="0x" + "c" * 64,
            title="较新赎回",
            timestamp=now + timedelta(seconds=2),
            size="40",
            transaction_hash="0xredeem2",
        ),
    ]

    wallet = add_wallet(client)
    first_page = client.get(
        "/api/position-events",
        params={"wallet_id": wallet["id"], "limit": 1},
    ).json()
    assert [event["title"] for event in first_page["items"]] == ["较新赎回"]
    assert isinstance(first_page["next_cursor"], str)
    assert "|" in first_page["next_cursor"]

    event = first_page["items"][0]
    assert event["type"] == "redeemed"
    assert event["before_size"] == 40
    assert event["after_size"] == 0
    assert event["delta_size"] == -40
    assert event["payout_amount"] == 40
    assert event["redemption_price"] == 1
    assert event["redemption_cost_complete"] is False
    assert event["redemption_entry_price"] is None
    assert event["redemption_profit"] is None
    assert event["transaction_hash"] == "0xredeem2"
    assert event["reconciliation_status"] == "onchain"
    assert event["fills"] == []

    second_page = client.get(
        "/api/position-events",
        params={
            "wallet_id": wallet["id"],
            "limit": 1,
            "cursor": first_page["next_cursor"],
        },
    ).json()
    assert [event["title"] for event in second_page["items"]] == ["较早赎回"]
    assert second_page["next_cursor"] is None

    assert client.post(f"/api/wallets/{wallet['id']}/sync").status_code == 200
    all_events = client.get(
        "/api/position-events",
        params={"wallet_id": wallet["id"]},
    ).json()["items"]
    assert [event["title"] for event in all_events] == ["较新赎回", "较早赎回"]


def test_redemption_uses_fifo_cost_for_entry_price_and_profit(
    app_client_factory,
):
    condition_id = "0x" + "d" * 64
    active = position(
        asset_id="redemption-asset",
        condition_id=condition_id,
        size="17",
        avg_price="0.258823529411764706",
    )
    client, fake = app_client_factory([[active]])
    now = utcnow()
    fake.trades = [
        TradeSnapshot(
            asset_id=active.asset_id,
            condition_id=condition_id,
            side="BUY",
            size=Decimal("20"),
            price=Decimal("0.2"),
            timestamp=now - timedelta(seconds=10),
            transaction_hash="0xbuy1",
        ),
        TradeSnapshot(
            asset_id=active.asset_id,
            condition_id=condition_id,
            side="SELL",
            size=Decimal("8"),
            price=Decimal("0.25"),
            timestamp=now - timedelta(seconds=9),
            transaction_hash="0xsell1",
        ),
        TradeSnapshot(
            asset_id=active.asset_id,
            condition_id=condition_id,
            side="BUY",
            size=Decimal("5"),
            price=Decimal("0.4"),
            timestamp=now - timedelta(seconds=8),
            transaction_hash="0xbuy2",
        ),
    ]
    fake.redemptions = [
        redemption(
            condition_id=condition_id,
            title="FIFO 赎回市场",
            timestamp=now + timedelta(seconds=1),
            size="17",
            transaction_hash="0xfiforedeem",
        )
    ]

    wallet = add_wallet(client)
    event = client.get(
        "/api/position-events",
        params={"wallet_id": wallet["id"]},
    ).json()["items"][0]

    assert event["asset_id"] == active.asset_id
    assert event["redemption_cost_complete"] is True
    assert event["redemption_cost_basis"] == 4.4
    assert event["redemption_entry_price"] == pytest.approx(4.4 / 17)
    assert event["redemption_price"] == 1
    assert event["redemption_profit"] == 12.6
    assert event["redemption_profit_percent"] == pytest.approx(12.6 / 4.4 * 100)
    assert event["copy_recommendation"] is None


def test_redemption_failure_does_not_interrupt_wallet_sync(app_client_factory):
    client, fake = app_client_factory([[]])
    wallet = add_wallet(client)
    fake.redemption_error = PolymarketAPIError("redemptions unavailable")

    response = client.post(f"/api/wallets/{wallet['id']}/sync")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["last_error"] is None
    events = client.get(
        "/api/position-events",
        params={"wallet_id": wallet["id"]},
    ).json()
    assert events["items"] == []


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
    assert events["items"][0]["copy_recommendation"] == {
        "action": "buy",
        "ratio_percent": 10.0,
        "shares": 0.4,
        "estimated_usdc": 0.1,
    }
    assert (
        client.put(
            "/api/settings",
            json={"copy_ratio_percent": 25},
        ).status_code
        == 200
    )
    recalculated = client.get(
        "/api/position-events",
        params={"wallet_id": wallet["id"]},
    ).json()["items"][0]
    assert recalculated["copy_recommendation"] == {
        "action": "buy",
        "ratio_percent": 25.0,
        "shares": 1.0,
        "estimated_usdc": 0.25,
    }
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
    assert events[0]["copy_recommendation"] == {
        "action": "sell",
        "ratio_percent": 10.0,
        "shares": 5.0,
        "estimated_usdc": 2.5,
    }
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


def test_copy_workspace_aggregates_multiple_strategies(app_client_factory):
    client, _ = app_client_factory([[], []])
    first = add_wallet(client)
    second_response = client.post(
        "/api/wallets",
        json={"address": OTHER_ADDRESS, "label": "第二策略"},
    )
    assert second_response.status_code == 201
    second = second_response.json()
    for wallet, ratio in [(first, 10), (second, 25)]:
        response = client.post(
            "/api/copy-trading/subscriptions",
            json={
                "tracked_wallet_id": wallet["id"],
                "copy_ratio_percent": ratio,
                "position_cap_usdc": 20,
                "total_exposure_cap_usdc": 80,
                "market_slippage_cents": 5,
            },
        )
        assert response.status_code == 201, response.text

    overview = client.get("/api/copy-trading/overview")
    assert overview.status_code == 200, overview.text
    payload = overview.json()
    assert [item["wallet"]["id"] for item in payload["strategies"]] == [
        first["id"],
        second["id"],
    ]
    assert [item["subscription"]["copy_ratio_percent"] for item in payload["strategies"]] == [
        10.0,
        25.0,
    ]
    assert payload["totals"]["open_exposure_usdc"] == 0.0
    assert payload["recent_orders"] == []

    positions = client.get("/api/copy-trading/positions")
    assert positions.status_code == 200
    assert positions.json()["items"] == []

    orders = client.get(
        "/api/copy-trading/orders",
        params={"tracked_wallet_id": second["id"], "status_group": "skipped"},
    )
    assert orders.status_code == 200
    assert orders.json() == {"items": [], "next_cursor": None}


def test_copy_workspace_rejects_invalid_filters(app_client_factory):
    client, _ = app_client_factory([[]])
    assert client.get("/api/copy-trading/positions", params={"scope": "unknown"}).status_code == 422
    assert client.get("/api/copy-trading/orders", params={"side": "HOLD"}).status_code == 422
    assert (
        client.get("/api/copy-trading/orders", params={"status_group": "unknown"}).status_code
        == 422
    )
