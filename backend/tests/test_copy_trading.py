from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from backend.copy_trading import (
    RiskUsage,
    allowed_buy_usdc,
    is_temperature_bucket,
    market_worst_price,
    tolerated_price,
)
from backend.models import CopySubscription
from backend.monitor import utcnow
from backend.polymarket import OrderBookLevel, OrderBookSnapshot, TradeSnapshot
from backend.tests.conftest import position
from backend.trading import (
    MarketTradeRequest,
    OfficialClobTrader,
    TradeRequest,
    simulate_limit_order,
    simulate_market_order,
)

MY_ADDRESS = "0x1111111111111111111111111111111111111111"
TRACKED_ADDRESS = "0x2222222222222222222222222222222222222222"


def move_fast_baseline_to_past(client, subscription_id: int) -> None:
    async def move() -> None:
        async with client.app.state.database.sessions() as session:
            subscription = await session.get(CopySubscription, subscription_id)
            assert subscription is not None
            baseline = utcnow() - timedelta(seconds=1)
            subscription.fast_poll_started_at = baseline
            subscription.last_trade_poll_at = baseline
            await session.commit()

    assert client.portal is not None
    client.portal.call(move)


def test_temperature_scope_accepts_all_highest_and_lowest_temperature_buckets():
    title = "Highest temperature in New York City on August 8?"
    assert is_temperature_bucket(title, "73°F or below")
    assert is_temperature_bucket(title, "86°F or higher")
    assert is_temperature_bucket(title, "80–81°F")
    assert is_temperature_bucket(
        "Will the highest temperature in Milan be 33°C on August 2?",
        "Yes",
    )
    assert is_temperature_bucket(
        "Will the lowest temperature in London be between 12-13°C on August 3?",
        "Yes",
    )
    assert not is_temperature_bucket("Will BTC exceed $100k?", "$100k or higher")


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


def test_market_worst_price_uses_current_quote_and_configured_cents():
    assert market_worst_price(
        Decimal("0.47"), Decimal("0.01"), Decimal("0"), side="BUY"
    ) == Decimal("0.47")
    assert market_worst_price(
        Decimal("0.47"), Decimal("0.01"), Decimal("5"), side="BUY"
    ) == Decimal("0.52")
    assert market_worst_price(
        Decimal("0.48"), Decimal("0.01"), Decimal("5"), side="SELL"
    ) == Decimal("0.43")
    assert market_worst_price(
        Decimal("0.98"), Decimal("0.01"), Decimal("50"), side="BUY"
    ) == Decimal("0.99")
    assert market_worst_price(
        Decimal("0.02"), Decimal("0.01"), Decimal("50"), side="SELL"
    ) == Decimal("0.01")
    assert market_worst_price(
        Decimal("0.998"), Decimal("0.001"), Decimal("50"), side="BUY"
    ) == Decimal("0.99")
    assert market_worst_price(
        Decimal("0.002"), Decimal("0.001"), Decimal("50"), side="SELL"
    ) == Decimal("0.01")


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


def test_paper_fak_market_order_is_terminal_after_partial_fill():
    book = OrderBookSnapshot(
        asset_id="asset-1",
        bids=(),
        asks=(
            OrderBookLevel(Decimal("0.47"), Decimal("5")),
            OrderBookLevel(Decimal("0.60"), Decimal("50")),
        ),
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        neg_risk=False,
    )
    result = simulate_market_order(
        MarketTradeRequest(
            asset_id="asset-1",
            side="BUY",
            amount=Decimal("6"),
            worst_price=Decimal("0.52"),
        ),
        book,
    )
    assert result.status == "partially_filled"
    assert result.filled_size == Decimal("5")
    assert result.filled_usdc == Decimal("2.35")


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


