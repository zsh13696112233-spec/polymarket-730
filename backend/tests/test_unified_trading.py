from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from polymarket.models.clob.order_response import AcceptedOrder
from polymarket.models.clob.orders import SignedOrder

from backend.keychain import KeychainReference
from backend.trading import (
    COLLATERAL_ADAPTER,
    CTF_ADDRESS,
    NEG_RISK_COLLATERAL_ADAPTER,
    V2_EXCHANGE_ADDRESS,
    V2_NEG_RISK_EXCHANGE_ADDRESS,
    MarketTradeRequest,
    TradeFillResult,
    UnifiedPolymarketTrader,
)

FUNDER = "0x3333333333333333333333333333333333333333"
SIGNER = "0x4444444444444444444444444444444444444444"
CONDITION = "0x" + "8" * 64


def signed_order(signature: str = "0x1234") -> SignedOrder:
    return SignedOrder(
        builder="0x" + "0" * 64,
        expiration=0,
        maker=FUNDER,
        maker_amount=1_000_000,
        metadata="0x" + "0" * 64,
        order_type="FAK",
        salt=7,
        side="BUY",
        signature=signature,
        signature_type=3,
        signer=SIGNER,
        taker_amount=2_000_000,
        timestamp=123,
        token_id="99",
    )


class FakeClient:
    def __init__(self) -> None:
        self._ctx = object()
        self.created: list[dict[str, object]] = []
        self.posted: list[SignedOrder] = []
        self.wallet = FUNDER
        self.signer = SIGNER
        self.wallet_type = "DEPOSIT_WALLET"
        self.closed = False

    async def create_market_order(self, **kwargs):
        self.created.append(kwargs)
        return signed_order()

    async def post_order(self, order):
        self.posted.append(order)
        return SimpleNamespace(
            ok=True,
            order_id="order-1",
            status="matched",
            trade_ids=("trade-1", "trade-2"),
        )

    async def wait_for_order_fill_settlement(self, response):
        return ("0xtx1", "0xtx2")

    async def close(self):
        self.closed = True


def trader(client: FakeClient) -> UnifiedPolymarketTrader:
    result = UnifiedPolymarketTrader(
        host="https://clob.test",
        keychain=SimpleNamespace(),
        key_reference=KeychainReference(service="test", account="test"),
        signature_type=3,
        funder_address=FUNDER,
    )
    result._client = client
    return result


@pytest.mark.parametrize(
    ("side", "expected"),
    [
        ("BUY", {"amount", "max_spend", "max_price"}),
        ("SELL", {"shares", "min_price"}),
    ],
)
async def test_market_order_keeps_fak_budget_and_price_guard(monkeypatch, side, expected):
    async def fetch_neg_risk(ctx, *, token_id):
        return False

    monkeypatch.setattr(
        "polymarket._internal.actions.orders.market_data.fetch_neg_risk", fetch_neg_risk
    )
    client = FakeClient()
    adapter = trader(client)
    prepared = await adapter.prepare_market(
        MarketTradeRequest(
            asset_id="99",
            side=side,
            amount=Decimal("1"),
            worst_price=Decimal("0.55"),
        )
    )

    assert client.created[0]["order_type"] == "FAK"
    assert expected <= client.created[0].keys()
    assert "builder_code" not in client.created[0]
    assert prepared.signed_order_hash.startswith("0x")


async def test_signed_fingerprint_does_not_depend_on_full_signature(monkeypatch):
    async def fetch_neg_risk(ctx, *, token_id):
        return False

    monkeypatch.setattr(
        "polymarket._internal.actions.orders.market_data.fetch_neg_risk", fetch_neg_risk
    )
    client = FakeClient()
    signatures = iter((signed_order("0xaaaa"), signed_order("0xbbbb")))

    async def create_market_order(**kwargs):
        return next(signatures)

    client.create_market_order = create_market_order
    adapter = trader(client)
    request = MarketTradeRequest("99", "BUY", Decimal("1"), Decimal("0.55"))

    first = await adapter.prepare_market(request)
    second = await adapter.prepare_market(request)

    assert first.signed_order_hash == second.signed_order_hash
    assert "aaaa" not in first.signed_order_hash


