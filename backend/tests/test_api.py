from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.main import create_app
from backend.models import (
    CopyFill,
    CopyLedger,
    CopyOrder,
    CopyPosition,
    PositionChangeCandidate,
    PositionEvent,
    PositionEventFill,
    PositionOverlapPeriod,
)
from backend.monitor import utcnow
from backend.polymarket import (
    ClosedPositionSnapshot,
    MarketResolution,
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


async def insert_daily_pnl_fixture(database, subscription_ids: list[int], local_day) -> None:
    shanghai = ZoneInfo("Asia/Shanghai")

    def utc_timestamp(day, hour: int) -> datetime:
        local_timestamp = datetime.combine(day, datetime.min.time(), tzinfo=shanghai).replace(
            hour=hour, minute=30
        )
        return local_timestamp.astimezone(UTC).replace(tzinfo=None)

    async with database.sessions() as session:
        rows = [
            (subscription_ids[0], "sell", Decimal("0"), Decimal("2"), local_day, 0),
            (subscription_ids[0], "redeem", Decimal("0"), Decimal("3"), local_day, 23),
            (subscription_ids[1], "reconcile_redeem", Decimal("0"), Decimal("-1"), local_day, 12),
            (subscription_ids[1], "buy", Decimal("12"), Decimal("0"), local_day, 13),
            (
                subscription_ids[1],
                "sell",
                Decimal("0"),
                Decimal("-2"),
                local_day - timedelta(days=2),
                12,
            ),
            (
                subscription_ids[0],
                "buy",
                Decimal("8"),
                Decimal("0"),
                local_day - timedelta(days=2),
                10,
            ),
            (
                subscription_ids[0],
                "redeem",
                Decimal("0"),
                Decimal("1"),
                local_day - timedelta(days=40),
                12,
            ),
        ]
        for subscription_id, entry_type, amount, pnl, day, hour in rows:
            session.add(
                CopyLedger(
                    subscription_id=subscription_id,
                    copy_position_id=None,
                    order_id=None,
                    type=entry_type,
                    amount_usdc=amount,
                    realized_pnl=pnl,
                    detail="每日盈亏测试",
                    timestamp=utc_timestamp(day, hour),
                )
            )
        await session.commit()


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


async def set_event_reconciliation_status(database, event_id: int, status: str) -> None:
    async with database.sessions() as session:
        event = await session.get(PositionEvent, event_id)
        assert event is not None
        event.reconciliation_status = status
        await session.commit()


async def add_copy_position(database, subscription_id: int) -> None:
    now = utcnow()
    async with database.sessions() as session:
        session.add(
            CopyPosition(
                subscription_id=subscription_id,
                asset_id="copy-asset-1",
                condition_id="0x" + "c" * 64,
                title="跟单测试市场",
                outcome="Yes",
                outcome_index=0,
                neg_risk=False,
                event_slug="copy-test-market",
                settlement_date=None,
                cycle_no=1,
                attributed_size=Decimal("10"),
                attributed_cost=Decimal("4"),
                reserved_buy_usdc=Decimal("0"),
                realized_pnl=Decimal("0"),
                status="open",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()


async def insert_copy_buy_fill_fixture(
    database,
    purchases: list[tuple[int, str, int, list[Decimal]]],
) -> None:
    now = utcnow()
    async with database.sessions() as session:
        for fixture_index, (subscription_id, asset_id, cycle_no, amounts) in enumerate(purchases):
            position = CopyPosition(
                subscription_id=subscription_id,
                asset_id=asset_id,
                condition_id="0x" + f"{fixture_index + 1:064x}",
                title=f"累计投入测试市场 {fixture_index + 1}",
                outcome="Yes",
                outcome_index=0,
                neg_risk=False,
                event_slug=f"lifetime-bought-{fixture_index + 1}",
                settlement_date=None,
                cycle_no=cycle_no,
                attributed_size=Decimal("0"),
                attributed_cost=Decimal("0"),
                reserved_buy_usdc=Decimal("0"),
                realized_pnl=Decimal("0"),
                status="dust_closed",
                created_at=now,
                updated_at=now,
            )
            session.add(position)
            await session.flush()
            total_amount = sum(amounts, start=Decimal("0"))
            total_size = total_amount * Decimal("2")
            order = CopyOrder(
                subscription_id=subscription_id,
                copy_position_id=position.id,
                leader_event_id=None,
                idempotency_key=f"lifetime-bought-{subscription_id}-{asset_id}-{cycle_no}",
                source="copy",
                signed_order_hash=None,
                asset_id=asset_id,
                condition_id=position.condition_id,
                side="BUY",
                requested_size=total_size + Decimal("1"),
                requested_usdc=total_amount + Decimal("1"),
                leader_purchase_usdc=total_amount * Decimal("10"),
                proportional_target_usdc=total_amount + Decimal("1"),
                limit_price=Decimal("0.55"),
                reference_price=Decimal("0.50"),
                filled_size=total_size,
                filled_usdc=total_amount,
                fee_usdc=Decimal("9.99"),
                status="partially_filled",
                reason="累计投入测试部分成交",
                external_order_id=None,
                external_trade_id=None,
                created_at=now,
                updated_at=now,
            )
            session.add(order)
            await session.flush()
            for fill_index, amount in enumerate(amounts):
                session.add(
                    CopyFill(
                        order_id=order.id,
                        fingerprint=f"lifetime-bought-{order.id}-{fill_index}",
                        external_trade_id=f"trade-{order.id}-{fill_index}",
                        size=amount * Decimal("2"),
                        price=Decimal("0.50"),
                        amount=amount,
                        fee_usdc=Decimal("1.23"),
                        timestamp=now + timedelta(seconds=fill_index),
                    )
                )
        await session.commit()


async def insert_grouped_event_fixture(database, wallet_id: int, asset_id: str) -> None:
    started_at = datetime(2026, 8, 1, 0, 0)
    async with database.sessions() as session:

        async def add_event(
            *,
            event_type: str,
            asset: str,
            offset: int,
            before_size: str,
            after_size: str,
            before_price: str,
            after_price: str,
            status: str = "matched",
            fill: tuple[str, str, str] | None = None,
            payout: str | None = None,
            redemption_cost: str | None = None,
        ) -> None:
            before = Decimal(before_size)
            after = Decimal(after_size)
            event = PositionEvent(
                wallet_id=wallet_id,
                asset_id=asset,
                condition_id=f"condition-{asset}",
                type=event_type,
                title=f"分组市场 {asset}",
                outcome="Yes",
                event_slug=f"event-{asset}",
                delta_size=after - before,
                before_size=before,
                after_size=after,
                before_avg_price=Decimal(before_price),
                after_avg_price=Decimal(after_price),
                average_fill_price=Decimal(fill[2]) if fill else None,
                current_value=after * Decimal(after_price),
                reconciliation_status=status,
                first_detected_at=started_at + timedelta(minutes=offset),
                settled_at=started_at + timedelta(minutes=offset),
                payout_amount=Decimal(payout) if payout is not None else None,
                redemption_cost_basis=(
                    Decimal(redemption_cost) if redemption_cost is not None else None
                ),
                transaction_hash=f"0x{asset}{offset}" if event_type == "redeemed" else None,
            )
            session.add(event)
            await session.flush()
            if fill is not None:
                side, size, price = fill
                session.add(
                    PositionEventFill(
                        event_id=event.id,
                        fingerprint=f"fixture-{asset}-{offset}",
                        side=side,
                        size=Decimal(size),
                        price=Decimal(price),
                        amount=Decimal(size) * Decimal(price),
                        timestamp=started_at + timedelta(minutes=offset),
                        transaction_hash=f"0xfill{asset}{offset}",
                    )
                )

        await add_event(
            event_type="opened",
            asset=asset_id,
            offset=1,
            before_size="0",
            after_size="10",
            before_price="0",
            after_price="0.4",
            fill=("BUY", "10", "0.4"),
        )
        await add_event(
            event_type="decreased",
            asset=asset_id,
            offset=2,
            before_size="10",
            after_size="5",
            before_price="0.4",
            after_price="0.4",
            fill=("SELL", "5", "0.6"),
        )
        await add_event(
            event_type="closed",
            asset=asset_id,
            offset=3,
            before_size="5",
            after_size="0",
            before_price="0.4",
            after_price="0",
            fill=("SELL", "5", "0.3"),
        )
        await add_event(
            event_type="opened",
            asset=asset_id,
            offset=4,
            before_size="0",
            after_size="2",
            before_price="0",
            after_price="0.2",
            fill=("BUY", "2", "0.2"),
        )
        await add_event(
            event_type="redeemed",
            asset="redeemed-asset",
            offset=5,
            before_size="6",
            after_size="0",
            before_price="0",
            after_price="0",
            status="onchain",
            payout="10",
            redemption_cost="6",
        )
        await add_event(
            event_type="closed",
            asset="incomplete-asset",
            offset=6,
            before_size="1",
            after_size="0",
            before_price="1",
            after_price="0",
            status="unavailable",
        )
        await add_event(
            event_type="opened",
            asset="gap-asset",
            offset=7,
            before_size="0",
            after_size="3",
            before_price="0",
            after_price="0.2",
            fill=("BUY", "3", "0.2"),
        )
        await add_event(
            event_type="opened",
            asset="gap-asset",
            offset=8,
            before_size="0",
            after_size="4",
            before_price="0",
            after_price="0.25",
            fill=("BUY", "4", "0.25"),
        )
        await session.commit()


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

    deleted = client.delete(f"/api/overlap-alerts/{alert['id']}")
    assert deleted.status_code == 204
    assert client.get(
        "/api/overlap-alerts",
        params={"tracked_wallet_id": tracked_wallet["id"]},
    ).json() == {"items": [], "unread_count": 0}

    assert client.put("/api/my-wallet", json={"address": OTHER_ADDRESS}).status_code == 200
    replacement_view = client.get(
        "/api/overlap-alerts",
        params={"tracked_wallet_id": tracked_wallet["id"]},
    ).json()
    assert replacement_view == {"items": [], "unread_count": 0}


def test_unread_common_position_alert_cannot_be_deleted(app_client_factory):
    initial = position(size="100")
    reduced = position(size="60")
    mine = position(size="10")
    client, _ = app_client_factory([[initial], [mine], [reduced]])
    tracked_wallet = add_wallet(client)
    assert client.put("/api/my-wallet", json={"address": MY_ADDRESS}).status_code == 200

    assert client.post(f"/api/wallets/{tracked_wallet['id']}/sync").status_code == 200
    alert = client.get(
        "/api/overlap-alerts",
        params={"tracked_wallet_id": tracked_wallet["id"]},
    ).json()["items"][0]

    response = client.delete(f"/api/overlap-alerts/{alert['id']}")
    assert response.status_code == 409
    assert response.json()["detail"] == "请先将提醒标为已读"


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


def test_redemption_without_asset_or_outcome_uses_unique_trade_balance(
    app_client_factory,
):
    condition_id = "0x" + "e" * 64
    winner = position(
        asset_id="winner-asset",
        condition_id=condition_id,
        size="59.666665",
        outcome="Winner",
    )
    loser = position(
        asset_id="loser-asset",
        condition_id=condition_id,
        size="69.7446",
        outcome="Loser",
    )
    client, fake = app_client_factory([[winner, loser]])
    now = utcnow()
    fake.trades = [
        TradeSnapshot(
            asset_id=winner.asset_id,
            condition_id=condition_id,
            side="BUY",
            size=winner.size,
            price=Decimal("0.54"),
            timestamp=now - timedelta(seconds=2),
            transaction_hash="0xwinnerbuy",
            outcome=winner.outcome,
        ),
        TradeSnapshot(
            asset_id=loser.asset_id,
            condition_id=condition_id,
            side="BUY",
            size=loser.size,
            price=Decimal("0.47"),
            timestamp=now - timedelta(seconds=1),
            transaction_hash="0xloserbuy",
            outcome=loser.outcome,
        ),
    ]
    fake.redemptions = [
        RedemptionSnapshot(
            asset_id="",
            condition_id=condition_id,
            title="稍后补全元数据的赎回",
            outcome="",
            outcome_index=999,
            event_slug="ambiguous-redemption",
            market_slug="ambiguous-redemption",
            size=winner.size,
            usdc_size=winner.size,
            timestamp=now + timedelta(seconds=1),
            transaction_hash="0xmanualredeem",
        )
    ]

    wallet = add_wallet(client)
    event = client.get(
        "/api/position-events",
        params={"wallet_id": wallet["id"]},
    ).json()["items"][0]

    assert event["asset_id"] == winner.asset_id
    assert event["outcome"] == winner.outcome
    assert event["transaction_hash"] == "0xmanualredeem"


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


def test_position_event_groups_preserve_cycles_and_summarize_wallet_pnl(
    app_client_factory,
):
    active = position(
        asset_id="grouped-asset",
        size="2",
        avg_price="0.2",
        current_price="0.7",
    )
    client, _ = app_client_factory([[active]])
    wallet = add_wallet(client)
    portal = client.portal
    assert portal is not None
    portal.call(
        insert_grouped_event_fixture,
        client.app.state.database,
        wallet["id"],
        active.asset_id,
    )

    response = client.get(
        "/api/position-event-groups",
        params={"wallet_id": wallet["id"], "limit": 10},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 4
    grouped = next(item for item in body["items"] if item["asset_id"] == active.asset_id)
    assert grouped["event_count"] == 4
    assert grouped["event_counts"] == {
        "opened": 2,
        "increased": 0,
        "decreased": 1,
        "closed": 1,
        "redeemed": 0,
    }
    assert grouped["cycle_count"] == 2
    assert [cycle["status"] for cycle in grouped["cycles"]] == ["closed", "open"]
    assert [cycle["start_source"] for cycle in grouped["cycles"]] == [
        "opened",
        "opened",
    ]
    assert all(cycle["history_complete"] for cycle in grouped["cycles"])
    assert [event["type"] for event in grouped["cycles"][0]["events"]] == [
        "opened",
        "decreased",
        "closed",
    ]
    assert grouped["confirmed_realized_pnl"] == pytest.approx(0.5)
    assert grouped["incomplete_profit_events"] == 0

    gap_group = next(item for item in body["items"] if item["asset_id"] == "gap-asset")
    assert gap_group["cycle_count"] == 2
    assert [cycle["status"] for cycle in gap_group["cycles"]] == [
        "history_gap",
        "history_gap",
    ]
    assert not any(cycle["history_complete"] for cycle in gap_group["cycles"])

    redeemed = next(item for item in body["items"] if item["asset_id"] == "redeemed-asset")
    assert redeemed["cycles"][0]["start_source"] == "first_recorded"
    assert redeemed["cycles"][0]["history_complete"] is False

    assert body["pnl"]["confirmed_realized_pnl"] == pytest.approx(4.5)
    assert body["pnl"]["current_unrealized_pnl"] == pytest.approx(1)
    assert body["pnl"]["confirmed_total_pnl"] == pytest.approx(5.5)
    assert body["pnl"]["incomplete_realized_events"] == 1
    assert body["pnl"]["complete"] is False

    first_page = client.get(
        "/api/position-event-groups",
        params={"wallet_id": wallet["id"], "limit": 1},
    ).json()
    assert len(first_page["items"]) == 1
    assert first_page["next_cursor"] is not None
    second_page = client.get(
        "/api/position-event-groups",
        params={
            "wallet_id": wallet["id"],
            "limit": 1,
            "cursor": first_page["next_cursor"],
        },
    ).json()
    assert len(second_page["items"]) == 1
    assert second_page["items"][0]["asset_id"] != first_page["items"][0]["asset_id"]
    assert second_page["pnl"] == first_page["pnl"]


def test_position_event_groups_use_official_realized_pnl_for_history_gaps(
    app_client_factory,
):
    active = position(
        asset_id="grouped-asset",
        size="2",
        avg_price="0.2",
        current_price="0.7",
    )
    client, fake = app_client_factory([[active]])
    wallet = add_wallet(client)
    portal = client.portal
    assert portal is not None
    portal.call(
        insert_grouped_event_fixture,
        client.app.state.database,
        wallet["id"],
        active.asset_id,
    )
    fake.closed_positions = [
        ClosedPositionSnapshot(
            asset_id="gap-asset",
            condition_id="condition-gap",
            title="断点市场",
            outcome="Yes",
            event_slug="gap-market",
            market_slug="gap-market",
            avg_price=Decimal("0.7"),
            total_bought=Decimal("140"),
            realized_pnl=Decimal("-98"),
            closed_at=None,
        ),
        ClosedPositionSnapshot(
            asset_id="older-official-asset",
            condition_id="condition-older",
            title="监控前已结仓市场",
            outcome="Yes",
            event_slug="older-market",
            market_slug="older-market",
            avg_price=Decimal("0.5"),
            total_bought=Decimal("20"),
            realized_pnl=Decimal("20"),
            closed_at=None,
        ),
    ]

    body = client.get(
        "/api/position-event-groups",
        params={"wallet_id": wallet["id"], "limit": 10},
    ).json()

    gap_group = next(item for item in body["items"] if item["asset_id"] == "gap-asset")
    assert gap_group["confirmed_realized_pnl"] == pytest.approx(-98)
    assert gap_group["realized_pnl_source"] == "polymarket"
    assert gap_group["realized_pnl_status"] == "confirmed"
    assert body["pnl"]["confirmed_realized_pnl"] == pytest.approx(-78)
    assert body["pnl"]["current_unrealized_pnl"] == pytest.approx(1)
    assert body["pnl"]["confirmed_total_pnl"] == pytest.approx(-77)
    assert body["pnl"]["source"] == "polymarket"
    assert body["pnl"]["complete"] is True


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
    assert event["close_profit_complete"] is True
    assert event["close_cost_basis"] == 4
    assert event["close_proceeds"] == 5
    assert event["close_profit"] == pytest.approx(1)
    assert event["close_profit_percent"] == pytest.approx(25)


def test_close_profit_includes_buys_during_the_close_window(app_client_factory):
    initial = position(size="10", avg_price="0.40")
    client, fake = app_client_factory([[initial], [], []])
    wallet = add_wallet(client)
    traded_at = utcnow()
    fake.trades = [
        TradeSnapshot(
            asset_id=initial.asset_id,
            condition_id=initial.condition_id,
            side="BUY",
            size=Decimal("2"),
            price=Decimal("0.30"),
            timestamp=traded_at,
            transaction_hash="0xbuy-during-close",
        ),
        TradeSnapshot(
            asset_id=initial.asset_id,
            condition_id=initial.condition_id,
            side="SELL",
            size=Decimal("12"),
            price=Decimal("0.60"),
            timestamp=traded_at + timedelta(milliseconds=1),
            transaction_hash="0xclose-after-buy",
        ),
    ]

    client.post(f"/api/wallets/{wallet['id']}/sync")
    client.post(f"/api/wallets/{wallet['id']}/sync")
    event = client.get("/api/position-events", params={"wallet_id": wallet["id"]}).json()["items"][
        0
    ]

    assert event["reconciliation_status"] == "matched"
    assert event["close_profit_complete"] is True
    assert event["close_cost_basis"] == pytest.approx(4.6)
    assert event["close_proceeds"] == pytest.approx(7.2)
    assert event["close_profit"] == pytest.approx(2.6)
    assert event["close_profit_percent"] == pytest.approx(2.6 / 4.6 * 100)


def test_close_profit_is_unavailable_when_fills_are_partial(app_client_factory):
    initial = position(size="10", avg_price="0.40")
    client, fake = app_client_factory([[initial], [], []])
    wallet = add_wallet(client)
    fake.trades = [
        TradeSnapshot(
            asset_id=initial.asset_id,
            condition_id=initial.condition_id,
            side="SELL",
            size=Decimal("9"),
            price=Decimal("0.50"),
            timestamp=utcnow(),
            transaction_hash="0xpartial-close",
        )
    ]

    client.post(f"/api/wallets/{wallet['id']}/sync")
    client.post(f"/api/wallets/{wallet['id']}/sync")
    event = client.get("/api/position-events", params={"wallet_id": wallet["id"]}).json()["items"][
        0
    ]

    assert event["reconciliation_status"] == "partial"
    assert event["close_profit_complete"] is False
    assert event["close_cost_basis"] is None
    assert event["close_proceeds"] is None
    assert event["close_profit"] is None
    assert event["close_profit_percent"] is None


def test_close_profit_accepts_rounding_equivalent_fill_sizes(app_client_factory):
    initial = position(size="21595.6727", avg_price="0.5499")
    client, fake = app_client_factory([[initial], [], []])
    wallet = add_wallet(client)
    fake.trades = [
        TradeSnapshot(
            asset_id=initial.asset_id,
            condition_id=initial.condition_id,
            side="SELL",
            size=Decimal("21595.67"),
            price=Decimal("0.999"),
            timestamp=utcnow(),
            transaction_hash="0xrounded-close",
        )
    ]

    client.post(f"/api/wallets/{wallet['id']}/sync")
    client.post(f"/api/wallets/{wallet['id']}/sync")
    response = client.get("/api/position-events", params={"wallet_id": wallet["id"]}).json()[
        "items"
    ][0]

    assert response["reconciliation_status"] == "matched"
    assert response["close_profit_complete"] is True
    assert response["close_proceeds"] == pytest.approx(
        float(Decimal("21595.67") * Decimal("0.999"))
    )
    assert response["close_profit"] == pytest.approx(
        float(Decimal("21595.67") * Decimal("0.999") - Decimal("21595.6727") * Decimal("0.5499"))
    )

    portal = client.portal
    assert portal is not None
    portal.call(
        set_event_reconciliation_status,
        client.app.state.database,
        response["id"],
        "partial",
    )
    recovered = client.get("/api/position-events", params={"wallet_id": wallet["id"]}).json()[
        "items"
    ][0]
    assert recovered["reconciliation_status"] == "matched"
    assert recovered["close_profit_complete"] is True


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


def test_resolved_disappearance_reuses_existing_onchain_redemption(
    app_client_factory,
):
    initial = position(size="10")
    client, fake = app_client_factory([[initial], [], []])
    now = utcnow()
    fake.evidence = SettlementEvidence(
        resolved_condition_ids=frozenset({initial.condition_id}),
        redeemable_asset_ids=frozenset({initial.asset_id}),
        non_trade_condition_ids=frozenset(),
    )
    fake.market_resolutions[initial.condition_id] = MarketResolution(
        condition_id=initial.condition_id,
        payout_by_asset_id={initial.asset_id: Decimal("1")},
        resolved_at=now + timedelta(seconds=2),
    )
    fake.redemptions = [
        redemption(
            condition_id=initial.condition_id,
            title=initial.title,
            timestamp=now + timedelta(seconds=1),
            size="10",
            transaction_hash="0xsingle-redemption",
        )
    ]
    wallet = add_wallet(client)

    client.post(f"/api/wallets/{wallet['id']}/sync")
    client.post(f"/api/wallets/{wallet['id']}/sync")
    events = client.get("/api/position-events", params={"wallet_id": wallet["id"]}).json()

    redeemed = [item for item in events["items"] if item["type"] == "redeemed"]
    assert len(redeemed) == 1
    assert redeemed[0]["transaction_hash"] == "0xsingle-redemption"
    assert redeemed[0]["asset_id"] == initial.asset_id


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


def test_copy_workspace_aggregates_lifetime_bought_by_wallet(app_client_factory):
    client, _ = app_client_factory([[], []])
    first_wallet = add_wallet(client)
    second_wallet = client.post(
        "/api/wallets",
        json={"address": OTHER_ADDRESS, "label": "第二策略"},
    ).json()
    first_subscription = client.post(
        "/api/copy-trading/subscriptions",
        json={"tracked_wallet_id": first_wallet["id"]},
    ).json()
    second_subscription = client.post(
        "/api/copy-trading/subscriptions",
        json={"tracked_wallet_id": second_wallet["id"]},
    ).json()
    assert client.portal is not None
    client.portal.call(
        insert_copy_buy_fill_fixture,
        client.app.state.database,
        [
            (
                first_subscription["id"],
                "first-wallet-asset",
                1,
                [Decimal("4"), Decimal("1.25")],
            ),
            (
                first_subscription["id"],
                "first-wallet-asset",
                2,
                [Decimal("3.75")],
            ),
            (
                second_subscription["id"],
                "second-wallet-asset",
                1,
                [Decimal("50")],
            ),
        ],
    )

    response = client.get("/api/copy-trading/overview")

    assert response.status_code == 200, response.text
    strategies = {item["wallet"]["id"]: item for item in response.json()["strategies"]}
    assert strategies[first_wallet["id"]]["lifetime_bought_usdc"] == pytest.approx(9)
    assert strategies[second_wallet["id"]]["lifetime_bought_usdc"] == pytest.approx(50)
    assert strategies[first_wallet["id"]]["subscription"]["open_exposure_usdc"] == 0


def test_copy_workspace_returns_zero_lifetime_bought_for_new_wallet(app_client_factory):
    client, _ = app_client_factory([[]])
    wallet = add_wallet(client)
    client.post(
        "/api/copy-trading/subscriptions",
        json={"tracked_wallet_id": wallet["id"]},
    )

    response = client.get("/api/copy-trading/overview")

    assert response.status_code == 200, response.text
    assert response.json()["strategies"][0]["lifetime_bought_usdc"] == 0


def test_copy_workspace_returns_complete_shanghai_daily_realized_pnl_points(
    app_client_factory,
):
    client, _ = app_client_factory([[], []])
    first = add_wallet(client)
    second = client.post(
        "/api/wallets",
        json={"address": OTHER_ADDRESS, "label": "第二策略"},
    ).json()
    subscriptions = []
    for wallet in (first, second):
        response = client.post(
            "/api/copy-trading/subscriptions",
            json={
                "tracked_wallet_id": wallet["id"],
                "copy_ratio_percent": 10,
                "position_cap_usdc": 20,
                "total_exposure_cap_usdc": 80,
                "market_slippage_cents": 5,
            },
        )
        assert response.status_code == 201, response.text
        subscriptions.append(response.json())

    shanghai = ZoneInfo("Asia/Shanghai")
    today = datetime.now(shanghai).date()
    local_day = today - timedelta(days=1)
    assert client.portal is not None
    client.portal.call(
        insert_daily_pnl_fixture,
        client.app.state.database,
        [subscription["id"] for subscription in subscriptions],
        local_day,
    )

    response = client.get("/api/copy-trading/overview")
    assert response.status_code == 200, response.text
    points = response.json()["daily_realized_pnl"]
    assert len(points) == 42
    assert points[0]["date"] == (local_day - timedelta(days=40)).isoformat()
    assert points[-1]["date"] == today.isoformat()
    point_by_date = {point["date"]: point["realized_pnl"] for point in points}
    bought_by_date = {point["date"]: point["bought_usdc"] for point in points}
    assert point_by_date[local_day.isoformat()] == pytest.approx(4)
    assert point_by_date[(local_day - timedelta(days=1)).isoformat()] == 0
    assert point_by_date[(local_day - timedelta(days=2)).isoformat()] == pytest.approx(-2)
    assert point_by_date[(local_day - timedelta(days=40)).isoformat()] == pytest.approx(1)
    assert bought_by_date[local_day.isoformat()] == pytest.approx(12)
    assert bought_by_date[(local_day - timedelta(days=2)).isoformat()] == pytest.approx(8)
    assert bought_by_date[(local_day - timedelta(days=40)).isoformat()] == 0


def test_copy_workspace_filters_daily_realized_pnl_by_tracked_wallet(
    app_client_factory,
):
    client, _ = app_client_factory([[], []])
    first = add_wallet(client)
    second = client.post(
        "/api/wallets",
        json={"address": OTHER_ADDRESS, "label": "第二策略"},
    ).json()
    subscriptions = []
    for wallet in (first, second):
        response = client.post(
            "/api/copy-trading/subscriptions",
            json={
                "tracked_wallet_id": wallet["id"],
                "copy_ratio_percent": 10,
                "position_cap_usdc": 20,
                "total_exposure_cap_usdc": 80,
                "market_slippage_cents": 5,
            },
        )
        assert response.status_code == 201, response.text
        subscriptions.append(response.json())

    shanghai = ZoneInfo("Asia/Shanghai")
    today = datetime.now(shanghai).date()
    local_day = today - timedelta(days=1)
    assert client.portal is not None
    client.portal.call(
        insert_daily_pnl_fixture,
        client.app.state.database,
        [subscription["id"] for subscription in subscriptions],
        local_day,
    )

    first_response = client.get(
        "/api/copy-trading/overview",
        params={"tracked_wallet_id": first["id"]},
    )
    assert first_response.status_code == 200, first_response.text
    first_points = {
        point["date"]: point["realized_pnl"]
        for point in first_response.json()["daily_realized_pnl"]
    }
    first_bought = {
        point["date"]: point["bought_usdc"] for point in first_response.json()["daily_realized_pnl"]
    }
    assert first_points[local_day.isoformat()] == pytest.approx(5)
    assert first_points[(local_day - timedelta(days=2)).isoformat()] == 0
    assert first_points[(local_day - timedelta(days=40)).isoformat()] == pytest.approx(1)
    assert first_bought[local_day.isoformat()] == 0
    assert first_bought[(local_day - timedelta(days=2)).isoformat()] == pytest.approx(8)

    second_response = client.get(
        "/api/copy-trading/overview",
        params={"tracked_wallet_id": second["id"]},
    )
    assert second_response.status_code == 200, second_response.text
    second_points = {
        point["date"]: point["realized_pnl"]
        for point in second_response.json()["daily_realized_pnl"]
    }
    second_bought = {
        point["date"]: point["bought_usdc"]
        for point in second_response.json()["daily_realized_pnl"]
    }
    assert second_points[local_day.isoformat()] == pytest.approx(-1)
    assert second_points[(local_day - timedelta(days=2)).isoformat()] == pytest.approx(-2)
    assert (local_day - timedelta(days=40)).isoformat() not in second_points
    assert second_bought[local_day.isoformat()] == pytest.approx(12)
    assert second_bought[(local_day - timedelta(days=2)).isoformat()] == 0
    assert len(second_response.json()["daily_realized_pnl"]) == 30

    # Filtering daily pnl must not shrink strategy list or totals payload.
    assert len(first_response.json()["strategies"]) == 2
    assert len(second_response.json()["strategies"]) == 2


def test_copy_workspace_serializes_positions_with_wallet_metadata(app_client_factory):
    client, _ = app_client_factory([[]])
    wallet = add_wallet(client)
    subscription = client.post(
        "/api/copy-trading/subscriptions",
        json={
            "tracked_wallet_id": wallet["id"],
            "copy_ratio_percent": 10,
            "position_cap_usdc": 20,
            "total_exposure_cap_usdc": 80,
            "market_slippage_cents": 5,
        },
    ).json()
    portal = client.portal
    assert portal is not None
    portal.call(
        partial(
            add_copy_position,
            client.app.state.database,
            subscription["id"],
        )
    )

    overview = client.get("/api/copy-trading/overview")
    assert overview.status_code == 200, overview.text
    positions = client.get("/api/copy-trading/positions")
    assert positions.status_code == 200, positions.text
    item = positions.json()["items"][0]
    assert item["tracked_wallet_id"] == wallet["id"]
    assert item["tracked_wallet_label"] == wallet["label"]
    assert item["tracked_wallet_address"] == wallet["proxy_wallet"]


def test_copy_workspace_uses_final_payout_when_a_resolved_book_is_gone(app_client_factory):
    client, fake = app_client_factory([[]])
    wallet = add_wallet(client)
    subscription = client.post(
        "/api/copy-trading/subscriptions",
        json={"tracked_wallet_id": wallet["id"]},
    ).json()
    assert client.portal is not None
    client.portal.call(partial(add_copy_position, client.app.state.database, subscription["id"]))

    async def missing_book(asset_id: str):
        raise PolymarketAPIError("Polymarket 接口返回 404：订单簿不存在")

    fake.fetch_order_book = missing_book  # type: ignore[attr-defined]
    condition_id = "0x" + "c" * 64
    fake.market_resolutions[condition_id] = MarketResolution(
        condition_id=condition_id,
        payout_by_asset_id={"copy-asset-1": Decimal("0")},
        resolved_at=datetime(2026, 8, 8, 2, 0),
    )

    overview = client.get("/api/copy-trading/overview").json()
    assert overview["totals"]["valuation_complete"] is True
    assert overview["totals"]["unpriced_positions"] == 0
    assert overview["totals"]["total_pnl"] == pytest.approx(-4)


def test_copy_workspace_rejects_invalid_filters(app_client_factory):
    client, _ = app_client_factory([[]])
    assert client.get("/api/copy-trading/positions", params={"scope": "unknown"}).status_code == 422
    assert client.get("/api/copy-trading/orders", params={"side": "HOLD"}).status_code == 422
    assert (
        client.get("/api/copy-trading/orders", params={"status_group": "unknown"}).status_code
        == 422
    )
