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
from backend.models import (
    CopyLedger,
    CopyOrder,
    CopyPosition,
    CopyRedemption,
    CopySubscription,
    ExecutionAccount,
    PositionEvent,
    WatchedWallet,
)
from backend.monitor import utcnow
from backend.polymarket import OrderBookLevel, OrderBookSnapshot
from backend.schemas import RehearsalExecuteRequest, RehearsalPreviewRequest
from backend.trading import (
    V2_EXCHANGE_ADDRESS,
    V2_NEG_RISK_EXCHANGE_ADDRESS,
    MarketTradeRequest,
    OfficialClobTrader,
    TradeResult,
    TradingUnavailable,
    simulate_market_order,
)

TRACKED_ADDRESS = "0x2222222222222222222222222222222222222222"
CONDITION_ID = "0x" + "8" * 64
SELF_ADDRESS = "0x3333333333333333333333333333333333333333"


class FakeLiveTrader:
    def __init__(self, balance: str = "1000") -> None:
        self.balance = Decimal(balance)
        self.calls = 0

    async def collateral_balance(self) -> Decimal:
        return self.balance

    async def prepare_market(self, request):
        return SimpleNamespace(request=request, signed_order_hash=f"0xsigned{self.calls}")

    async def submit_prepared_market(self, prepared):
        self.calls += 1
        request = prepared.request
        if request.side == "BUY":
            size = request.amount / request.worst_price
            filled_usdc = request.amount
        else:
            size = request.amount
            filled_usdc = request.amount * request.worst_price
        return TradeResult(
            status="filled",
            external_order_id=f"order-{self.calls}",
            external_trade_id=f"trade-{self.calls}",
            filled_size=size,
            filled_usdc=filled_usdc,
            average_price=request.worst_price,
            signed_order_hash=prepared.signed_order_hash,
        )

    async def trade_fee(self, trade_id: str) -> Decimal:
        return Decimal("0")

    async def redeem(self, **kwargs) -> str:
        return "0xredeemed"


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