async def test_accepted_order_persists_every_confirmed_fill(monkeypatch):
    async def fetch_neg_risk(ctx, *, token_id):
        return False

    monkeypatch.setattr(
        "polymarket._internal.actions.orders.market_data.fetch_neg_risk", fetch_neg_risk
    )
    client = FakeClient()
    adapter = trader(client)
    fills = (
        TradeFillResult(
            "trade-1",
            Decimal("1"),
            Decimal("0.50"),
            Decimal("0.50"),
            Decimal("0.01"),
            "0xtx1",
            1,
            "confirmed",
        ),
        TradeFillResult(
            "trade-2",
            Decimal("1"),
            Decimal("0.50"),
            Decimal("0.50"),
            Decimal("0.02"),
            "0xtx2",
            2,
            "confirmed",
        ),
    )

    async def confirmed_fills(ids):
        assert ids == ("trade-1", "trade-2")
        return fills

    adapter._confirmed_fills = confirmed_fills
    prepared = await adapter.prepare_market(
        MarketTradeRequest("99", "BUY", Decimal("1"), Decimal("0.55"))
    )
    result = await adapter.submit_prepared_market(prepared)

    assert result.status == "filled"
    assert result.external_trade_ids == ("trade-1", "trade-2")
    assert result.filled_size == Decimal("2")
    assert result.fee_usdc == Decimal("0.03")
    assert result.fills == fills


async def test_accepted_order_without_initial_trade_ids_reconciles_associate_trades(monkeypatch):
    async def fetch_neg_risk(ctx, *, token_id):
        return False

    monkeypatch.setattr(
        "polymarket._internal.actions.orders.market_data.fetch_neg_risk", fetch_neg_risk
    )
    client = FakeClient()

    async def post_order(order):
        return AcceptedOrder(
            order_id="order-1",
            status="matched",
            making_amount=Decimal("1"),
            taking_amount=Decimal("9"),
            trade_ids=(),
            transactions_hashes=(),
        )

    async def get_order(*, order_id):
        assert order_id == "order-1"
        return SimpleNamespace(associate_trades=("trade-late",))

    client.post_order = post_order
    client.get_order = get_order
    adapter = trader(client)
    fill = TradeFillResult(
        "trade-late",
        Decimal("9"),
        Decimal("0.11"),
        Decimal("0.99"),
        Decimal("0"),
        "0xtx",
        0,
        "confirmed",
    )

    async def confirmed_fills(ids):
        assert ids == ("trade-late",)
        return (fill,)

    adapter._confirmed_fills = confirmed_fills
    prepared = await adapter.prepare_market(
        MarketTradeRequest("99", "BUY", Decimal("1"), Decimal("0.16"))
    )
    result = await adapter.submit_prepared_market(prepared)

    assert result.external_trade_ids == ("trade-late",)
    assert result.fills == (fill,)
    assert result.status == "filled"


async def test_neg_risk_metadata_mismatch_fails_before_signing(monkeypatch):
    async def fetch_neg_risk(ctx, *, token_id):
        return True

    monkeypatch.setattr(
        "polymarket._internal.actions.orders.market_data.fetch_neg_risk", fetch_neg_risk
    )
    client = FakeClient()
    adapter = trader(client)

    with pytest.raises(Exception, match="Neg Risk"):
        await adapter.prepare_market(
            MarketTradeRequest("99", "BUY", Decimal("1"), Decimal("0.55"), False)
        )
    assert client.created == []


async def test_redemption_returns_handle_before_wait(monkeypatch):
    class Handle:
        transaction_id = "relay-1"
        transaction_hash = None

        async def wait(self):
            return SimpleNamespace(transaction_hash="0xredeemed")

    client = FakeClient()

    async def redeem_positions(*, condition_id):
        assert condition_id == CONDITION
        return Handle()

    client.redeem_positions = redeem_positions
    adapter = trader(client)

    async def approved(token, operator):
        return True

    adapter._is_approved_for_all = approved
    prepared = await adapter.start_redemption(condition_id=CONDITION, neg_risk=False)

    assert prepared.transaction_id == "relay-1"
    assert prepared.transaction_hash is None
    assert await adapter.wait_redemption(prepared) == "0xredeemed"


