from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from pydantic import ValidationError
from sqlalchemy import func, select

from backend.copy_trading import (
    RiskUsage,
    allowed_buy_usdc,
    market_worst_price,
    redemption_execution_was_never_submitted,
)
from backend.keychain import KeychainReference, MacOSKeychain
from backend.models import (
    CopyLedger,
    CopyOrder,
    CopyPosition,
    CopyRedemption,
    CopyRedemptionExecution,
    CopySubscription,
    CurrentPosition,
    ExecutionAccount,
    PositionEvent,
    PositionEventFill,
    WatchedWallet,
)
from backend.monitor import utcnow
from backend.polymarket import (
    MarketResolution,
    OrderBookLevel,
    OrderBookSnapshot,
    PolymarketAPIError,
)
from backend.schemas import RehearsalExecuteRequest, RehearsalPreviewRequest
from backend.tests.conftest import position
from backend.trading import (
    FAK_IGNORABLE_REMAINDER_USDC,
    V2_EXCHANGE_ADDRESS,
    V2_NEG_RISK_EXCHANGE_ADDRESS,
    MarketTradeRequest,
    PreparedRedemption,
    RedemptionSubmissionUnknown,
    TradeResult,
    TradingUnavailable,
    UnifiedPolymarketTrader,
    is_effectively_filled,
    normalize_fak_result,
    simulate_market_order,
)

TRACKED_ADDRESS = "0x2222222222222222222222222222222222222222"
CONDITION_ID = "0x" + "8" * 64
SELF_ADDRESS = "0x3333333333333333333333333333333333333333"


class FakeLiveTrader:
    def __init__(self, balance: str = "1000") -> None:
        self.balance = Decimal(balance)
        self.onchain_balance = Decimal("0")
        self.onchain_balance_calls: list[str] = []
        self.onchain_payout_rate: Decimal | None = Decimal("1")
        self.onchain_payout_calls: list[tuple[str, int | None, bool]] = []
        self.redemption_calls: list[dict[str, object]] = []
        self.consume_balance_on_redeem = True
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
        self.redemption_calls.append(kwargs)
        if self.consume_balance_on_redeem:
            self.onchain_balance = Decimal("0")
        return "0xredeemed"

    async def onchain_outcome_balance(self, asset_id: str) -> Decimal:
        self.onchain_balance_calls.append(asset_id)
        return self.onchain_balance

    async def onchain_redemption_payout_rate(
        self,
        condition_id: str,
        outcome_index: int | None,
        neg_risk: bool,
    ) -> Decimal | None:
        self.onchain_payout_calls.append((condition_id, outcome_index, neg_risk))
        return self.onchain_payout_rate


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
    async def ensure_ready_approvals(self) -> None:
        return None

    async def signer_address(self) -> str:
        return SELF_ADDRESS

    async def wallet_type(self) -> str:
        return "DEPOSIT_WALLET"

    async def collateral_balance(self) -> Decimal:
        return Decimal(balance)

    async def collateral_allowances(self) -> dict[str, Decimal]:
        return {
            V2_EXCHANGE_ADDRESS.lower(): Decimal("1"),
            V2_NEG_RISK_EXCHANGE_ADDRESS.lower(): Decimal("1"),
        }

    monkeypatch.setattr(UnifiedPolymarketTrader, "ensure_ready_approvals", ensure_ready_approvals)
    monkeypatch.setattr(UnifiedPolymarketTrader, "signer_address", signer_address)
    monkeypatch.setattr(UnifiedPolymarketTrader, "wallet_type", wallet_type)
    monkeypatch.setattr(UnifiedPolymarketTrader, "collateral_balance", collateral_balance)
    monkeypatch.setattr(UnifiedPolymarketTrader, "collateral_allowances", collateral_allowances)


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


def configured_large_subscription(client, fake, **overrides) -> dict:
    install_book(fake)
    wallet = client.post("/api/wallets", json={"address": TRACKED_ADDRESS, "label": "大额观察钱包"})
    assert wallet.status_code == 201, wallet.text
    payload = {
        "tracked_wallet_id": wallet.json()["id"],
        "strategy_mode": "large_increase",
        "position_cap_usdc": 500,
        "total_exposure_cap_usdc": 1000,
        **overrides,
    }
    created = client.post("/api/copy-trading/subscriptions", json=payload)
    assert created.status_code == 201, created.text
    subscription = created.json()
    install_execution_account(client)

    async def activate() -> None:
        async with client.app.state.database.sessions() as session:
            row = await session.get(CopySubscription, subscription["id"])
            assert row is not None
            row.state = "active"
            row.enabled_at = utcnow()
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