def test_official_trader_posts_fak_market_order():
    calls: dict[str, object] = {}

    class FakeClient:
        def create_market_order(self, args, options):
            calls["args"] = args
            calls["options"] = options
            return "signed-order"

        def post_order(self, signed, order_type):
            calls["signed"] = signed
            calls["order_type"] = order_type
            return {
                "success": True,
                "orderID": "order-1",
                "makingAmount": "6",
                "takingAmount": "12.5",
                "status": "matched",
            }

        def get_order(self, order_id):
            raise AssertionError(f"成交回包已经明确，不应再次查询订单：{order_id}")

    trader = OfficialClobTrader(
        host="https://clob.example.test",
        keychain=SimpleNamespace(),
        key_reference=SimpleNamespace(),
        signature_type=0,
        funder_address=None,
    )
    trader._client = FakeClient()  # type: ignore[assignment]
    result = trader._submit_market_sync(
        MarketTradeRequest(
            asset_id="asset-1",
            side="BUY",
            amount=Decimal("6"),
            worst_price=Decimal("0.52"),
            neg_risk=True,
        )
    )
    args = calls["args"]
    assert args.amount == 6.0
    assert args.price == 0.52
    assert str(calls["order_type"]) == "FAK"
    assert result.status == "filled"
    assert result.filled_size == Decimal("12.5")
    assert result.filled_usdc == Decimal("6")
    assert result.average_price == Decimal("0.48")


def test_official_trader_treats_definite_fak_rejection_as_terminal():
    class FakeClient:
        def create_market_order(self, args, options):
            return "signed-order"

        def post_order(self, signed, order_type):
            return {"success": False, "errorMsg": "no match within protection price"}

    trader = OfficialClobTrader(
        host="https://clob.example.test",
        keychain=SimpleNamespace(),
        key_reference=SimpleNamespace(),
        signature_type=0,
        funder_address=None,
    )
    trader._client = FakeClient()  # type: ignore[assignment]
    result = trader._submit_market_sync(
        MarketTradeRequest(
            asset_id="asset-1",
            side="BUY",
            amount=Decimal("6"),
            worst_price=Decimal("0.52"),
        )
    )
    assert result.status == "unfilled"
    assert result.filled_size == 0
    assert result.reason == "no match within protection price"


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
    assert subscription["market_slippage_cents"] == 5.0

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


def test_activate_and_resume_establish_new_trade_baselines(app_client_factory):
    client, fake = app_client_factory([[], []])
    tracked = client.post(
        "/api/wallets", json={"address": TRACKED_ADDRESS, "label": "jjavi"}
    ).json()
    subscription = client.post(
        "/api/copy-trading/subscriptions", json={"tracked_wallet_id": tracked["id"]}
    ).json()
    common = {
        "asset_id": "asset-baseline",
        "condition_id": "0x" + "2" * 64,
        "side": "BUY",
        "size": Decimal("100"),
        "price": Decimal("0.40"),
        "title": "Will the highest temperature in Rome be 35°C on August 3?",
        "outcome": "Yes",
        "outcome_index": 0,
        "event_slug": "highest-temperature-in-rome-on-august-3-2026",
    }
    fake.trades = [
        TradeSnapshot(
            **common,
            timestamp=utcnow() - timedelta(seconds=10),
            transaction_hash="0xbefore-activate",
        )
    ]
    client.post(
        f"/api/copy-trading/subscriptions/{subscription['id']}/action",
        json={"action": "activate"},
    )
    assert client.portal is not None
    client.portal.call(client.app.state.copy_engine.process_fast_trades, subscription["id"])
    before_pause = client.get(
        "/api/copy-trading/dashboard", params={"tracked_wallet_id": tracked["id"]}
    ).json()
    assert before_pause["signals"] == []
    assert before_pause["orders"] == []

    client.post(
        f"/api/copy-trading/subscriptions/{subscription['id']}/action",
        json={"action": "pause"},
    )
    fake.trades.append(
        TradeSnapshot(
            **common,
            timestamp=utcnow(),
            transaction_hash="0xwhile-paused",
        )
    )
    client.post(
        f"/api/copy-trading/subscriptions/{subscription['id']}/action",
        json={"action": "resume"},
    )
    client.portal.call(client.app.state.copy_engine.process_fast_trades, subscription["id"])
    after_resume = client.get(
        "/api/copy-trading/dashboard", params={"tracked_wallet_id": tracked["id"]}
    ).json()
    assert after_resume["signals"] == []
    assert after_resume["orders"] == []


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


