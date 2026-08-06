from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from pydantic import ValidationError
from sqlalchemy import func, select

from backend.copy_trading import RiskUsage, allowed_buy_usdc, market_worst_price
from backend.models import CopyOrder, CopyRedemption, CopySubscription, PositionEvent
from backend.monitor import utcnow
from backend.polymarket import OrderBookLevel, OrderBookSnapshot
from backend.schemas import RehearsalExecuteRequest, RehearsalPreviewRequest
from backend.trading import (
    MarketTradeRequest,
    OfficialClobTrader,
    TradingUnavailable,
    simulate_market_order,
)

TRACKED_ADDRESS = "0x2222222222222222222222222222222222222222"
CONDITION_ID = "0x" + "8" * 64


def install_book(fake, *, ask: str = "0.50", bid: str = "0.49", depth: str = "1000") -> None:
    async def fetch_order_book(asset_id: str) -> OrderBookSnapshot:
        return OrderBookSnapshot(
            asset_id=asset_id,
            bids=(OrderBookLevel(Decimal(bid), Decimal(depth)),),
            asks=(OrderBookLevel(Decimal(ask), Decimal(depth)),),
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            neg_risk=False,
        )

    fake.fetch_order_book = fetch_order_book  # type: ignore[attr-defined]


def configured_subscription(client, fake) -> dict:
    install_book(fake)
    wallet = client.post("/api/wallets", json={"address": TRACKED_ADDRESS, "label": "低频观察钱包"})
    assert wallet.status_code == 201, wallet.text
    created = client.post(
        "/api/copy-trading/subscriptions",
        json={"tracked_wallet_id": wallet.json()["id"]},
    )
    assert created.status_code == 201, created.text
    subscription = created.json()
    activated = client.post(
        f"/api/copy-trading/subscriptions/{subscription['id']}/action",
        json={"action": "activate"},
    )
    assert activated.status_code == 200, activated.text
    return activated.json()


def add_event(
    client,
    subscription: dict,
    event_type: str,
    *,
    asset_id: str = "asset-simple",
    before: str = "0",
    after: str = "100",
    price: str = "0.50",
    payout: str | None = None,
) -> int:
    async def insert() -> int:
        async with client.app.state.database.sessions() as session:
            now = utcnow()
            before_size = Decimal(before)
            after_size = Decimal(after)
            row = PositionEvent(
                wallet_id=subscription["tracked_wallet_id"],
                asset_id=asset_id,
                condition_id=CONDITION_ID,
                type=event_type,
                title="开赛前市场",
                outcome="Yes",
                event_slug=f"event-{asset_id}",
                delta_size=after_size - before_size,
                before_size=before_size,
                after_size=after_size,
                before_avg_price=Decimal(price),
                after_avg_price=Decimal(price),
                average_fill_price=Decimal(price),
                current_value=after_size * Decimal(price),
                reconciliation_status="matched",
                first_detected_at=now,
                settled_at=now,
                source_fingerprint=f"test-{asset_id}-{event_type}-{now.timestamp()}",
                payout_amount=Decimal(payout) if payout is not None else None,
                redemption_cost_basis=None,
                transaction_hash="0xtest" if event_type == "redeemed" else None,
            )
            session.add(row)
            await session.commit()
            return row.id

    return client.portal.call(insert)


def tick(client) -> None:
    client.portal.call(client.app.state.copy_engine.tick)


