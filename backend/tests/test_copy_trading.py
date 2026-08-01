from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from backend.copy_trading import (
    RiskUsage,
    allowed_buy_usdc,
    is_extreme_temperature_bucket,
    tolerated_price,
)
from backend.monitor import utcnow
from backend.polymarket import OrderBookLevel, OrderBookSnapshot, TradeSnapshot
from backend.tests.conftest import position
from backend.trading import OfficialClobTrader, TradeRequest, simulate_limit_order

MY_ADDRESS = "0x1111111111111111111111111111111111111111"
TRACKED_ADDRESS = "0x2222222222222222222222222222222222222222"


def test_temperature_scope_only_accepts_extreme_buckets():
    title = "Highest temperature in New York City on August 8?"
    assert is_extreme_temperature_bucket(title, "73°F or below")
    assert is_extreme_temperature_bucket(title, "86°F or higher")
    assert not is_extreme_temperature_bucket(title, "80–81°F")
    assert not is_extreme_temperature_bucket("Will BTC exceed $100k?", "$100k or higher")


def test_price_tolerance_uses_smaller_of_ticks_and_percent():
    assert tolerated_price(
        Decimal("0.50"),
        Decimal("0.01"),
        2,
        Decimal("3"),
        side="BUY",
    ) == Decimal("0.52")
    assert tolerated_price(
        Decimal("0.90"),
        Decimal("0.01"),
        2,
        Decimal("3"),
        side="SELL",
    ) == Decimal("0.88")


def test_risk_caps_and_loss_circuit_breaker():
    subscription = SimpleNamespace(
        daily_loss_limit_usdc=Decimal("40"),
        event_cap_usdc=Decimal("60"),
        settlement_day_cap_usdc=Decimal("100"),
        total_exposure_cap_usdc=Decimal("160"),
        daily_buy_limit_usdc=Decimal("80"),
    )
    allowed, reason = allowed_buy_usdc(
        Decimal("30"),
        bucket_cap=Decimal("40"),
        subscription=subscription,
        usage=RiskUsage(
            bucket=Decimal("15"),
            event=Decimal("20"),
            settlement_day=Decimal("20"),
            total=Decimal("30"),
            bought_today=Decimal("70"),
            wallet_capital=Decimal("160"),
        ),
    )
    assert allowed == Decimal("10")
    assert reason is None

    stopped, reason = allowed_buy_usdc(
        Decimal("1"),
        bucket_cap=Decimal("40"),
        subscription=subscription,
        usage=RiskUsage(
            realized_loss_today=Decimal("40"),
            wallet_capital=Decimal("160"),
        ),
    )
    assert stopped == 0
    assert reason == "已触发当日已实现亏损熔断"


def test_paper_limit_order_consumes_only_marketable_depth():
    book = OrderBookSnapshot(
        asset_id="asset-1",
        bids=(OrderBookLevel(Decimal("0.48"), Decimal("10")),),
        asks=(
            OrderBookLevel(Decimal("0.50"), Decimal("3")),
            OrderBookLevel(Decimal("0.51"), Decimal("4")),
            OrderBookLevel(Decimal("0.53"), Decimal("20")),
        ),
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        neg_risk=False,
    )
    result = simulate_limit_order(
        TradeRequest(
            asset_id="asset-1",
            side="BUY",
            size=Decimal("10"),
            limit_price=Decimal("0.52"),
            expiration=0,
        ),
        book,
    )
    assert result.status == "partially_filled"
    assert result.filled_size == Decimal("7")
    assert result.filled_usdc == Decimal("3.54")


def test_redemption_calldata_supports_standard_and_neg_risk():
    condition = "0x" + "1" * 64
    standard_to, standard_data = OfficialClobTrader._redemption_call(
        condition,
        Decimal("12.5"),
        0,
        False,
    )
    neg_risk_to, neg_risk_data = OfficialClobTrader._redemption_call(
        condition,
        Decimal("12.5"),
        1,
        True,
    )
    assert standard_to.lower().startswith("0x4d97")
    assert neg_risk_to.lower().startswith("0xd91e")
    assert standard_data.startswith("0x01b7037c")
    assert neg_risk_data.startswith("0x01b7037c") is False