def install_execution_account(
    client,
    *,
    balance: str = "1000",
    budget: str = "1000",
    reserve: str = "0",
    exposure_cap: str = "1000",
    status: str = "ready",
) -> None:
    async def install() -> None:
        async with client.app.state.database.sessions() as session:
            now = utcnow()
            self_wallet = await session.scalar(
                select(WatchedWallet).where(WatchedWallet.wallet_role == "self")
            )
            if self_wallet is None:
                self_wallet = WatchedWallet(
                    address=SELF_ADDRESS,
                    proxy_wallet=SELF_ADDRESS,
                    label="执行钱包",
                    wallet_role="self",
                    enabled=True,
                    baseline_established=True,
                    status="ok",
                    consecutive_failures=0,
                    created_at=now,
                    updated_at=now,
                )
                session.add(self_wallet)
                await session.flush()
            account = await session.get(ExecutionAccount, 1)
            if account is None:
                account = ExecutionAccount(
                    id=1,
                    wallet_id=self_wallet.id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(account)
            account.signer_address = SELF_ADDRESS
            account.funder_address = SELF_ADDRESS
            account.signature_type = 3
            account.keychain_service = "test"
            account.keychain_account = SELF_ADDRESS
            account.status = status
            account.budget_usdc = Decimal(budget)
            account.cash_reserve_usdc = Decimal(reserve)
            account.max_total_exposure_usdc = Decimal(exposure_cap)
            account.daily_buy_limit_usdc = Decimal("1000")
            account.daily_loss_limit_usdc = Decimal("1000")
            account.auto_redeem = True
            account.collateral_balance = Decimal(balance)
            account.last_balance_at = now
            account.updated_at = now
            await session.commit()

    client.portal.call(install)


def mock_account_verification(monkeypatch, *, balance: str = "400") -> None:
    async def signer_address(self) -> str:
        return SELF_ADDRESS

    async def collateral_balance(self) -> Decimal:
        return Decimal(balance)

    async def collateral_allowances(self) -> dict[str, Decimal]:
        return {
            V2_EXCHANGE_ADDRESS.lower(): Decimal("1"),
            V2_NEG_RISK_EXCHANGE_ADDRESS.lower(): Decimal("1"),
        }

    monkeypatch.setattr(OfficialClobTrader, "signer_address", signer_address)
    monkeypatch.setattr(OfficialClobTrader, "collateral_balance", collateral_balance)
    monkeypatch.setattr(OfficialClobTrader, "collateral_allowances", collateral_allowances)


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

    install_execution_account(client)

    async def activate() -> None:
        async with client.app.state.database.sessions() as session:
            now = utcnow()
            row = await session.get(CopySubscription, subscription["id"])
            assert row is not None
            row.state = "active"
            row.enabled_at = now
            await session.commit()

    client.portal.call(activate)
    trader = FakeLiveTrader()

    async def get_trader():
        return trader

    client.app.state.copy_engine._trader = get_trader
    return {**subscription, "enabled": True, "state": "active"}


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
    calls = {"submit": 0}

    class TimeoutTrader:
        async def collateral_balance(self):
            return Decimal("1000")

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


def test_create_rejects_removed_mode_fields(app_client_factory):
    client, fake = app_client_factory([[]])
    wallet = client.post("/api/wallets", json={"address": TRACKED_ADDRESS}).json()
    response = client.post(
        "/api/copy-trading/subscriptions",
        json={"tracked_wallet_id": wallet["id"], "mode": "paper"},
    )
    assert response.status_code == 422


def test_two_wallets_can_run_live_with_fixed_shared_allocations(app_client_factory, monkeypatch):
    client, fake = app_client_factory([[]], live_copy_enabled=True)
    install_execution_account(
        client,
        balance="400",
        budget="400",
        reserve="240",
        exposure_cap="160",
    )
    mock_account_verification(monkeypatch, balance="400")
    wallets = [
        client.post(
            "/api/wallets",
            json={"address": f"0x{value * 40}", "label": f"目标 {value}"},
        ).json()
        for value in ("4", "5")
    ]
    subscriptions = [
        client.post(
            "/api/copy-trading/subscriptions",
            json={"tracked_wallet_id": wallet["id"], "total_exposure_cap_usdc": 80},
        ).json()
        for wallet in wallets
    ]

    missing_confirmation = client.put(
        f"/api/copy-trading/subscriptions/{subscriptions[0]['id']}/enabled",
        json={"enabled": True},
    )
    assert missing_confirmation.status_code == 422

    for subscription in subscriptions:
        enabled = client.put(
            f"/api/copy-trading/subscriptions/{subscription['id']}/enabled",
            json={"enabled": True, "confirm_live": True},
        )
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["enabled"] is True
        assert enabled.json()["state"] == "active"

    listed = client.get("/api/copy-trading/subscriptions").json()
    assert len([row for row in listed if row["enabled"]]) == 2
    still_requires_confirmation = client.put(
        f"/api/copy-trading/subscriptions/{subscriptions[0]['id']}/enabled",
        json={"enabled": True},
    )
    assert still_requires_confirmation.status_code == 422

    overallocated = client.put(
        f"/api/copy-trading/subscriptions/{subscriptions[0]['id']}",
        json={
            "copy_ratio_percent": 10,
            "position_cap_usdc": 20,
            "total_exposure_cap_usdc": 100,
            "market_slippage_cents": 5,
        },
    )
    assert overallocated.status_code == 409
    assert "还差 $20.00" in overallocated.json()["detail"]


def test_turning_off_is_exit_only_and_reenable_does_not_backfill(app_client_factory, monkeypatch):
    client, fake = app_client_factory([[]], live_copy_enabled=True)
    install_book(fake)
    install_execution_account(client, balance="400", budget="400", reserve="240")
    mock_account_verification(monkeypatch, balance="400")
    wallet = client.post(
        "/api/wallets",
        json={"address": TRACKED_ADDRESS, "label": "仅退出目标"},
    ).json()
    subscription = client.post(
        "/api/copy-trading/subscriptions",
        json={"tracked_wallet_id": wallet["id"], "total_exposure_cap_usdc": 80},
    ).json()
    enabled = client.put(
        f"/api/copy-trading/subscriptions/{subscription['id']}/enabled",
        json={"enabled": True, "confirm_live": True},
    ).json()
    disabled = client.put(
        f"/api/copy-trading/subscriptions/{subscription['id']}/enabled",
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert disabled.json()["state"] == "exit_only"

    add_event(client, enabled, "opened")
    tick(client)
    assert dashboard(client, enabled)["orders"] == []

    resumed = client.put(
        f"/api/copy-trading/subscriptions/{subscription['id']}/enabled",
        json={"enabled": True, "confirm_live": True},
    )
    assert resumed.status_code == 200, resumed.text
    tick(client)
    assert dashboard(client, enabled)["orders"] == []


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
            global_total=Decimal("150"),
            bought_today=Decimal("70"),
            wallet_capital=Decimal("160"),
            account_daily_buy_limit=Decimal("80"),
            account_daily_loss_limit=Decimal("40"),
        ),
    )
    assert allowed == Decimal("10")
    assert reason is None