def prepare_daily_loss_force_buy(client, subscription: dict, *, after: str = "100") -> int:
    async def prepare() -> None:
        async with client.app.state.database.sessions() as session:
            now = utcnow()
            account = await session.get(ExecutionAccount, 1)
            assert account is not None
            account.daily_loss_limit_usdc = Decimal("1")
            session.add(
                CopyLedger(
                    subscription_id=subscription["id"],
                    copy_position_id=None,
                    order_id=None,
                    type="settle_loss",
                    amount_usdc=Decimal("0"),
                    realized_pnl=Decimal("-2"),
                    detail="触发测试熔断",
                    timestamp=now,
                )
            )
            size = Decimal(after)
            session.add(
                CurrentPosition(
                    wallet_id=subscription["tracked_wallet_id"],
                    asset_id="asset-simple",
                    condition_id=CONDITION_ID,
                    title="开赛前市场",
                    outcome="Yes",
                    outcome_index=0,
                    icon_url=None,
                    event_slug="event-asset-simple",
                    market_slug="market-asset-simple",
                    size=size,
                    avg_price=Decimal("0.50"),
                    current_price=Decimal("0.50"),
                    initial_value=size * Decimal("0.50"),
                    current_value=size * Decimal("0.50"),
                    cash_pnl=Decimal("0"),
                    percent_pnl=Decimal("0"),
                    total_bought=size,
                    realized_pnl=Decimal("0"),
                    end_date=None,
                    missing_count=0,
                    first_seen_at=now,
                    last_seen_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

    client.portal.call(prepare)
    event_id = add_event(client, subscription, "opened", after=after)
    tick(client)
    data = dashboard(client, subscription)
    return next(item["id"] for item in data["orders"] if item["leader_event_id"] == event_id)


def test_daily_loss_skip_can_be_force_bought_once(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    source_order_id = prepare_daily_loss_force_buy(client, subscription)

    activity = client.get("/api/copy-trading/activities").json()["items"][0]
    assert activity["source_id"] == source_order_id
    assert activity["force_buy_eligible"] is True

    preview_response = client.post(f"/api/copy-trading/orders/{source_order_id}/force-buy/preview")
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["proportional_target_usdc"] == 5
    assert preview["executable_usdc"] == 5
    assert preview["minimum_adjusted"] is False

    execute = client.post(
        f"/api/copy-trading/orders/{source_order_id}/force-buy/execute",
        json={
            "confirmation_id": preview["confirmation_id"],
            "confirmation_text": "确认强制真实买入",
        },
    )
    assert execute.status_code == 200, execute.text
    assert execute.json()["override_of_order_id"] == source_order_id
    assert execute.json()["status"] == "filled"
    assert (
        client.post(f"/api/copy-trading/orders/{source_order_id}/force-buy/preview").status_code
        == 409
    )
    updated = next(
        item
        for item in client.get("/api/copy-trading/activities").json()["items"]
        if item["source_id"] == source_order_id
    )
    assert updated["force_buy_eligible"] is False
    assert updated["force_buy_unavailable_reason"] == "已强制处理"
    assert updated["force_buy_status"] == "filled"


def test_force_buy_uses_market_minimum_but_keeps_position_cap(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    source_order_id = prepare_daily_loss_force_buy(client, subscription, after="1")

    preview = client.post(f"/api/copy-trading/orders/{source_order_id}/force-buy/preview").json()
    assert preview["proportional_target_usdc"] == pytest.approx(0.05)
    assert preview["minimum_adjusted"] is True
    assert preview["executable_usdc"] == preview["minimum_order_usdc"]

    async def cap_below_minimum() -> None:
        async with client.app.state.database.sessions() as session:
            row = await session.get(CopySubscription, subscription["id"])
            assert row is not None
            row.position_cap_usdc = Decimal("2")
            await session.commit()

    client.portal.call(cap_below_minimum)
    blocked = client.post(f"/api/copy-trading/orders/{source_order_id}/force-buy/preview")
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "剩余风控额度低于市场最小下单金额"


def test_subscription_defaults_include_large_increase_threshold(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    assert subscription["copy_ratio_percent"] == 10
    assert subscription["position_cap_usdc"] == 20
    assert subscription["large_increase_threshold_usdc"] == 100
    assert subscription["strategy_mode"] == "normal"
    assert subscription["base_entry_threshold_usdc"] == 100
    assert subscription["base_entry_ratio_percent"] == 10
    assert subscription["tier_one_threshold_usdc"] == 50000
    assert subscription["tier_one_ratio_percent"] == 0.1
    assert subscription["tier_two_threshold_usdc"] == 100000
    assert subscription["tier_two_ratio_percent"] == 0.2
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
    updated = client.put(
        f"/api/copy-trading/subscriptions/{subscription['id']}",
        json={
            "copy_ratio_percent": 10,
            "position_cap_usdc": 20,
            "large_increase_threshold_usdc": 250,
            "total_exposure_cap_usdc": 160,
            "market_slippage_cents": 5,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["large_increase_threshold_usdc"] == 250
    invalid = client.put(
        f"/api/copy-trading/subscriptions/{subscription['id']}",
        json={
            "copy_ratio_percent": 10,
            "position_cap_usdc": 20,
            "large_increase_threshold_usdc": 0,
            "total_exposure_cap_usdc": 160,
            "market_slippage_cents": 5,
        },
    )
    assert invalid.status_code == 422


def test_wallet_creation_atomically_creates_immutable_large_strategy(app_client_factory):
    client, _ = app_client_factory([[]])
    copy_strategy = {
        "strategy_mode": "large_increase",
        "position_cap_usdc": 20,
        "base_entry_threshold_usdc": 100,
        "base_entry_ratio_percent": 10,
        "tier_one_threshold_usdc": 50000,
        "tier_one_ratio_percent": 0.1,
        "tier_two_threshold_usdc": 100000,
        "tier_two_ratio_percent": 0.2,
    }
    created = client.post(
        "/api/wallets",
        json={
            "address": TRACKED_ADDRESS,
            "label": "大额策略",
            "copy_strategy": copy_strategy,
        },
    )
    assert created.status_code == 201, created.text
    subscriptions = client.get("/api/copy-trading/subscriptions").json()
    assert len(subscriptions) == 1
    assert subscriptions[0]["strategy_mode"] == "large_increase"
    assert subscriptions[0]["state"] == "disabled"

    incompatible = client.post(
        "/api/wallets",
        json={
            "address": TRACKED_ADDRESS,
            "label": "重新添加",
            "copy_strategy": {**copy_strategy, "strategy_mode": "normal"},
        },
    )
    assert incompatible.status_code == 409
    assert "不可变" in incompatible.json()["detail"]

    immutable_update = client.put(
        f"/api/copy-trading/subscriptions/{subscriptions[0]['id']}",
        json={**copy_strategy, "strategy_mode": "normal"},
    )
    assert immutable_update.status_code == 422

    partial_update = client.put(
        f"/api/copy-trading/subscriptions/{subscriptions[0]['id']}",
        json={"position_cap_usdc": 25},
    )
    assert partial_update.status_code == 200, partial_update.text
    assert partial_update.json()["position_cap_usdc"] == 25
    assert partial_update.json()["tier_one_threshold_usdc"] == 50000
    assert partial_update.json()["tier_two_ratio_percent"] == 0.2


def test_large_mode_open_threshold_is_inclusive_and_uses_base_ratio(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_large_subscription(client, fake)
    add_event(client, subscription, "opened", after="200")
    tick(client)

    data = dashboard(client, subscription)
    assert Decimal(str(data["orders"][0]["leader_purchase_usdc"])) == Decimal("100")
    assert Decimal(str(data["orders"][0]["proportional_target_usdc"])) == Decimal("10")
    assert Decimal(str(data["orders"][0]["filled_usdc"])) == Decimal("10")


def test_large_mode_qualifying_increase_can_open_after_small_base(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_large_subscription(client, fake)
    add_event(client, subscription, "opened", after="100")
    tick(client)
    first = dashboard(client, subscription)
    assert first["positions"] == []
    assert first["orders"][0]["reason"] == "底仓金额未达到建仓阈值"

    event_id = add_event(client, subscription, "increased", before="100", after="100100")
    tick(client)
    tick(client)
    data = dashboard(client, subscription)
    order = next(item for item in data["orders"] if item["leader_event_id"] == event_id)
    assert Decimal(str(order["leader_purchase_usdc"])) == Decimal("50000")
    assert Decimal(str(order["proportional_target_usdc"])) == Decimal("50")
    assert Decimal(str(order["filled_usdc"])) == Decimal("50")
    assert len(data["positions"]) == 1


def test_large_mode_uses_only_highest_matching_tier_on_existing_position(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_large_subscription(client, fake)
    add_event(client, subscription, "opened", after="200")
    tick(client)
    event_id = add_event(client, subscription, "increased", before="200", after="200200")
    tick(client)
    tick(client)

    order = next(
        item
        for item in dashboard(client, subscription)["orders"]
        if item["leader_event_id"] == event_id
    )
    assert Decimal(str(order["leader_purchase_usdc"])) == Decimal("100000")
    assert Decimal(str(order["proportional_target_usdc"])) == Decimal("200")
    assert Decimal(str(order["filled_usdc"])) == Decimal("200")


def test_large_mode_shows_below_tier_skip_in_activity(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_large_subscription(client, fake)
    event_id = add_event(client, subscription, "increased", before="100", after="102")
    tick(client)

    data = dashboard(client, subscription)
    assert len(data["orders"]) == 1
    assert data["orders"][0]["status"] == "skipped"
    assert data["orders"][0]["reason"] == "单次净加仓未达到第一档阈值"
    assert data["subscription"]["last_processed_event_id"] == event_id
    activities = client.get("/api/copy-trading/activities").json()
    assert len(activities["items"]) == 1
    assert activities["items"][0]["status"] == "skipped"
    assert activities["items"][0]["reason"] == "单次净加仓未达到第一档阈值"
    overview = client.get("/api/copy-trading/overview").json()
    assert overview["recent_orders"][0]["status"] == "skipped"
    assert overview["recent_activities"][0]["status"] == "skipped"


def test_normal_mode_shows_skipped_audit_and_status_filter(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "increased", before="100", after="500")
    tick(client)

    data = dashboard(client, subscription)
    assert len(data["orders"]) == 1
    assert data["orders"][0]["status"] == "skipped"
    assert data["orders"][0]["reason"] == "首次建仓未成功，不追随后续加仓"
    visible = client.get("/api/copy-trading/activities").json()["items"]
    assert len(visible) == 1
    assert visible[0]["status"] == "skipped"
    assert visible[0]["reason"] == "首次建仓未成功，不追随后续加仓"
    filtered = client.get(
        "/api/copy-trading/activities", params={"status_group": "skipped"}
    ).json()["items"]
    assert [item["status"] for item in filtered] == ["skipped"]

    async def mark_as_blocked() -> None:
        async with client.app.state.database.sessions() as session:
            order = await session.get(CopyOrder, data["orders"][0]["id"])
            assert order is not None
            order.status = "blocked"
            await session.commit()

    client.portal.call(mark_as_blocked)
    visible = client.get("/api/copy-trading/activities").json()["items"]
    assert len(visible) == 1
    assert visible[0]["status"] == "blocked"


def test_large_mode_does_not_top_up_below_market_minimum(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_large_subscription(
        client,
        fake,
        base_entry_threshold_usdc=100,
        base_entry_ratio_percent=0.1,
    )
    add_event(client, subscription, "opened", after="200")
    tick(client)

    data = dashboard(client, subscription)
    assert data["positions"] == []
    assert data["orders"][0]["reason"] == "按执行比例计算后低于市场最小下单份数"
    assert Decimal(str(data["orders"][0]["proportional_target_usdc"])) == Decimal("0.1")


def test_large_mode_increase_is_truncated_at_market_cap(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_large_subscription(
        client,
        fake,
        position_cap_usdc=20,
    )
    add_event(client, subscription, "opened", after="200")
    tick(client)
    event_id = add_event(client, subscription, "increased", before="200", after="100200")
    tick(client)
    tick(client)

    data = dashboard(client, subscription)
    increase = next(item for item in data["orders"] if item["leader_event_id"] == event_id)
    assert Decimal(str(increase["proportional_target_usdc"])) == Decimal("50")
    assert Decimal(str(increase["filled_usdc"])) == Decimal("10")
    assert Decimal(str(data["positions"][0]["attributed_cost"])) == Decimal("20")


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
    assert Decimal(str(buys[0]["leader_purchase_usdc"])) == Decimal("50")
    assert Decimal(str(buys[0]["proportional_target_usdc"])) == Decimal("5")
    assert data["positions"][0]["cycle_no"] == 1
    assert data["positions"][0]["attributed_size"] > 0


def test_workspace_order_apis_return_amount_snapshots(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)

    overview = client.get("/api/copy-trading/overview")
    orders = client.get("/api/copy-trading/orders")
    assert overview.status_code == 200, overview.text
    assert orders.status_code == 200, orders.text
    for order in (overview.json()["recent_orders"][0], orders.json()["items"][0]):
        assert Decimal(str(order["leader_purchase_usdc"])) == Decimal("50")
        assert Decimal(str(order["proportional_target_usdc"])) == Decimal("5")


def test_small_increased_and_decreased_are_monitor_only_events(app_client_factory):
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


def test_large_increase_follows_ratio_and_reuses_existing_position(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)
    initial = dashboard(client, subscription)
    position_id = initial["positions"][0]["id"]
    initial_cost = Decimal(str(initial["positions"][0]["attributed_cost"]))

    event_id = add_event(client, subscription, "increased", before="100", after="300")
    tick(client)
    tick(client)

    data = dashboard(client, subscription)
    buys = [order for order in data["orders"] if order["side"] == "BUY"]
    assert len(buys) == 2
    increase_order = next(order for order in buys if order["leader_event_id"] == event_id)
    assert Decimal(str(increase_order["leader_purchase_usdc"])) == Decimal("100")
    assert Decimal(str(increase_order["proportional_target_usdc"])) == Decimal("10")
    assert Decimal(str(increase_order["filled_usdc"])) == Decimal("10")
    assert data["positions"][0]["id"] == position_id
    assert Decimal(str(data["positions"][0]["attributed_cost"])) == initial_cost + Decimal("10")


def test_large_increases_stop_at_remaining_position_cap(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    add_event(client, subscription, "increased", before="100", after="700")
    add_event(client, subscription, "increased", before="700", after="1300")
    tick(client)

    data = dashboard(client, subscription)
    filled_buys = [
        order
        for order in data["orders"]
        if order["side"] == "BUY" and Decimal(str(order["filled_usdc"])) > 0
    ]
    assert [Decimal(str(order["filled_usdc"])) for order in reversed(filled_buys)] == [
        Decimal("5"),
        Decimal("15"),
    ]
    assert Decimal(str(data["positions"][0]["attributed_cost"])) == Decimal("20")
    assert any(order["reason"] == "单仓最大投入已满" for order in data["orders"])


def test_small_initial_open_is_topped_up_to_market_minimum(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened", after="5")
    tick(client)

    data = dashboard(client, subscription)
    assert len(data["positions"]) == 1
    buys = [order for order in data["orders"] if order["side"] == "BUY"]
    assert len(buys) == 1
    assert Decimal(str(buys[0]["filled_usdc"])) == Decimal("2.75")
    assert Decimal(str(buys[0]["filled_size"])) == Decimal("5")
    assert Decimal(str(buys[0]["proportional_target_usdc"])) == Decimal("0.25")


def test_small_initial_open_still_respects_risk_caps(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)

    async def limit_position_cap() -> None:
        async with client.app.state.database.sessions() as session:
            row = await session.get(CopySubscription, subscription["id"])
            assert row is not None
            row.position_cap_usdc = Decimal("2")
            await session.commit()

    client.portal.call(limit_position_cap)
    add_event(client, subscription, "opened", after="5")
    tick(client)

    data = dashboard(client, subscription)
    assert data["positions"] == []
    assert any(
        order["reason"] == "剩余风控额度低于市场最小下单金额"
        and Decimal(str(order["leader_purchase_usdc"])) == Decimal("2.5")
        and Decimal(str(order["proportional_target_usdc"])) == Decimal("0.25")
        for order in data["orders"]
    )


def test_small_large_increase_is_not_topped_up(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)

    async def reduce_copy_ratio() -> None:
        async with client.app.state.database.sessions() as session:
            row = await session.get(CopySubscription, subscription["id"])
            assert row is not None
            row.copy_ratio_percent = Decimal("1")
            await session.commit()

    client.portal.call(reduce_copy_ratio)
    add_event(client, subscription, "opened")
    add_event(client, subscription, "increased", before="100", after="300")
    tick(client)

    data = dashboard(client, subscription)
    buys = [order for order in data["orders"] if order["side"] == "BUY"]
    assert len(buys) == 2
    assert Decimal(str(buys[0]["filled_usdc"])) == Decimal("0")
    assert buys[0]["reason"] == "按执行比例计算后低于市场最小下单份数"
    assert Decimal(str(buys[1]["filled_usdc"])) == Decimal("2.75")


def test_mixed_buy_sell_event_uses_net_position_cost_increase(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)
    event_id = add_event(client, subscription, "increased", before="100", after="110")

    async def add_mixed_fills() -> None:
        async with client.app.state.database.sessions() as session:
            now = utcnow()
            session.add_all(
                [
                    PositionEventFill(
                        event_id=event_id,
                        fingerprint=f"mixed-buy-{event_id}",
                        side="BUY",
                        size=Decimal("300"),
                        price=Decimal("0.50"),
                        amount=Decimal("150"),
                        timestamp=now,
                        transaction_hash="0xmixedbuy",
                    ),
                    PositionEventFill(
                        event_id=event_id,
                        fingerprint=f"mixed-sell-{event_id}",
                        side="SELL",
                        size=Decimal("290"),
                        price=Decimal("0.50"),
                        amount=Decimal("145"),
                        timestamp=now,
                        transaction_hash="0xmixedsell",
                    ),
                ]
            )
            await session.commit()

    client.portal.call(add_mixed_fills)
    tick(client)

    buys = [order for order in dashboard(client, subscription)["orders"] if order["side"] == "BUY"]
    assert len(buys) == 1


def test_unfilled_large_increase_preserves_existing_position(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)
    before = dashboard(client, subscription)["positions"][0]

    class ZeroFillTrader(FakeLiveTrader):
        async def submit_prepared_market(self, prepared):
            self.calls += 1
            return TradeResult(
                status="unmatched",
                external_order_id=f"order-{self.calls}",
                filled_size=Decimal("0"),
                filled_usdc=Decimal("0"),
                signed_order_hash=prepared.signed_order_hash,
            )

    trader = ZeroFillTrader()

    async def get_trader():
        return trader

    client.app.state.copy_engine._trader = get_trader
    add_event(client, subscription, "increased", before="100", after="300")
    tick(client)

    after = dashboard(client, subscription)["positions"][0]
    assert after["status"] == "open"
    assert after["attributed_size"] == before["attributed_size"]
    assert after["attributed_cost"] == before["attributed_cost"]


def test_closed_is_the_only_sell_signal_and_sells_all_attributed_size(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    add_event(client, subscription, "increased", before="100", after="300")
    add_event(client, subscription, "decreased", before="300", after="20")
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


def test_untradeable_sell_dust_is_written_off(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)

    class PartialSellTrader(FakeLiveTrader):
        async def submit_prepared_market(self, prepared):
            self.calls += 1
            request = prepared.request
            if request.side == "BUY":
                return await super().submit_prepared_market(prepared)
            filled_size = request.amount - Decimal("0.001762")
            return TradeResult(
                status="partially_filled",
                external_order_id=f"order-{self.calls}",
                external_trade_id=f"trade-{self.calls}",
                filled_size=filled_size,
                filled_usdc=filled_size * request.worst_price,
                average_price=request.worst_price,
                signed_order_hash=prepared.signed_order_hash,
                reason="FAK 部分成交，剩余已取消",
            )

    trader = PartialSellTrader()

    async def get_trader():
        return trader

    client.app.state.copy_engine._trader = get_trader
    add_event(client, subscription, "closed", before="100", after="0")
    tick(client)

    result = dashboard(client, subscription)
    position = result["positions"][0]
    assert Decimal(str(position["attributed_size"])) == 0
    assert Decimal(str(position["attributed_cost"])) == 0
    assert position["status"] == "dust_closed"

    async def dust_writeoff() -> CopyLedger:
        async with client.app.state.database.sessions() as session:
            row = await session.scalar(
                select(CopyLedger)
                .where(CopyLedger.copy_position_id == position["id"])
                .where(CopyLedger.type == "dust_writeoff")
            )
            assert row is not None
            return row

    ledger = client.portal.call(dust_writeoff)
    assert Decimal(str(ledger.amount_usdc)) == 0
    assert Decimal(str(ledger.realized_pnl)) < 0


def test_redeemed_runs_once_without_a_sell_order(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)
    add_event(client, subscription, "redeemed", before="100", after="0", payout="100")
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


def test_resolved_source_disappearance_redeems_copied_winner(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)
    fake.market_resolutions[CONDITION_ID] = MarketResolution(
        condition_id=CONDITION_ID,
        payout_by_asset_id={"asset-simple": Decimal("1")},
        resolved_at=utcnow(),
    )

    client.portal.call(
        client.app.state.copy_engine.reconcile_terminal_positions,
        subscription["id"],
    )

    position = dashboard(client, subscription)["positions"][0]
    assert Decimal(str(position["attributed_size"])) == 0
    assert position["status"] == "redeemed"


def test_resolved_source_disappearance_records_fractional_payout(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)
    before = dashboard(client, subscription)["positions"][0]
    before_size = Decimal(str(before["attributed_size"]))
    fake.market_resolutions[CONDITION_ID] = MarketResolution(
        condition_id=CONDITION_ID,
        payout_by_asset_id={"asset-simple": Decimal("0.5")},
        resolved_at=utcnow(),
    )
    trader = client.portal.call(client.app.state.copy_engine._trader)
    trader.onchain_payout_rate = Decimal("0.5")

    client.portal.call(
        client.app.state.copy_engine.reconcile_terminal_positions,
        subscription["id"],
    )

    async def redemption_payout() -> Decimal:
        async with client.app.state.database.sessions() as session:
            row = await session.scalar(select(CopyRedemption))
            assert row is not None
            return row.payout_usdc or Decimal("0")

    assert abs(client.portal.call(redemption_payout) - before_size * Decimal("0.5")) < Decimal(
        "0.000000000001"
    )


def test_auto_redeem_false_defers_resolution_redemption(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)
    fake.market_resolutions[CONDITION_ID] = MarketResolution(
        condition_id=CONDITION_ID,
        payout_by_asset_id={"asset-simple": Decimal("1")},
        resolved_at=utcnow(),
    )

    async def set_auto_redeem(enabled: bool) -> None:
        async with client.app.state.database.sessions() as session:
            account = await session.get(ExecutionAccount, 1)
            assert account is not None
            account.auto_redeem = enabled
            await session.commit()

    client.portal.call(set_auto_redeem, False)
    client.portal.call(
        client.app.state.copy_engine.reconcile_terminal_positions,
        subscription["id"],
    )
    assert dashboard(client, subscription)["positions"][0]["status"] == "open"

    client.portal.call(set_auto_redeem, True)
    client.portal.call(
        client.app.state.copy_engine.reconcile_terminal_positions,
        subscription["id"],
    )
    assert dashboard(client, subscription)["positions"][0]["status"] == "redeemed"


def test_resolved_source_disappearance_writes_off_copied_loser(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)
    fake.market_resolutions[CONDITION_ID] = MarketResolution(
        condition_id=CONDITION_ID,
        payout_by_asset_id={"asset-simple": Decimal("0")},
        resolved_at=utcnow(),
    )

    client.portal.call(
        client.app.state.copy_engine.reconcile_terminal_positions,
        subscription["id"],
    )

    position = dashboard(client, subscription)["positions"][0]
    assert Decimal(str(position["attributed_size"])) == 0
    assert position["status"] == "settled_loss"


@pytest.mark.parametrize(
    "failure",
    [
        "Deposit Wallet 自动赎回需要 Relayer API 凭证",
        "缺少官方 Builder Relayer 客户端",
        "Builder 凭证格式无效",
        "自动赎回提交失败：expected safe is not deployed",
        "自动赎回提交失败：expected safe 0x1234 is not deployed",
    ],
)
def test_retries_redemption_after_pre_submit_error_is_fixed(app_client_factory, failure):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)

    class MissingRelayerCredentialsTrader(FakeLiveTrader):
        async def redeem(self, **kwargs) -> str:
            raise TradingUnavailable(failure)

    async def missing_credentials_trader() -> MissingRelayerCredentialsTrader:
        return MissingRelayerCredentialsTrader()

    client.app.state.copy_engine._trader = missing_credentials_trader
    add_event(client, subscription, "redeemed", before="100", after="0", payout="100")
    tick(client)

    async def pending_redemption() -> CopyRedemption:
        async with client.app.state.database.sessions() as session:
            redemption = await session.scalar(select(CopyRedemption))
            assert redemption is not None
            return redemption

    pending = client.portal.call(pending_redemption)
    assert pending.status == "pending"
    assert pending.transaction_hash is None
    assert pending.last_error == failure

    async def configured_trader() -> FakeLiveTrader:
        return FakeLiveTrader()

    client.app.state.copy_engine._trader = configured_trader
    tick(client)
    assert dashboard(client, subscription)["positions"][0]["status"] == "redeemed"

    async def completed_redemption() -> tuple[str | None, str | None]:
        async with client.app.state.database.sessions() as session:
            redemption = await session.scalar(select(CopyRedemption))
            subscription_row = await session.get(CopySubscription, subscription["id"])
            assert redemption is not None and subscription_row is not None
            return redemption.last_error, subscription_row.last_error

    assert client.portal.call(completed_redemption) == (None, None)


def test_unknown_redemption_result_persists_transaction_hash_without_retry(
    app_client_factory,
):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)

    class UnknownResultTrader(FakeLiveTrader):
        async def redeem(self, **kwargs) -> str:
            raise RedemptionSubmissionUnknown("自动赎回结果待确认", "0xpendingredeem")

    trader = UnknownResultTrader()

    async def unknown_result_trader() -> UnknownResultTrader:
        return trader

    client.app.state.copy_engine._trader = unknown_result_trader
    add_event(client, subscription, "redeemed", before="100", after="0", payout="100")
    tick(client)
    trader.onchain_balance = Decimal("1000")
    tick(client)

    async def pending_redemption() -> tuple[str, str | None, int]:
        async with client.app.state.database.sessions() as session:
            row = await session.scalar(select(CopyRedemption))
            assert row is not None
            return row.status, row.transaction_hash, row.attempts

    assert client.portal.call(pending_redemption) == ("pending", "0xpendingredeem", 1)


def test_processed_redemption_with_zero_onchain_balance_reconciles_redemption(
    app_client_factory,
):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)
    redemption_event_id = add_event(client, subscription, "redeemed", before="100", after="0")

    async def mark_event_as_already_processed() -> None:
        async with client.app.state.database.sessions() as session:
            row = await session.get(CopySubscription, subscription["id"])
            assert row is not None
            row.last_processed_event_id = redemption_event_id
            await session.commit()

    client.portal.call(mark_event_as_already_processed)
    tick(client)

    async def reconciliation() -> tuple[str, str, Decimal]:
        async with client.app.state.database.sessions() as session:
            position = await session.scalar(select(CopyPosition))
            redemption = await session.scalar(select(CopyRedemption))
            assert position is not None and redemption is not None
            return position.status, redemption.status, position.realized_pnl

    position_status, redemption_status, realized_pnl = client.portal.call(reconciliation)
    assert (position_status, redemption_status) == ("reconciled", "reconciled")
    assert realized_pnl > Decimal("0")


def test_manual_redemption_reconciles_a_pending_automatic_redemption(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)

    async def create_pending_redemption() -> None:
        async with client.app.state.database.sessions() as session:
            position = await session.scalar(select(CopyPosition))
            assert position is not None
            session.add(
                CopyRedemption(
                    copy_position_id=position.id,
                    status="pending",
                    size=position.attributed_size,
                    payout_usdc=None,
                    transaction_hash=None,
                    attempts=1,
                    last_error="自动赎回提交失败",
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
            position.status = "redeeming"
            await session.commit()

    client.portal.call(create_pending_redemption)
    add_event(client, subscription, "redeemed", before="100", after="0", payout="100")
    tick(client)
    data = dashboard(client, subscription)
    assert data["positions"][0]["status"] == "reconciled"

    async def redemption_status() -> str:
        async with client.app.state.database.sessions() as session:
            redemption = await session.scalar(select(CopyRedemption))
            assert redemption is not None
            return redemption.status

    assert client.portal.call(redemption_status) == "reconciled"


def test_synthetic_execution_redemption_reconciles_pending_position_by_size(
    app_client_factory,
):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)
    redemption_error = "自动赎回提交失败：expected safe is not deployed"

    async def create_pending_redemption_and_execution_event() -> int:
        async with client.app.state.database.sessions() as session:
            position = await session.scalar(select(CopyPosition))
            account = await session.get(ExecutionAccount, 1)
            subscription_row = await session.get(CopySubscription, subscription["id"])
            assert position is not None and account is not None and subscription_row is not None
            now = utcnow()
            session.add(
                CopyRedemption(
                    copy_position_id=position.id,
                    status="pending",
                    size=position.attributed_size,
                    payout_usdc=None,
                    transaction_hash=None,
                    attempts=1,
                    last_error=redemption_error,
                    created_at=now,
                    updated_at=now,
                )
            )
            execution_event = PositionEvent(
                wallet_id=account.wallet_id,
                asset_id=f"redeem:{position.condition_id}:999",
                condition_id=position.condition_id,
                type="redeemed",
                title=position.title,
                outcome="",
                event_slug=position.event_slug,
                delta_size=-position.attributed_size,
                before_size=position.attributed_size,
                after_size=Decimal("0"),
                before_avg_price=Decimal("0"),
                after_avg_price=Decimal("0"),
                average_fill_price=None,
                current_value=Decimal("0"),
                reconciliation_status="onchain",
                first_detected_at=now,
                settled_at=now,
                payout_amount=position.attributed_size,
                redemption_cost_basis=None,
                transaction_hash="0xmanualredeem",
            )
            session.add(execution_event)
            position.status = "redeeming"
            subscription_row.last_error = redemption_error
            await session.commit()
            return execution_event.id

    execution_event_id = client.portal.call(create_pending_redemption_and_execution_event)
    client.portal.call(
        client.app.state.copy_engine.reconcile_terminal_positions,
        subscription["id"],
    )

    async def reconciliation() -> tuple[str, str, str | None, str, str, str | None]:
        async with client.app.state.database.sessions() as session:
            position = await session.scalar(select(CopyPosition))
            redemption = await session.scalar(select(CopyRedemption))
            execution_event = await session.get(PositionEvent, execution_event_id)
            subscription_row = await session.get(CopySubscription, subscription["id"])
            assert (
                position is not None
                and redemption is not None
                and execution_event is not None
                and subscription_row is not None
            )
            return (
                position.status,
                redemption.status,
                redemption.transaction_hash,
                execution_event.asset_id,
                execution_event.outcome,
                subscription_row.last_error,
            )

    result = client.portal.call(reconciliation)
    assert result == (
        "reconciled",
        "reconciled",
        "0xmanualredeem",
        "asset-simple",
        "Yes",
        None,
    )


def test_settlement_execution_event_with_positive_balance_stays_pending(
    app_client_factory,
):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)
    redemption_error = "自动赎回提交失败：原始错误"

    async def create_pending_redemption_and_settlement_event() -> None:
        async with client.app.state.database.sessions() as session:
            position = await session.scalar(select(CopyPosition))
            account = await session.get(ExecutionAccount, 1)
            assert position is not None and account is not None
            now = utcnow()
            session.add(
                CopyRedemption(
                    copy_position_id=position.id,
                    status="pending",
                    size=position.attributed_size,
                    payout_usdc=position.attributed_size,
                    transaction_hash=None,
                    attempts=1,
                    last_error=redemption_error,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                PositionEvent(
                    wallet_id=account.wallet_id,
                    asset_id=position.asset_id,
                    condition_id=position.condition_id,
                    type="redeemed",
                    title=position.title,
                    outcome=position.outcome,
                    event_slug=position.event_slug,
                    delta_size=-position.attributed_size,
                    before_size=position.attributed_size,
                    after_size=Decimal("0"),
                    before_avg_price=Decimal("0"),
                    after_avg_price=Decimal("0"),
                    average_fill_price=None,
                    current_value=Decimal("0"),
                    reconciliation_status="settlement",
                    first_detected_at=now,
                    settled_at=now,
                    payout_amount=position.attributed_size,
                    redemption_cost_basis=None,
                    transaction_hash=None,
                )
            )
            position.status = "redeeming"
            await session.commit()

    client.portal.call(create_pending_redemption_and_settlement_event)
    trader = client.portal.call(client.app.state.copy_engine._trader)
    trader.onchain_balance = Decimal("100")
    client.portal.call(
        client.app.state.copy_engine.reconcile_terminal_positions,
        subscription["id"],
    )

    async def state() -> tuple[str, Decimal, str, str | None]:
        async with client.app.state.database.sessions() as session:
            position = await session.scalar(select(CopyPosition))
            redemption = await session.scalar(select(CopyRedemption))
            assert position is not None and redemption is not None
            return (
                position.status,
                position.attributed_size,
                redemption.status,
                redemption.last_error,
            )

    position_status, size, redemption_status, last_error = client.portal.call(state)
    assert position_status == "redeeming"
    assert size > Decimal("0")
    assert redemption_status == "pending"
    assert last_error == redemption_error
    assert trader.onchain_balance_calls == ["asset-simple"]


def test_settlement_execution_event_with_zero_balance_preserves_original_error(
    app_client_factory,
):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)
    redemption_error = "自动赎回提交失败：原始错误"

    async def create_pending_redemption_and_settlement_event() -> None:
        async with client.app.state.database.sessions() as session:
            position = await session.scalar(select(CopyPosition))
            account = await session.get(ExecutionAccount, 1)
            assert position is not None and account is not None
            now = utcnow()
            session.add(
                CopyRedemption(
                    copy_position_id=position.id,
                    status="pending",
                    size=position.attributed_size,
                    payout_usdc=position.attributed_size,
                    transaction_hash=None,
                    attempts=1,
                    last_error=redemption_error,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                PositionEvent(
                    wallet_id=account.wallet_id,
                    asset_id=position.asset_id,
                    condition_id=position.condition_id,
                    type="redeemed",
                    title=position.title,
                    outcome=position.outcome,
                    event_slug=position.event_slug,
                    delta_size=-position.attributed_size,
                    before_size=position.attributed_size,
                    after_size=Decimal("0"),
                    before_avg_price=Decimal("0"),
                    after_avg_price=Decimal("0"),
                    average_fill_price=None,
                    current_value=Decimal("0"),
                    reconciliation_status="settlement",
                    first_detected_at=now,
                    settled_at=now,
                    payout_amount=position.attributed_size,
                    redemption_cost_basis=None,
                    transaction_hash=None,
                )
            )
            position.status = "redeeming"
            await session.commit()

    client.portal.call(create_pending_redemption_and_settlement_event)
    trader = client.portal.call(client.app.state.copy_engine._trader)
    client.portal.call(
        client.app.state.copy_engine.reconcile_terminal_positions,
        subscription["id"],
    )

    async def state() -> tuple[str, str, str | None]:
        async with client.app.state.database.sessions() as session:
            position = await session.scalar(select(CopyPosition))
            redemption = await session.scalar(select(CopyRedemption))
            assert position is not None and redemption is not None
            return position.status, redemption.status, redemption.last_error

    position_status, redemption_status, last_error = client.portal.call(state)
    assert position_status == "reconciled"
    assert redemption_status == "reconciled"
    assert last_error is not None
    assert redemption_error in last_error
    assert "对账说明" in last_error
    assert trader.onchain_balance_calls == ["asset-simple"]


def test_execution_wallet_redeemable_position_triggers_before_gamma_finalizes(
    app_client_factory,
):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)

    trader = client.portal.call(client.app.state.copy_engine._trader)
    trader.onchain_balance = Decimal("20.243242")
    trader.onchain_payout_rate = None
    fake.redeemable_positions = [
        position(
            asset_id="asset-simple",
            condition_id=CONDITION_ID,
            size="20.2432",
            current_price="0.9995",
            current_value="20.2331",
        )
    ]
    add_event(
        client,
        subscription,
        "decreased",
        before="100",
        after="0.1",
        price="0.50",
    )

    tick(client)

    async def pending_state() -> tuple[str, str, str | None]:
        async with client.app.state.database.sessions() as session:
            copy_position = await session.scalar(select(CopyPosition))
            redemption = await session.scalar(select(CopyRedemption))
            assert copy_position is not None and redemption is not None
            return copy_position.status, redemption.status, redemption.last_error

    pending_position, pending_redemption, pending_error = client.portal.call(pending_state)
    assert pending_position == "redeeming"
    assert pending_redemption == "pending"
    assert pending_error == "等待市场完成链上结算后再自动赎回"
    assert trader.redemption_calls == []

    trader.onchain_payout_rate = Decimal("1")
    tick(client)

    async def state() -> tuple[str, Decimal, str, Decimal, str | None]:
        async with client.app.state.database.sessions() as session:
            copy_position = await session.scalar(select(CopyPosition))
            redemption = await session.scalar(select(CopyRedemption))
            assert copy_position is not None and redemption is not None
            return (
                copy_position.status,
                copy_position.attributed_size,
                redemption.status,
                redemption.payout_usdc or Decimal("0"),
                redemption.transaction_hash,
            )

    position_status, size, redemption_status, payout, transaction_hash = client.portal.call(state)
    assert position_status == "redeemed"
    assert size == Decimal("0")
    assert redemption_status == "completed"
    assert payout > Decimal("0")
    assert transaction_hash == "0xredeemed"
    assert trader.onchain_balance_calls == ["asset-simple", "asset-simple"]
    assert trader.onchain_payout_calls == [
        (CONDITION_ID, None, False),
        (CONDITION_ID, None, False),
    ]
    assert len(trader.redemption_calls) == 1
    assert trader.redemption_calls[0]["condition_id"] == CONDITION_ID
    assert trader.redemption_calls[0]["size"] > Decimal("0")
    assert trader.redemption_calls[0]["neg_risk"] is False
    assert fake.market_resolution_calls
    assert fake.market_resolutions == {}
    assert fake.redeemable_position_calls[-1] == (SELF_ADDRESS, [CONDITION_ID])


def test_execution_wallet_zero_payout_reconciles_stuck_redemption_as_loss(
    app_client_factory,
):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)

    async def create_stuck_redemption() -> None:
        async with client.app.state.database.sessions() as session:
            copy_position = await session.scalar(select(CopyPosition))
            assert copy_position is not None
            session.add(
                CopyRedemption(
                    copy_position_id=copy_position.id,
                    status="pending",
                    size=copy_position.attributed_size,
                    payout_usdc=Decimal("0.0029"),
                    attempts=1,
                    last_error="链上结算结果没有可赎回金额",
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
            copy_position.status = "redeeming"
            await session.commit()

    client.portal.call(create_stuck_redemption)
    trader = client.portal.call(client.app.state.copy_engine._trader)
    trader.onchain_payout_rate = Decimal("0")
    fake.redeemable_positions = [
        position(
            asset_id="asset-simple",
            condition_id=CONDITION_ID,
            size="20.2432",
            current_price="0.0005",
            current_value="0.0029",
        )
    ]

    client.portal.call(
        client.app.state.copy_engine.process_execution_redeemable_positions,
        subscription["id"],
    )

    async def state() -> tuple[str, Decimal, str, Decimal, str | None]:
        async with client.app.state.database.sessions() as session:
            copy_position = await session.scalar(select(CopyPosition))
            redemption = await session.scalar(select(CopyRedemption))
            assert copy_position is not None and redemption is not None
            return (
                copy_position.status,
                copy_position.attributed_size,
                redemption.status,
                redemption.payout_usdc or Decimal("0"),
                redemption.last_error,
            )

    position_status, size, redemption_status, payout, last_error = client.portal.call(state)
    assert position_status == "settled_loss"
    assert size == Decimal("0")
    assert redemption_status == "reconciled"
    assert payout == Decimal("0")
    assert last_error is not None and "兑付为 0" in last_error
    assert trader.redemption_calls == []


def test_unified_redemption_persists_handle_and_condition_execution(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)
    trader = client.portal.call(client.app.state.copy_engine._trader)

    async def attributed_size() -> Decimal:
        async with client.app.state.database.sessions() as session:
            copy_position = await session.scalar(select(CopyPosition))
            assert copy_position is not None
            return copy_position.attributed_size

    size = client.portal.call(attributed_size)
    trader.onchain_balance = size + Decimal("1")
    trader.chain_pusd = Decimal("100")
    fake.redeemable_positions = [
        position(
            asset_id="asset-simple",
            condition_id=CONDITION_ID,
            size=str(size - Decimal("0.00009")),
            current_price="1",
            current_value=str(size),
        )
    ]

    async def onchain_collateral_balance() -> Decimal:
        return trader.chain_pusd

    starts: list[str] = []

    async def start_redemption(*, condition_id: str, neg_risk: bool) -> PreparedRedemption:
        assert condition_id == CONDITION_ID
        assert neg_risk is False
        starts.append(condition_id)
        return PreparedRedemption(
            condition_id=condition_id,
            transaction_id="relay-unified",
            transaction_hash=None,
            handle=SimpleNamespace(),
        )

    async def wait_redemption(prepared: PreparedRedemption) -> str:
        assert prepared.transaction_id == "relay-unified"
        trader.onchain_balance = Decimal("0")
        trader.chain_pusd += size
        fake.redeemable_positions = []
        return "0xunifiedredeemed"

    async def transaction_receipt_success(transaction_hash: str) -> bool:
        assert transaction_hash == "0xunifiedredeemed"
        return True

    trader.onchain_collateral_balance = onchain_collateral_balance
    trader.start_redemption = start_redemption
    trader.wait_redemption = wait_redemption
    trader.transaction_receipt_success = transaction_receipt_success
    trader.onchain_payout_rate = Decimal("1")
    add_event(client, subscription, "decreased", before="100", after="0.1")
    tick(client)

    async def assert_pre_submit_review() -> int:
        async with client.app.state.database.sessions() as session:
            redemption = await session.scalar(select(CopyRedemption))
            execution = await session.scalar(select(CopyRedemptionExecution))
            assert redemption is not None and execution is not None
            assert redemption.execution_id == execution.id
            assert execution.status == "manual_review"
            assert execution.attempts == 0
            assert execution.relayer_transaction_id is None
            assert execution.transaction_hash is None
            # Match the production failure shape: an attempted SDK call that
            # produced no submission identifiers and left both rows in review.
            execution.attempts = 1
            execution.last_error = (
                f"自动赎回提交结果不明：No market found for condition {CONDITION_ID}"
            )
            redemption.status = "manual_review"
            redemption.attempts = 1
            redemption.last_error = execution.last_error
            await session.commit()
            return redemption.id

    client.portal.call(assert_pre_submit_review)
    trader.onchain_balance = size
    tick(client)

    async def state() -> tuple[str, str, str | None, str | None, Decimal | None]:
        async with client.app.state.database.sessions() as session:
            redemption = await session.scalar(select(CopyRedemption))
            execution = await session.scalar(select(CopyRedemptionExecution))
            assert redemption is not None and execution is not None
            return (
                redemption.status,
                execution.status,
                execution.relayer_transaction_id,
                execution.transaction_hash,
                execution.actual_pusd_delta,
            )

    assert client.portal.call(state) == (
        "completed",
        "completed",
        "relay-unified",
        "0xunifiedredeemed",
        size,
    )
    assert starts == [CONDITION_ID]


@pytest.mark.parametrize(
    ("attempts", "transaction_id", "transaction_hash", "submitted_at"),
    [
        (0, "relay-existing", None, None),
        (0, None, "0xexisting", None),
        (0, None, None, datetime.now(UTC).replace(tzinfo=None)),
    ],
)
def test_redemption_review_with_submission_evidence_cannot_auto_recover(
    attempts,
    transaction_id,
    transaction_hash,
    submitted_at,
):
    execution = CopyRedemptionExecution(
        wallet_address=SELF_ADDRESS,
        condition_id=CONDITION_ID,
        status="manual_review",
        attempts=attempts,
        relayer_transaction_id=transaction_id,
        transaction_hash=transaction_hash,
        submitted_at=submitted_at,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    assert redemption_execution_was_never_submitted(execution) is False


def test_redemption_attempt_without_submission_identifiers_can_auto_recover():
    execution = CopyRedemptionExecution(
        wallet_address=SELF_ADDRESS,
        condition_id=CONDITION_ID,
        status="manual_review",
        attempts=1,
        relayer_transaction_id=None,
        transaction_hash=None,
        submitted_at=None,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    assert redemption_execution_was_never_submitted(execution) is True


def test_confirmed_relayer_transaction_without_token_consumption_stays_pending(
    app_client_factory,
):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)

    trader = client.portal.call(client.app.state.copy_engine._trader)
    trader.onchain_balance = Decimal("10")
    trader.consume_balance_on_redeem = False
    fake.redeemable_positions = [
        position(
            asset_id="asset-simple",
            condition_id=CONDITION_ID,
            size="10",
            current_price="1",
            current_value="10",
        )
    ]

    tick(client)

    async def state() -> tuple[str, Decimal, str, str | None, str | None, int]:
        async with client.app.state.database.sessions() as session:
            copy_position = await session.scalar(select(CopyPosition))
            redemption = await session.scalar(select(CopyRedemption))
            assert copy_position is not None and redemption is not None
            return (
                copy_position.status,
                copy_position.attributed_size,
                redemption.status,
                redemption.transaction_hash,
                redemption.last_error,
                redemption.attempts,
            )

    position_status, size, redemption_status, tx_hash, last_error, attempts = client.portal.call(
        state
    )
    assert position_status == "redeeming"
    assert size > Decimal("0")
    assert redemption_status == "pending"
    assert tx_hash is None
    assert last_error is not None and "未消耗 outcome token" in last_error
    assert attempts == 1


def test_onchain_balance_rpc_request_includes_user_agent(monkeypatch):
    captured_headers: dict[str, str] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        @staticmethod
        def read() -> bytes:
            return b'{"jsonrpc":"2.0","id":1,"result":"0x0f4240"}'

    def fake_urlopen(request, timeout):
        assert timeout == 15
        captured_headers.update(dict(request.header_items()))
        return Response()

    monkeypatch.setattr("backend.trading.urlopen", fake_urlopen)
    trader = UnifiedPolymarketTrader(
        host="https://clob.test",
        keychain=MacOSKeychain(),
        key_reference=KeychainReference(service="unused", account="unused"),
        signature_type=3,
        funder_address=SELF_ADDRESS,
        rpc_url="https://polygon.test",
    )

    assert trader._onchain_outcome_balance_sync("1") == Decimal("1")
    assert captured_headers["User-agent"] == "polymarket-wallet-monitor/0.1"


def test_forced_balance_refresh_updates_execution_account(app_client_factory):
    client, fake = app_client_factory([[]])
    configured_subscription(client, fake)
    trader = FakeLiveTrader(balance="321.45")

    async def get_trader() -> FakeLiveTrader:
        return trader

    client.app.state.copy_engine._trader = get_trader

    async def refresh() -> None:
        await client.app.state.copy_engine.refresh_execution_balance(force=True)

    client.portal.call(refresh)

    async def balance() -> Decimal:
        async with client.app.state.database.sessions() as session:
            account = await session.get(ExecutionAccount, 1)
            assert account is not None
            return account.collateral_balance

    assert client.portal.call(balance).quantize(Decimal("0.01")) == Decimal("321.45")


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


def test_fak_ignorable_remainder_is_not_reported_as_partial_fill():
    assert is_effectively_filled(
        Decimal("5.20034620017986"),
        Decimal("4.70034620017986"),
        tolerance=FAK_IGNORABLE_REMAINDER_USDC,
    )
    assert is_effectively_filled(
        Decimal("29.519996"),
        Decimal("29.51"),
        tolerance=FAK_IGNORABLE_REMAINDER_USDC / Decimal("0.899"),
    )


def test_fak_executable_remainder_is_reported_as_partial_fill():
    assert not is_effectively_filled(
        Decimal("10"),
        Decimal("9.499999"),
        tolerance=FAK_IGNORABLE_REMAINDER_USDC,
    )
    assert not is_effectively_filled(
        Decimal("10"),
        Decimal("9.4"),
        tolerance=FAK_IGNORABLE_REMAINDER_USDC / Decimal("0.9"),
    )


def test_reconciled_fak_buy_ignores_ten_mills_of_residual_value():
    request = MarketTradeRequest(
        asset_id="asset",
        side="BUY",
        amount=Decimal("19.999999998"),
        worst_price=Decimal("0.65"),
    )
    result = normalize_fak_result(
        request,
        TradeResult(
            status="partially_filled",
            external_order_id="order",
            filled_size=Decimal("36.345453"),
            filled_usdc=Decimal("19.989999"),
            reason="FAK 部分成交，剩余已取消",
        ),
    )
    assert result.status == "filled"
    assert result.reason is None


def test_resolved_losing_outcome_without_orderbook_is_written_off(app_client_factory):
    client, fake = app_client_factory([[]])
    subscription = configured_subscription(client, fake)
    add_event(client, subscription, "opened")
    tick(client)

    async def missing_book(asset_id: str) -> OrderBookSnapshot:
        raise PolymarketAPIError("Polymarket 接口返回 404：订单簿不存在")

    fake.fetch_order_book = missing_book  # type: ignore[attr-defined]
    fake.market_resolutions[CONDITION_ID] = MarketResolution(
        condition_id=CONDITION_ID,
        payout_by_asset_id={"asset-simple": Decimal("0")},
        resolved_at=datetime(2026, 8, 8, 2, 0),
    )
    close_event_id = add_event(client, subscription, "closed", before="100", after="0")
    tick(client)

    data = dashboard(client, subscription)
    assert data["positions"][0]["status"] == "settled_loss"
    assert Decimal(str(data["positions"][0]["attributed_size"])) == 0
    assert Decimal(str(data["portfolio"]["realized_pnl"])) < 0

    async def processed_event_id() -> int:
        async with client.app.state.database.sessions() as session:
            row = await session.get(CopySubscription, subscription["id"])
            assert row is not None
            return row.last_processed_event_id

    assert client.portal.call(processed_event_id) == close_event_id


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
        assert "large_increase_threshold_usdc" in subscription_columns
        assert connection.execute(
            "SELECT large_increase_threshold_usdc FROM copy_subscriptions WHERE id = 2"
        ).fetchone() == (100,)
        assert connection.execute(
            """SELECT strategy_mode,base_entry_threshold_usdc,base_entry_ratio_percent,
                      tier_one_threshold_usdc,tier_one_ratio_percent,
                      tier_two_threshold_usdc,tier_two_ratio_percent
               FROM copy_subscriptions WHERE id = 2"""
        ).fetchone() == ("normal", 100, 10, 50000, 0.1, 100000, 0.2)
        assert "mode" not in order_columns
        assert "leader_purchase_usdc" in order_columns
        assert "proportional_target_usdc" in order_columns
        assert "uq_copy_subscriptions_single_live" not in indexes


def test_fak_rounding_migration_normalizes_only_ignored_remainders(tmp_path: Path):
    database_path = tmp_path / "fak-rounding.db"
    config = AlembicConfig(str(Path("backend/alembic.ini").resolve()))
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{database_path}"
    command.upgrade(config, "0017_normalize_fak_rounding_dust")
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    order_columns = (
        "idempotency_key,source,asset_id,condition_id,side,requested_size,requested_usdc,"
        "limit_price,filled_size,filled_usdc,fee_usdc,status,reason,created_at,updated_at"
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            f"""INSERT INTO copy_orders ({order_columns})
            VALUES ('buy-dust','copy','buy-asset',?,'BUY',19.8228651793941,16.849435402485,
                    .85,22.453332,16.839999,0,'partially_filled','FAK 部分成交，剩余已取消',?,?)""",
            (CONDITION_ID, now, now),
        )
        connection.execute(
            f"""INSERT INTO copy_orders ({order_columns})
            VALUES ('buy-after-migration','copy','buy-after-asset',?,'BUY',30.7692307661538,
                    19.999999998,.65,36.345453,19.989999,0,'partially_filled',
                    'FAK 部分成交，剩余已取消',?,?)""",
            (CONDITION_ID, now, now),
        )
        connection.execute(
            f"""INSERT INTO copy_orders ({order_columns})
            VALUES ('sell-dust','copy','sell-asset',?,'SELL',29.519996,26.538476404,
                    .899,29.51,29.48049,0,'partially_filled','FAK 部分成交，剩余已取消',?,?)""",
            (CONDITION_ID, now, now),
        )
        connection.execute(
            f"""INSERT INTO copy_orders ({order_columns})
            VALUES ('real-partial','copy','partial-asset',?,'SELL',10,9,
                    .9,9.4,8.46,0,'partially_filled','FAK 部分成交，剩余已取消',?,?)""",
            (CONDITION_ID, now, now),
        )
        connection.commit()

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT idempotency_key, status, reason FROM copy_orders ORDER BY id"
        ).fetchall()
    assert rows == [
        ("buy-dust", "filled", None),
        ("buy-after-migration", "filled", None),
        ("sell-dust", "filled", None),
        ("real-partial", "partially_filled", "FAK 部分成交，剩余已取消"),
    ]