def test_copy_subscription_api_defaults_and_state_transitions(app_client_factory):
    client, _ = app_client_factory([[], []])
    my_wallet = client.put(
        "/api/my-wallet",
        json={"address": MY_ADDRESS, "label": "执行钱包"},
    ).json()
    tracked = client.post(
        "/api/wallets",
        json={"address": TRACKED_ADDRESS, "label": "jjavi"},
    ).json()

    account = client.put(
        "/api/copy-trading/account",
        json={"wallet_id": my_wallet["id"]},
    )
    assert account.status_code == 200
    assert account.json()["status"] == "missing_key"
    assert account.json()["budget_usdc"] == 400.0

    created = client.post(
        "/api/copy-trading/subscriptions",
        json={"tracked_wallet_id": tracked["id"]},
    )
    assert created.status_code == 201, created.text
    subscription = created.json()
    assert subscription["mode"] == "paper"
    assert subscription["state"] == "disabled"
    assert subscription["copy_ratio_percent"] == 2.0
    assert subscription["base_bucket_cap_usdc"] == 20.0
    assert subscription["strong_bucket_cap_usdc"] == 40.0

    activated = client.post(
        f"/api/copy-trading/subscriptions/{subscription['id']}/action",
        json={"action": "activate"},
    )
    assert activated.status_code == 200
    assert activated.json()["state"] == "active"

    paused = client.post(
        f"/api/copy-trading/subscriptions/{subscription['id']}/action",
        json={"action": "pause"},
    )
    assert paused.status_code == 200
    assert paused.json()["state"] == "paused"

    resumed = client.post(
        f"/api/copy-trading/subscriptions/{subscription['id']}/action",
        json={"action": "resume"},
    )
    assert resumed.status_code == 200
    assert resumed.json()["state"] == "active"

    dashboard = client.get(
        "/api/copy-trading/dashboard",
        params={"tracked_wallet_id": tracked["id"]},
    )
    assert dashboard.status_code == 200
    assert dashboard.json()["subscription"]["tracked_wallet_label"] == "jjavi"
    assert dashboard.json()["positions"] == []
    assert dashboard.json()["orders"] == []


def test_invalid_cap_order_is_rejected(app_client_factory):
    client, _ = app_client_factory([[]])
    tracked = client.post(
        "/api/wallets",
        json={"address": TRACKED_ADDRESS, "label": "jjavi"},
    ).json()
    response = client.post(
        "/api/copy-trading/subscriptions",
        json={
            "tracked_wallet_id": tracked["id"],
            "base_bucket_cap_usdc": 50,
            "strong_bucket_cap_usdc": 40,
        },
    )
    assert response.status_code == 422
    assert "普通温度桶" in response.json()["detail"]


def test_paper_engine_follows_new_buy_without_backfill(app_client_factory):
    initial = position(
        size="10",
        avg_price="0.50",
        title="Highest temperature in Shanghai on August 8?",
        outcome="75°F or below",
    )
    increased = position(
        size="1010",
        avg_price="0.50",
        title=initial.title,
        outcome=initial.outcome,
    )
    client, fake = app_client_factory([[initial], [increased], [increased]])
    tracked = client.post(
        "/api/wallets",
        json={"address": TRACKED_ADDRESS, "label": "jjavi"},
    ).json()
    subscription = client.post(
        "/api/copy-trading/subscriptions",
        json={"tracked_wallet_id": tracked["id"]},
    ).json()
    client.post(
        f"/api/copy-trading/subscriptions/{subscription['id']}/action",
        json={"action": "activate"},
    )
    fake.trades = [
        TradeSnapshot(
            asset_id=initial.asset_id,
            condition_id=initial.condition_id,
            side="BUY",
            size=Decimal("1000"),
            price=Decimal("0.50"),
            timestamp=utcnow(),
            transaction_hash="0xbuy",
        )
    ]

    async def fetch_order_book(_: str) -> OrderBookSnapshot:
        return OrderBookSnapshot(
            asset_id=initial.asset_id,
            bids=(OrderBookLevel(Decimal("0.49"), Decimal("1000")),),
            asks=(OrderBookLevel(Decimal("0.50"), Decimal("1000")),),
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            neg_risk=False,
        )

    fake.fetch_order_book = fetch_order_book  # type: ignore[attr-defined]
    assert client.post(f"/api/wallets/{tracked['id']}/sync").status_code == 200
    assert client.post(f"/api/wallets/{tracked['id']}/sync").status_code == 200
    assert client.portal is not None
    client.portal.call(client.app.state.copy_engine.process_subscription, subscription["id"])

    dashboard = client.get(
        "/api/copy-trading/dashboard",
        params={"tracked_wallet_id": tracked["id"]},
    ).json()
    assert len(dashboard["orders"]) == 1
    assert dashboard["orders"][0]["status"] == "filled"
    assert Decimal(str(dashboard["positions"][0]["attributed_cost"])) > Decimal("9")
    assert dashboard["subscription"]["last_processed_event_id"] > 0