def test_fast_paper_engine_buys_at_current_ask_not_leader_average(app_client_factory):
    client, fake = app_client_factory([[]])
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
    move_fast_baseline_to_past(client, subscription["id"])
    traded_at = utcnow()
    fake.trades = [
        TradeSnapshot(
            asset_id="asset-fast",
            condition_id="0x" + "3" * 64,
            side="BUY",
            size=Decimal("857.142857142857142857"),
            price=Decimal("0.35"),
            timestamp=traded_at,
            transaction_hash="0xbuy",
            title="Will the highest temperature in Milan be 36°C on August 3?",
            outcome="Yes",
            outcome_index=0,
            event_slug="highest-temperature-in-milan-on-august-3-2026",
        )
    ]

    async def fetch_order_book(_: str) -> OrderBookSnapshot:
        return OrderBookSnapshot(
            asset_id="asset-fast",
            bids=(OrderBookLevel(Decimal("0.46"), Decimal("1000")),),
            asks=(OrderBookLevel(Decimal("0.47"), Decimal("1000")),),
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            neg_risk=False,
        )

    fake.fetch_order_book = fetch_order_book  # type: ignore[attr-defined]
    assert client.portal is not None
    client.portal.call(client.app.state.copy_engine.process_fast_trades, subscription["id"])

    dashboard = client.get(
        "/api/copy-trading/dashboard",
        params={"tracked_wallet_id": tracked["id"]},
    ).json()
    assert len(dashboard["orders"]) == 1
    assert dashboard["orders"][0]["status"] == "filled"
    assert abs(Decimal(str(dashboard["orders"][0]["requested_usdc"])) - Decimal("6")) < Decimal(
        "0.000001"
    )
    assert Decimal(str(dashboard["orders"][0]["reference_price"])) == Decimal("0.47")
    assert Decimal(str(dashboard["orders"][0]["limit_price"])) == Decimal("0.52")
    assert dashboard["orders"][0]["order_type"] == "FAK"
    assert dashboard["orders"][0]["expires_at"] is None
    assert dashboard["signals"][0]["status"] == "followed"
    position = dashboard["positions"][0]
    assert abs(Decimal(str(position["average_entry_price"])) - Decimal("0.47")) < Decimal(
        "0.000001"
    )
    assert Decimal(str(position["current_bid"])) == Decimal("0.46")
    assert abs(Decimal(str(position["current_value"])) - Decimal("5.87234")) < Decimal("0.00001")
    assert Decimal(str(position["unrealized_pnl"])) < 0
    assert Decimal(str(position["lifetime_bought_usdc"])) == Decimal("6")
    assert position["valuation_status"] == "ok"
    assert dashboard["portfolio"]["valuation_complete"] is True
    assert dashboard["portfolio"]["unpriced_positions"] == 0

    client.portal.call(client.app.state.copy_engine.process_fast_trades, subscription["id"])
    repeated = client.get(
        "/api/copy-trading/dashboard",
        params={"tracked_wallet_id": tracked["id"]},
    ).json()
    assert len(repeated["orders"]) == 1
    assert len(repeated["signals"]) == 1

    async def unavailable_order_book(_: str) -> OrderBookSnapshot:
        raise RuntimeError("temporary quote failure")

    fake.fetch_order_book = unavailable_order_book  # type: ignore[attr-defined]
    unavailable = client.get(
        "/api/copy-trading/dashboard",
        params={"tracked_wallet_id": tracked["id"]},
    ).json()
    assert unavailable["positions"][0]["valuation_status"] == "unavailable"
    assert unavailable["positions"][0]["current_bid"] is None
    assert unavailable["portfolio"]["valuation_complete"] is False
    assert unavailable["portfolio"]["market_value_usdc"] is None
    assert unavailable["portfolio"]["total_pnl"] is None


