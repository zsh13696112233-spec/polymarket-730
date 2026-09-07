from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from polymarket.models.clob.order_response import AcceptedOrder
from polymarket.models.clob.orders import SignedOrder
from pydantic import ValidationError

from backend.keychain import KeychainReference
from backend.schemas import ExecutionAccountUpdate
from backend.trading import (
    COLLATERAL_ADAPTER,
    CTF_ADDRESS,
    NEG_RISK_COLLATERAL_ADAPTER,
    V2_EXCHANGE_ADDRESS,
    V2_NEG_RISK_EXCHANGE_ADDRESS,
    MarketTradeRequest,
    TradeFillResult,
    TradingUnavailable,
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
        self.created: list[dict[str, object]] = []
        self.posted: list[SignedOrder] = []
        self.wallet = FUNDER
        self.signer = SIGNER
        self.wallet_type = "DEPOSIT_WALLET"
        self.closed = False
        self.neg_risk = False
        self.market = SimpleNamespace(
            condition_id=CONDITION,
            trading=SimpleNamespace(fees_enabled=False, fee_schedule=None),
        )

    async def get_order_book(self, *, token_id):
        assert token_id == "99"
        return SimpleNamespace(neg_risk=self.neg_risk)

    def list_markets(self, *, condition_ids, page_size):
        assert condition_ids == [self.market.condition_id]
        assert page_size == 1
        market = self.market

        class Paginator:
            async def first_page(self):
                return SimpleNamespace(items=(market,))

        return Paginator()

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


def test_execution_account_update_rejects_retired_proxy_wallet_type():
    with pytest.raises(ValidationError):
        ExecutionAccountUpdate(
            signer_address=SIGNER,
            funder_address=FUNDER,
            signature_type=1,
        )


def test_execution_account_defaults_to_platform_managed_redemption():
    payload = ExecutionAccountUpdate(
        signer_address=SIGNER,
        funder_address=FUNDER,
    )

    assert payload.auto_redeem is False


def test_polygon_rpc_bypasses_configured_proxy(monkeypatch):
    adapter = UnifiedPolymarketTrader(
        host="https://clob.test",
        keychain=SimpleNamespace(),
        key_reference=KeychainReference(service="test", account="test"),
        signature_type=3,
        funder_address=FUNDER,
        proxy_url="http://127.0.0.1:7897",
    )
    observed: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return b'{"jsonrpc":"2.0","id":1,"result":"0x89"}'

    class Opener:
        def open(self, request, timeout):
            observed["url"] = request.full_url
            observed["timeout"] = timeout
            return Response()

    def fake_build_opener(handler):
        observed["proxies"] = handler.proxies
        return Opener()

    monkeypatch.setattr("backend.trading.build_opener", fake_build_opener)

    assert adapter._rpc_sync("eth_chainId", []) == "0x89"
    assert observed == {
        "url": "https://polygon.drpc.org",
        "timeout": 15,
        "proxies": {},
    }


async def test_unified_trader_rejects_retired_proxy_wallet_type():
    adapter = UnifiedPolymarketTrader(
        host="https://clob.test",
        keychain=SimpleNamespace(),
        key_reference=KeychainReference(service="test", account="test"),
        signature_type=1,
        funder_address=FUNDER,
    )

    with pytest.raises(TradingUnavailable, match="仅允许 Deposit Wallet"):
        await adapter._build_client()


@pytest.mark.parametrize(
    ("side", "expected"),
    [
        ("BUY", {"amount", "max_spend", "max_price"}),
        ("SELL", {"shares", "min_price"}),
    ],
)
async def test_market_order_keeps_fak_budget_and_price_guard(side, expected):
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


async def test_signed_fingerprint_does_not_depend_on_full_signature():
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


async def test_accepted_order_persists_every_confirmed_fill():
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


async def test_accepted_order_without_initial_trade_ids_reconciles_associate_trades():
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


async def test_order_status_uses_confirmed_fills_instead_of_order_limit_price():
    client = FakeClient()

    async def get_order(*, order_id):
        assert order_id == "order-1"
        return SimpleNamespace(
            associate_trades=("trade-late",),
            size_matched=Decimal("28.185713"),
            price=Decimal("0.73"),
            original_size=Decimal("28.185713"),
            status="matched",
            side="BUY",
        )

    client.get_order = get_order
    adapter = trader(client)
    fill = TradeFillResult(
        "trade-late",
        Decimal("28.185713"),
        Decimal("0.6999999965"),
        Decimal("19.7299990013500045"),
        Decimal("0.29594"),
        "0xactual-fill",
        0,
        "confirmed",
    )

    async def confirmed_fills(ids):
        assert ids == ("trade-late",)
        return (fill,)

    adapter._confirmed_fills = confirmed_fills

    result = await adapter.order_status("order-1")

    assert result.status == "filled"
    assert result.filled_size == Decimal("28.185713")
    assert result.filled_usdc == Decimal("19.7299990013500045")
    assert result.average_price == Decimal("0.6999999965")
    assert result.fee_usdc == Decimal("0.29594")
    assert result.fills == (fill,)


async def test_order_status_waits_when_matched_fill_has_no_confirmed_transaction():
    client = FakeClient()

    async def get_order(*, order_id):
        assert order_id == "order-1"
        return SimpleNamespace(
            associate_trades=(),
            size_matched=Decimal("28.185713"),
            price=Decimal("0.73"),
            original_size=Decimal("28.185713"),
            status="matched",
            side="BUY",
        )

    client.get_order = get_order
    adapter = trader(client)

    result = await adapter.order_status("order-1")

    assert result.status == "reconciliation_pending"
    assert result.filled_size == Decimal("0")
    assert result.filled_usdc == Decimal("0")
    assert "confirmed fill" in (result.reason or "")


async def test_neg_risk_metadata_mismatch_fails_before_signing():
    client = FakeClient()
    client.neg_risk = True
    adapter = trader(client)

    with pytest.raises(Exception, match="Neg Risk"):
        await adapter.prepare_market(
            MarketTradeRequest("99", "BUY", Decimal("1"), Decimal("0.55"), False)
        )
    assert client.created == []


async def test_taker_fee_uses_public_market_fee_schedule():
    client = FakeClient()
    client.market = SimpleNamespace(
        condition_id=CONDITION,
        trading=SimpleNamespace(
            fees_enabled=True,
            fee_schedule=SimpleNamespace(rate=Decimal("0.04"), exponent=1),
        ),
    )
    adapter = trader(client)

    fee = await adapter._fee_for_trade(
        SimpleNamespace(
            id="trade-1",
            trader_side="TAKER",
            condition_id=CONDITION,
            price=Decimal("0.50"),
            size=Decimal("10"),
        )
    )

    assert fee == Decimal("0.10000")


async def test_taker_fee_fails_closed_when_public_schedule_is_missing():
    client = FakeClient()
    client.market = SimpleNamespace(
        condition_id=CONDITION,
        trading=SimpleNamespace(fees_enabled=True, fee_schedule=None),
    )
    adapter = trader(client)

    with pytest.raises(TradingUnavailable, match="缺少手续费参数"):
        await adapter._fee_for_trade(
            SimpleNamespace(
                id="trade-1",
                trader_side="TAKER",
                condition_id=CONDITION,
                price=Decimal("0.50"),
                size=Decimal("10"),
            )
        )


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


async def test_limit_sell_uses_sdk_gtc_and_separate_submission():
    from dataclasses import replace
    from unittest.mock import AsyncMock

    client = FakeClient()
    client.create_limit_order = AsyncMock(
        return_value=replace(
            signed_order(),
            side="SELL",
            order_type="GTC",
            maker_amount=10_000_000,
            taker_amount=6_000_000,
        )
    )
    sdk = trader(client)
    request = MarketTradeRequest(
        asset_id="99", side="SELL", amount=Decimal("10"), worst_price=Decimal("0.6")
    )
    prepared = await sdk.prepare_limit(request)
    assert client.posted == []
    client.create_limit_order.assert_awaited_once_with(
        token_id="99",
        side="SELL",
        price=Decimal("0.6"),
        size=Decimal("10"),
        post_only=False,
    )
    result = await sdk.submit_prepared_limit(prepared)
    assert result.external_order_id == "order-1"
    assert result.status == "submitted"
    assert len(client.posted) == 1


@pytest.mark.parametrize(
    "upstream,matched,confirmed,expected",
    [
        ("LIVE", "4", "4", "partially_filled_live"),
        ("CANCELED", "4", "4", "cancelled"),
        ("MATCHED", "10", "10", "filled"),
        ("CANCELED", "4", "2", "reconciliation_pending"),
    ],
)
async def test_gtc_status_keeps_unfilled_and_unsettled_shares_reserved(
    upstream,
    matched,
    confirmed,
    expected,
):
    from unittest.mock import AsyncMock

    client = FakeClient()
    client.get_order = AsyncMock(
        return_value=SimpleNamespace(
            original_size="10",
            size_matched=matched,
            status=upstream,
            associate_trades=("trade",),
        )
    )
    sdk = trader(client)
    sdk._confirmed_fills = AsyncMock(
        return_value=(
            TradeFillResult(
                external_trade_id="trade",
                size=Decimal(confirmed),
                price=Decimal("0.6"),
                amount=Decimal(confirmed) * Decimal("0.6"),
                fee_usdc=Decimal("0"),
                transaction_hash="0xtx",
                bucket_index=0,
                settlement_status="confirmed",
            ),
        )
    )
    result = await sdk.order_status("order-1", order_type="GTC")
    assert result.status == expected
    assert result.filled_size == Decimal(confirmed)


async def test_cancel_requires_sdk_acknowledgement():
    from unittest.mock import AsyncMock

    client = FakeClient()
    client.cancel_order = AsyncMock(return_value=SimpleNamespace(canceled=(), not_canceled={}))
    sdk = trader(client)
    with pytest.raises(TradingUnavailable, match="未确认撤单"):
        await sdk.cancel_confirmed("order-1")
    client.cancel_order.return_value = SimpleNamespace(canceled=("order-1",), not_canceled={})
    await sdk.cancel_confirmed("order-1")


@pytest.mark.parametrize("empty", [False, True])
async def test_open_orders_reads_every_sdk_page(empty):
    from polymarket.pagination import AsyncPaginator, Page

    cursors = []

    async def fetch(cursor):
        cursors.append(cursor)
        if empty:
            return Page(items=(), has_more=False)
        if cursor is None:
            return Page(items=("order-1", "order-2"), has_more=True, next_cursor="next")
        return Page(items=("order-3",), has_more=False)

    client = FakeClient()
    client.list_open_orders = lambda: AsyncPaginator(fetch)
    assert await trader(client).open_orders() == (
        [] if empty else ["order-1", "order-2", "order-3"]
    )
    assert cursors == ([None] if empty else [None, "next"])


@pytest.mark.parametrize(
    "fees,closed,valid", [(False, False, True), (None, False, False), (False, True, False)]
)
async def test_wallet_market_metadata_fails_closed(fees, closed, valid):
    from unittest.mock import AsyncMock

    client = FakeClient()
    market = SimpleNamespace(
        condition_id=CONDITION,
        outcomes=SimpleNamespace(
            yes=SimpleNamespace(token_id="99"), no=SimpleNamespace(token_id="100")
        ),
        state=SimpleNamespace(closed=closed, active=True, accepting_orders=True, neg_risk=False),
        trading=SimpleNamespace(fees_enabled=fees, fee_schedule=None),
    )
    client.list_markets = lambda **kwargs: SimpleNamespace(
        first_page=AsyncMock(return_value=SimpleNamespace(items=[market]))
    )
    sdk = trader(client)
    if valid:
        assert await sdk.sell_market(CONDITION, "99") is market
    else:
        with pytest.raises(TradingUnavailable, match="资料不完整"):
            await sdk.sell_market(CONDITION, "99")