async def test_neg_risk_redemption_checks_ctf_approval():
    client = FakeClient()

    async def redeem_positions(*, condition_id):
        return SimpleNamespace(transaction_id="relay-1", transaction_hash=None)

    client.redeem_positions = redeem_positions
    adapter = trader(client)
    checked: list[tuple[str, str]] = []

    async def approved(token, operator):
        checked.append((token, operator))
        return True

    adapter._is_approved_for_all = approved
    await adapter.start_redemption(condition_id=CONDITION, neg_risk=True)

    assert checked == [(CTF_ADDRESS, NEG_RISK_COLLATERAL_ADAPTER)]


async def test_redemption_falls_back_to_direct_sdk_call_when_closed_market_lookup_misses():
    class Handle:
        transaction_id = "relay-fallback"
        transaction_hash = None

    client = FakeClient()
    executed = []

    async def redeem_positions(*, condition_id):
        raise ValueError(f"No market found for condition {condition_id}")

    async def execute_transaction(*, calls, metadata):
        executed.append((calls, metadata))
        return Handle()

    client.redeem_positions = redeem_positions
    client.execute_transaction = execute_transaction
    adapter = trader(client)

    async def approved(token, operator):
        return True

    adapter._is_approved_for_all = approved
    prepared = await adapter.start_redemption(condition_id=CONDITION, neg_risk=False)

    assert prepared.transaction_id == "relay-fallback"
    assert len(executed) == 1
    calls, metadata = executed[0]
    assert len(calls) == 1
    assert calls[0].to.lower() == COLLATERAL_ADAPTER.lower()
    assert CONDITION.removeprefix("0x") in calls[0].data
    assert CONDITION in metadata


async def test_ready_approvals_check_ctf_for_both_exchanges():
    client = FakeClient()

    async def get_balance_allowance(*, asset_type):
        assert asset_type == "COLLATERAL"
        return SimpleNamespace(
            allowances={
                V2_EXCHANGE_ADDRESS: 1,
                V2_NEG_RISK_EXCHANGE_ADDRESS: 1,
            }
        )

    client.get_balance_allowance = get_balance_allowance
    adapter = trader(client)
    checked: list[tuple[str, str]] = []

    async def approved(token, operator):
        checked.append((token, operator))
        return True

    adapter._is_approved_for_all = approved
    await adapter.ensure_ready_approvals()

    assert checked == [
        (CTF_ADDRESS, V2_EXCHANGE_ADDRESS),
        (CTF_ADDRESS, V2_NEG_RISK_EXCHANGE_ADDRESS),
    ]


async def test_neg_risk_sell_repairs_ctf_approval():
    client = FakeClient()
    calls = 0
    approvals: list[tuple[str, str]] = []

    async def get_balance_allowance(*, asset_type, token_id):
        nonlocal calls
        assert asset_type == "CONDITIONAL"
        assert token_id == "99"
        calls += 1
        return SimpleNamespace(
            balance=2_000_000,
            allowances={V2_NEG_RISK_EXCHANGE_ADDRESS: 0 if calls == 1 else 2_000_000},
        )

    class Approval:
        async def wait(self):
            return None

    async def approve_erc1155_for_all(*, token_address, operator_address, metadata):
        approvals.append((token_address, operator_address))
        return Approval()

    client.get_balance_allowance = get_balance_allowance
    client.approve_erc1155_for_all = approve_erc1155_for_all
    adapter = trader(client)

    repaired = await adapter._repair_missing_order_approval(
        MarketTradeRequest("99", "SELL", Decimal("1"), Decimal("0.50"), neg_risk=True)
    )

    assert repaired is True
    assert approvals == [(CTF_ADDRESS, V2_NEG_RISK_EXCHANGE_ADDRESS)]


async def test_cached_client_is_closed_explicitly():
    client = FakeClient()
    adapter = trader(client)

    await adapter.close()

    assert client.closed is True
    assert adapter._client is None