def test_fast_buy_uses_precise_gamma_end_time_instead_of_date_only_midnight(
    app_client_factory,
):
    asset_id = "asset-precise-end"
    market_slug = f"market-{asset_id}"
    snapshot = replace(
        position(
            asset_id=asset_id,
            title="Will the highest temperature in New York City be between 82-83°F on August 2?",
        ),
        end_date=utcnow().replace(hour=0, minute=0, second=0, microsecond=0),
    )
    client, fake = app_client_factory([[snapshot], []])
    tracked = client.post(
        "/api/wallets", json={"address": TRACKED_ADDRESS, "label": "jjavi"}
    ).json()
    assert client.portal is not None
    client.portal.call(client.app.state.monitor.sync_wallet, tracked["id"])
    subscription = client.post(
        "/api/copy-trading/subscriptions", json={"tracked_wallet_id": tracked["id"]}
    ).json()
    client.post(
        f"/api/copy-trading/subscriptions/{subscription['id']}/action",
        json={"action": "activate"},
    )
    move_fast_baseline_to_past(client, subscription["id"])
    fake.market_end_dates[market_slug] = utcnow() + timedelta(hours=2)
    fake.trades = [
        TradeSnapshot(
            asset_id=asset_id,
            condition_id=snapshot.condition_id,
            side="BUY",
            size=Decimal("857.142857142857142857"),
            price=Decimal("0.35"),
            timestamp=utcnow(),
            transaction_hash="0xprecise-end",
            title=snapshot.title,
            outcome="Yes",
            outcome_index=0,
            event_slug=snapshot.event_slug,
            market_slug=market_slug,
        )
    ]

    async def fetch_order_book(_: str) -> OrderBookSnapshot:
        return OrderBookSnapshot(
            asset_id=asset_id,
            bids=(OrderBookLevel(Decimal("0.46"), Decimal("1000")),),
            asks=(OrderBookLevel(Decimal("0.47"), Decimal("1000")),),
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            neg_risk=True,
        )

    fake.fetch_order_book = fetch_order_book  # type: ignore[attr-defined]
    client.portal.call(client.app.state.copy_engine.process_fast_trades, subscription["id"])
    dashboard = client.get(
        "/api/copy-trading/dashboard", params={"tracked_wallet_id": tracked["id"]}
    ).json()
    assert len(dashboard["orders"]) == 1
    assert dashboard["orders"][0]["status"] == "filled"
    assert dashboard["signals"][0]["status"] == "followed"


def test_same_poll_round_trip_is_netted_without_orders(app_client_factory):
    client, fake = app_client_factory([[]])
    tracked = client.post(
        "/api/wallets", json={"address": TRACKED_ADDRESS, "label": "jjavi"}
    ).json()
    subscription = client.post(
        "/api/copy-trading/subscriptions", json={"tracked_wallet_id": tracked["id"]}
    ).json()
    client.post(
        f"/api/copy-trading/subscriptions/{subscription['id']}/action",
        json={"action": "activate"},
    )
    move_fast_baseline_to_past(client, subscription["id"])
    now = utcnow()
    common = {
        "asset_id": "asset-round-trip",
        "condition_id": "0x" + "4" * 64,
        "title": "Will the highest temperature in Paris be 36°C on August 3?",
        "outcome": "Yes",
        "outcome_index": 0,
        "event_slug": "highest-temperature-in-paris-on-august-3-2026",
    }
    fake.trades = [
        TradeSnapshot(
            **common,
            side="BUY",
            size=Decimal("100"),
            price=Decimal("0.35"),
            timestamp=now,
            transaction_hash="0xround-buy",
        ),
        TradeSnapshot(
            **common,
            side="SELL",
            size=Decimal("100"),
            price=Decimal("0.70"),
            timestamp=now,
            transaction_hash="0xround-sell",
        ),
    ]
    assert client.portal is not None
    client.portal.call(client.app.state.copy_engine.process_fast_trades, subscription["id"])
    dashboard = client.get(
        "/api/copy-trading/dashboard", params={"tracked_wallet_id": tracked["id"]}
    ).json()
    assert dashboard["orders"] == []
    assert {signal["status"] for signal in dashboard["signals"]} == {"netted"}