def test_global_risk_usage_aggregates_all_live_subscriptions(app_client_factory):
    client, fake = app_client_factory([[]])
    first = configured_subscription(client, fake)
    second_wallet = client.post(
        "/api/wallets",
        json={"address": "0x" + "7" * 40, "label": "第二目标"},
    ).json()
    second = client.post(
        "/api/copy-trading/subscriptions",
        json={"tracked_wallet_id": second_wallet["id"], "total_exposure_cap_usdc": 80},
    ).json()

    async def seed_and_read() -> RiskUsage:
        async with client.app.state.database.sessions() as session:
            now = utcnow()
            second_row = await session.get(CopySubscription, second["id"])
            assert second_row is not None
            second_row.state = "active"
            for subscription_id, asset_id, cost in (
                (first["id"], "asset-first", Decimal("10")),
                (second["id"], "asset-second", Decimal("20")),
            ):
                position = CopyPosition(
                    subscription_id=subscription_id,
                    asset_id=asset_id,
                    condition_id=CONDITION_ID,
                    title=asset_id,
                    outcome="Yes",
                    cycle_no=1,
                    attributed_size=Decimal("10"),
                    attributed_cost=cost,
                    reserved_buy_usdc=Decimal("0"),
                    realized_pnl=Decimal("0"),
                    status="open",
                    created_at=now,
                    updated_at=now,
                )
                session.add(position)
                await session.flush()
                session.add(
                    CopyLedger(
                        subscription_id=subscription_id,
                        copy_position_id=position.id,
                        order_id=None,
                        type="buy",
                        amount_usdc=cost,
                        realized_pnl=Decimal("0"),
                        timestamp=now,
                    )
                )
            await session.commit()
            account = await session.get(ExecutionAccount, 1)
            assert account is not None
        return await client.app.state.copy_engine._risk_usage(first["id"], account)

    usage = client.portal.call(seed_and_read)
    assert usage.total == Decimal("10")
    assert usage.global_total == Decimal("30")
    assert usage.bought_today == Decimal("30")


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


def test_rehearsal_requires_valid_cap_and_exact_second_confirmation():
    with pytest.raises(ValidationError):
        RehearsalPreviewRequest(
            market_url="https://polymarket.com/event/x",
            outcome="Yes",
            max_total_usdc=Decimal("100.01"),
        )
    with pytest.raises(ValidationError):
        RehearsalExecuteRequest(confirmation_id="x" * 30, confirmation_text="确认")
    accepted = RehearsalExecuteRequest(
        confirmation_id="x" * 30, confirmation_text="确认执行真实买入"
    )
    assert accepted.confirmation_text == "确认执行真实买入"


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


@pytest.mark.parametrize("signature_type", [1, 3])
def test_v2_prepared_order_captures_hash_trade_id_and_actual_fee(monkeypatch, signature_type):
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
        signature_type=signature_type,
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