def dashboard(client, subscription: dict) -> dict:
    response = client.get(
        "/api/copy-trading/dashboard",
        params={"tracked_wallet_id": subscription["tracked_wallet_id"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_subscription_defaults_are_only_four_simple_settings(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    assert subscription["copy_ratio_percent"] == 10
    assert subscription["position_cap_usdc"] == 20
    assert subscription["total_exposure_cap_usdc"] == 160
    assert subscription["market_slippage_cents"] == 5
    removed = {
        "large_trade_threshold_usdc",
        "base_bucket_cap_usdc",
        "price_tolerance_ticks",
        "order_ttl_minutes",
        "daily_buy_limit_usdc",
    }
    assert not removed.intersection(subscription)


def test_one_cycle_opens_exactly_once_and_duplicate_ticks_do_nothing(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    event_id = add_event(client, subscription, "opened")
    tick(client)
    tick(client)
    data = dashboard(client, subscription)
    buys = [order for order in data["orders"] if order["side"] == "BUY"]
    assert len(buys) == 1
    assert buys[0]["leader_event_id"] == event_id
    assert buys[0]["source"] == "copy"
    assert data["positions"][0]["cycle_no"] == 1
    assert data["positions"][0]["attributed_size"] > 0


def test_increased_and_decreased_are_monitor_only_events(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    add_event(client, subscription, "increased", before="100", after="140")
    add_event(client, subscription, "decreased", before="140", after="40")
    tick(client)
    data = dashboard(client, subscription)
    assert len(data["orders"]) == 1
    assert data["orders"][0]["side"] == "BUY"
    assert data["subscription"]["last_processed_event_id"] > 0


def test_closed_is_the_only_sell_signal_and_sells_all_attributed_size(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    add_event(client, subscription, "decreased", before="100", after="20")
    tick(client)
    opened = dashboard(client, subscription)
    bought_size = Decimal(str(opened["positions"][0]["attributed_size"]))
    assert bought_size > 0
    add_event(client, subscription, "closed", before="20", after="0")
    tick(client)
    closed = dashboard(client, subscription)
    sells = [row for row in closed["orders"] if row["side"] == "SELL"]
    assert len(sells) == 1
    assert Decimal(str(sells[0]["filled_size"])) == bought_size
    assert Decimal(str(closed["positions"][0]["attributed_size"])) == 0


def test_redeemed_runs_once_without_a_sell_order(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)
    add_event(client, subscription, "redeemed", before="100", after="0", payout="100")
    tick(client)
    tick(client)

    async def counts() -> tuple[int, int]:
        async with client.app.state.database.sessions() as session:
            redemptions = await session.scalar(select(func.count(CopyRedemption.id)))
            sells = await session.scalar(
                select(func.count(CopyOrder.id)).where(CopyOrder.side == "SELL")
            )
            return int(redemptions or 0), int(sells or 0)

    assert client.portal.call(counts) == (1, 0)
    assert dashboard(client, subscription)["positions"][0]["status"] == "redeemed"


@pytest.mark.parametrize("terminal_event", ["closed", "redeemed"])
def test_terminal_event_allows_same_asset_to_open_a_new_cycle(
    app_client_factory, terminal_event: str
):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)
    add_event(
        client,
        subscription,
        terminal_event,
        before="100",
        after="0",
        payout="100" if terminal_event == "redeemed" else None,
    )
    tick(client)
    add_event(client, subscription, "opened")
    tick(client)
    rows = dashboard(client, subscription)["positions"]
    assert {row["cycle_no"] for row in rows} == {1, 2}
    assert sum(1 for row in rows if Decimal(str(row["attributed_size"])) > 0) == 1


def test_restart_style_reprocessing_cannot_duplicate_an_event_order(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    event_id = add_event(client, subscription, "opened")
    tick(client)

    async def rewind() -> None:
        async with client.app.state.database.sessions() as session:
            row = await session.get(CopySubscription, subscription["id"])
            assert row is not None
            row.last_processed_event_id = event_id - 1
            await session.commit()

    client.portal.call(rewind)
    tick(client)
    assert len(dashboard(client, subscription)["orders"]) == 1


def test_timeout_after_signing_is_held_for_reconciliation_without_retry(
    app_client_factory, monkeypatch
):
    client, fake = app_client_factory([[]], live_copy_enabled=True)
    subscription = configured_subscription(client, fake)

    async def make_live() -> None:
        async with client.app.state.database.sessions() as session:
            row = await session.get(CopySubscription, subscription["id"])
            assert row is not None
            row.mode = "live"
            await session.commit()

    client.portal.call(make_live)
    calls = {"submit": 0}

    class TimeoutTrader:
        async def prepare_market(self, request):
            return SimpleNamespace(signed_order_hash="0xsigned-timeout")

        async def submit_prepared_market(self, prepared):
            calls["submit"] += 1
            raise TradingUnavailable("请求超时，结果未知")

    async def trader():
        return TimeoutTrader()

    monkeypatch.setattr(client.app.state.copy_engine, "_trader", trader)
    add_event(client, subscription, "opened")
    tick(client)
    tick(client)
    data = dashboard(client, subscription)
    assert calls["submit"] == 1
    assert data["orders"][0]["status"] == "reconciliation_pending"
    assert data["orders"][0]["signed_order_hash"] == "0xsigned-timeout"


def test_live_mode_is_server_locked(app_client_factory):
    client, fake = app_client_factory([[]])
    wallet = client.post("/api/wallets", json={"address": TRACKED_ADDRESS}).json()
    response = client.post(
        "/api/copy-trading/subscriptions",
        json={"tracked_wallet_id": wallet["id"], "mode": "live", "confirm_live": True},
    )
    assert response.status_code == 409
    assert "锁定" in response.json()["detail"]


def test_risk_limits_are_subscription_simple_and_account_centralized():
    subscription = SimpleNamespace(
        position_cap_usdc=Decimal("20"), total_exposure_cap_usdc=Decimal("160")
    )
    allowed, reason = allowed_buy_usdc(
        Decimal("30"),
        subscription=subscription,
        usage=RiskUsage(
            position=Decimal("0"),
            total=Decimal("150"),
            bought_today=Decimal("70"),
            wallet_capital=Decimal("160"),
            account_daily_buy_limit=Decimal("80"),
            account_daily_loss_limit=Decimal("40"),
        ),
    )
    assert allowed == Decimal("10")
    assert reason is None


def test_market_fak_cancels_unfilled_remainder():
    book = OrderBookSnapshot(
        asset_id="asset",
        bids=(),
        asks=(OrderBookLevel(Decimal("0.50"), Decimal("6")),),
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        neg_risk=False,
    )
    result = simulate_market_order(
        MarketTradeRequest(
            asset_id="asset",
            side="BUY",
            amount=Decimal("5"),
            worst_price=Decimal("0.55"),
        ),
        book,
    )
    assert result.status == "partially_filled"
    assert result.filled_size == Decimal("6")
    assert "取消" in (result.reason or "")


def test_market_slippage_is_one_cents_setting_for_both_sides():
    assert market_worst_price(
        Decimal("0.50"), Decimal("0.01"), Decimal("5"), side="BUY"
    ) == Decimal("0.55")
    assert market_worst_price(
        Decimal("0.50"), Decimal("0.01"), Decimal("5"), side="SELL"
    ) == Decimal("0.45")


def test_rehearsal_requires_five_dollar_cap_and_exact_second_confirmation():
    with pytest.raises(ValidationError):
        RehearsalPreviewRequest(
            market_url="https://polymarket.com/event/x",
            outcome="Yes",
            max_total_usdc=Decimal("5.01"),
        )
    with pytest.raises(ValidationError):
        RehearsalExecuteRequest(confirmation_id="x" * 30, confirmation_text="确认")
    accepted = RehearsalExecuteRequest(
        confirmation_id="x" * 30, confirmation_text="确认执行5美元演练"
    )
    assert accepted.confirmation_text == "确认执行5美元演练"


@dataclass
class FakeSignedOrder:
    salt: str = "1"
    maker: str = "0x" + "1" * 40
    signer: str = "0x" + "2" * 40
    tokenId: str = "123"
    makerAmount: str = "5000000"
    takerAmount: str = "10000000"
    side: int = 0
    signatureType: int = 1
    timestamp: str = "1"
    metadata: str = "0x" + "0" * 64
    builder: str = "0x" + "0" * 64
    expiration: str = "0"
    signature: str = "0xsigned"


def test_v2_prepared_order_captures_hash_trade_id_and_actual_fee(monkeypatch):
    calls: dict[str, object] = {}

    class FakeClient:
        def create_market_order(self, args, options):
            calls["args"] = args
            calls["options"] = options
            return FakeSignedOrder()

        def post_order(self, signed, order_type):
            calls["order_type"] = order_type
            return {
                "success": True,
                "orderID": "order-v2",
                "tradeIDs": ["trade-v2"],
                "makingAmount": "5",
                "takingAmount": "10",
            }

        def get_trades(self, params):
            return {"trades": [{"fee_usdc": "0.025"}]}

    trader = OfficialClobTrader(
        host="https://example.test",
        keychain=SimpleNamespace(),
        key_reference=SimpleNamespace(),
        signature_type=1,
        funder_address="0x" + "3" * 40,
    )
    monkeypatch.setattr(trader, "_client_sync", lambda: FakeClient())
    request = MarketTradeRequest(
        asset_id="123",
        side="BUY",
        amount=Decimal("5"),
        worst_price=Decimal("0.50"),
    )
    prepared = trader._prepare_market_sync(request)
    result = trader._submit_prepared_market_sync(prepared)
    assert prepared.signed_order_hash.startswith("0x")
    assert result.external_order_id == "order-v2"
    assert result.external_trade_id == "trade-v2"
    assert result.fee_usdc == Decimal("0.025")
    assert result.signed_order_hash == prepared.signed_order_hash
    assert str(calls["order_type"]) == "FAK"


def test_reset_migration_removes_copy_rows_and_preserves_monitor_rows(tmp_path: Path):
    database_path = tmp_path / "migration.db"
    config = AlembicConfig(str(Path("backend/alembic.ini").resolve()))
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{database_path}"
    command.upgrade(config, "0012_large_trade_fixed_shares")
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """INSERT INTO watched_wallets
            (id,address,proxy_wallet,label,wallet_role,enabled,baseline_established,status,
             consecutive_failures,created_at,updated_at)
            VALUES (1,?,?,?,?,1,0,'idle',0,?,?)""",
            (TRACKED_ADDRESS, TRACKED_ADDRESS, "保留钱包", "tracked", now, now),
        )
        connection.execute(
            """INSERT INTO wallet_trades
            (wallet_id,fingerprint,asset_id,condition_id,side,size,price,amount,timestamp,imported_at)
            VALUES (1,'keep','asset',?,'BUY',10,.5,5,?,?)""",
            (CONDITION_ID, now, now),
        )
        connection.execute(
            """INSERT INTO copy_subscriptions
            (tracked_wallet_id,mode,state,market_scope,copy_ratio_percent,
             large_trade_threshold_usdc,large_trade_fixed_shares,base_bucket_cap_usdc,
             strong_threshold_usdc,strong_bucket_cap_usdc,event_cap_usdc,
             settlement_day_cap_usdc,total_exposure_cap_usdc,daily_buy_limit_usdc,
             daily_loss_limit_usdc,market_slippage_cents,price_tolerance_ticks,
             price_tolerance_percent,order_ttl_minutes,close_buffer_minutes,
             baseline_event_id,last_processed_event_id,created_at,updated_at)
            VALUES (1,'paper','disabled','temperature',2,100,5,20,1000,40,60,100,160,
                    80,40,5,2,3,360,15,0,0,?,?)""",
            (now, now),
        )
        connection.commit()
    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT count(*) FROM copy_subscriptions").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM wallet_trades").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM watched_wallets").fetchone()[0] == 1