def test_fast_engine_sells_same_fraction_as_leader(app_client_factory):
    client, fake = app_client_factory([[]])
    tracked = client.post(
        "/api/wallets", json={"address": TRACKED_ADDRESS, "label": "jjavi"}
    ).json()
    subscription = client.post(
        "/api/copy-trading/subscriptions", json={"tracked_wallet_id": tracked["id"]}
    ).json()
    client.post(
        f"/api/copy-trading/subscriptions/{subscription['id']}/action",
        json={"action": "activate"},
    )
    move_fast_baseline_to_past(client, subscription["id"])
    now = utcnow()
    common = {
        "asset_id": "asset-sell",
        "condition_id": "0x" + "5" * 64,
        "title": "Will the lowest temperature in London be 17°C on August 3?",
        "outcome": "Yes",
        "outcome_index": 0,
        "event_slug": "lowest-temperature-in-london-on-august-3-2026",
    }
    buy = TradeSnapshot(
        **common,
        side="BUY",
        size=Decimal("600"),
        price=Decimal("0.50"),
        timestamp=now,
        transaction_hash="0xbuy-sell-test",
    )
    fake.trades = [buy]
    current_book = {
        "value": OrderBookSnapshot(
            asset_id="asset-sell",
            bids=(OrderBookLevel(Decimal("0.49"), Decimal("1000")),),
            asks=(OrderBookLevel(Decimal("0.50"), Decimal("1000")),),
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            neg_risk=False,
        )
    }

    async def fetch_order_book(_: str) -> OrderBookSnapshot:
        return current_book["value"]

    fake.fetch_order_book = fetch_order_book  # type: ignore[attr-defined]
    assert client.portal is not None
    client.portal.call(client.app.state.copy_engine.process_fast_trades, subscription["id"])

    fake.trades.append(
        TradeSnapshot(
            **common,
            side="SELL",
            size=Decimal("300"),
            price=Decimal("0.70"),
            timestamp=utcnow(),
            transaction_hash="0xsell-half",
        )
    )
    current_book["value"] = OrderBookSnapshot(
        asset_id="asset-sell",
        bids=(OrderBookLevel(Decimal("0.69"), Decimal("1000")),),
        asks=(OrderBookLevel(Decimal("0.70"), Decimal("1000")),),
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        neg_risk=False,
    )
    client.portal.call(client.app.state.copy_engine.process_fast_trades, subscription["id"])
    dashboard = client.get(
        "/api/copy-trading/dashboard", params={"tracked_wallet_id": tracked["id"]}
    ).json()
    assert [order["side"] for order in dashboard["orders"]] == ["SELL", "BUY"]
    assert Decimal(str(dashboard["positions"][0]["attributed_size"])) == Decimal("6")
    assert Decimal(str(dashboard["positions"][0]["attributed_cost"])) == Decimal("3")
    assert Decimal(str(dashboard["positions"][0]["current_value"])) == Decimal("4.14")
    assert Decimal(str(dashboard["positions"][0]["realized_pnl"])) == Decimal("1.14")
    assert Decimal(str(dashboard["positions"][0]["unrealized_pnl"])) == Decimal("1.14")
    assert Decimal(str(dashboard["positions"][0]["total_pnl"])) == Decimal("2.28")
    assert Decimal(str(dashboard["positions"][0]["lifetime_bought_usdc"])) == Decimal("6")
    assert Decimal(str(dashboard["positions"][0]["lifetime_sold_usdc"])) == Decimal("4.14")
    assert dashboard["signals"][0]["status"] == "followed"

    fake.trades.append(
        TradeSnapshot(
            **common,
            side="SELL",
            size=Decimal("300"),
            price=Decimal("0.80"),
            timestamp=utcnow(),
            transaction_hash="0xsell-rest",
        )
    )
    current_book["value"] = OrderBookSnapshot(
        asset_id="asset-sell",
        bids=(OrderBookLevel(Decimal("0.80"), Decimal("1000")),),
        asks=(OrderBookLevel(Decimal("0.81"), Decimal("1000")),),
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        neg_risk=False,
    )
    client.portal.call(client.app.state.copy_engine.process_fast_trades, subscription["id"])
    quote_calls = {"count": 0}

    async def should_not_quote_closed(_: str) -> OrderBookSnapshot:
        quote_calls["count"] += 1
        raise AssertionError("closed history must not request an order book")

    fake.fetch_order_book = should_not_quote_closed  # type: ignore[attr-defined]
    history = client.get(
        "/api/copy-trading/dashboard", params={"tracked_wallet_id": tracked["id"]}
    ).json()
    assert quote_calls["count"] == 0
    assert Decimal(str(history["positions"][0]["attributed_size"])) == 0
    assert history["positions"][0]["status"] == "closed"
    assert Decimal(str(history["positions"][0]["lifetime_bought_usdc"])) == Decimal("6")
    assert Decimal(str(history["positions"][0]["lifetime_sold_usdc"])) == Decimal("8.94")
    assert Decimal(str(history["positions"][0]["realized_pnl"])) == Decimal("2.94")
    assert Decimal(str(history["positions"][0]["total_pnl"])) == Decimal("2.94")
    assert Decimal(str(history["portfolio"]["open_cost_usdc"])) == 0
    assert Decimal(str(history["portfolio"]["market_value_usdc"])) == 0
    assert Decimal(str(history["portfolio"]["total_pnl"])) == Decimal("2.94")