def test_live_only_migration_deletes_paper_graph_and_preserves_live_orders(tmp_path: Path):
    database_path = tmp_path / "live-only-migration.db"
    config = AlembicConfig(str(Path("backend/alembic.ini").resolve()))
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{database_path}"
    command.upgrade(config, "0013_simple_copy_trading_v2")
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    with sqlite3.connect(database_path) as connection:
        for wallet_id, address in ((1, TRACKED_ADDRESS), (2, "0x" + "6" * 40)):
            connection.execute(
                """INSERT INTO watched_wallets
                (id,address,proxy_wallet,label,wallet_role,enabled,baseline_established,status,
                 consecutive_failures,created_at,updated_at)
                VALUES (?,?,?,?, 'tracked',1,1,'ok',0,?,?)""",
                (wallet_id, address, address, f"钱包 {wallet_id}", now, now),
            )
        connection.execute(
            """INSERT INTO copy_subscriptions
            (id,tracked_wallet_id,mode,state,copy_ratio_percent,position_cap_usdc,
             total_exposure_cap_usdc,market_slippage_cents,baseline_event_id,
             last_processed_event_id,created_at,updated_at)
            VALUES (1,1,'paper','active',10,20,80,5,0,0,?,?),
                   (2,2,'live','disabled',10,20,80,5,0,0,?,?)""",
            (now, now, now, now),
        )
        connection.execute(
            """INSERT INTO copy_positions
            (id,subscription_id,asset_id,condition_id,title,outcome,cycle_no,
             attributed_size,attributed_cost,reserved_buy_usdc,realized_pnl,status,
             created_at,updated_at)
            VALUES (1,1,'paper-asset',?,'模拟仓位','Yes',1,10,5,0,0,'open',?,?)""",
            (CONDITION_ID, now, now),
        )
        order_values = (
            "subscription_id,copy_position_id,idempotency_key,source,asset_id,condition_id,"
            "side,mode,requested_size,requested_usdc,limit_price,filled_size,filled_usdc,"
            "fee_usdc,status,created_at,updated_at"
        )
        connection.execute(
            f"""INSERT INTO copy_orders ({order_values})
            VALUES (1,1,'paper-order','copy','paper-asset',?,'BUY','paper',10,5,.5,10,5,0,
                    'filled',?,?)""",
            (CONDITION_ID, now, now),
        )
        connection.execute(
            f"""INSERT INTO copy_orders ({order_values})
            VALUES (2,NULL,'live-order','copy','live-asset',?,'BUY','live',10,5,.5,10,5,0,
                    'filled',?,?)""",
            (CONDITION_ID, now, now),
        )
        connection.execute(
            f"""INSERT INTO copy_orders ({order_values})
            VALUES (NULL,NULL,'rehearsal-order','rehearsal','rehearsal-asset',?,'BUY','live',
                    10,5,.5,10,5,0,'filled',?,?)""",
            (CONDITION_ID, now, now),
        )
        connection.execute(
            """INSERT INTO copy_fills
            (order_id,fingerprint,size,price,amount,fee_usdc,timestamp)
            VALUES (1,'paper-fill',10,.5,5,0,?)""",
            (now,),
        )
        connection.execute(
            """INSERT INTO copy_ledger
            (subscription_id,copy_position_id,order_id,type,amount_usdc,realized_pnl,timestamp)
            VALUES (1,1,1,'buy',5,0,?)""",
            (now,),
        )
        connection.execute(
            """INSERT INTO copy_redemptions
            (copy_position_id,status,size,attempts,created_at,updated_at)
            VALUES (1,'pending',10,0,?,?)""",
            (now, now),
        )
        connection.commit()

    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT id FROM copy_subscriptions").fetchall() == [(2,)]
        assert connection.execute(
            "SELECT idempotency_key FROM copy_orders ORDER BY id"
        ).fetchall() == [("live-order",), ("rehearsal-order",)]
        assert connection.execute("SELECT count(*) FROM copy_positions").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM copy_fills").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM copy_ledger").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM copy_redemptions").fetchone()[0] == 0
        subscription_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(copy_subscriptions)")
        }
        order_columns = {row[1] for row in connection.execute("PRAGMA table_info(copy_orders)")}
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(copy_subscriptions)")}
        assert "mode" not in subscription_columns
        assert "mode" not in order_columns
        assert "uq_copy_subscriptions_single_live" not in indexes
