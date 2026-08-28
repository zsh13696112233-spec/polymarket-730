from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.config import Settings
from backend.db import Database
from backend.models import (
    ExecutionAccount,
    WhaleFill,
    WhaleFollowLedger,
    WhaleFollowPosition,
    WhaleMarket,
    WhaleOrder,
)
from backend.polymarket import TradeSnapshot
from backend.trading import TradeResult
from backend.whale import WhaleFollowExecutor

CONDITION_ID = "0x" + "8" * 64
ASSET_ID = "whale-asset"
FUNDER_ADDRESS = "0x" + "f" * 40
SOURCE_WALLET = "0x" + "a" * 40
NOW = datetime(2026, 8, 16, 1, 2, 3)
ZERO = Decimal("0")


def assert_decimal(actual: Decimal, expected: str) -> None:
    assert actual.quantize(Decimal("0.000001")) == Decimal(expected)


@pytest.fixture
async def database() -> Database:
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        start_monitor=False,
    )
    database = Database(settings)
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


def executor(database: Database) -> WhaleFollowExecutor:
    return WhaleFollowExecutor(
        database=database,
        client=SimpleNamespace(),  # type: ignore[arg-type]
        settings=database.settings,
        keychain=SimpleNamespace(),  # type: ignore[arg-type]
    )


class ManualTradeClient:
    def __init__(self, trades: list[TradeSnapshot]) -> None:
        self.trades = trades

    async def fetch_trades(self, *_: object, **__: object) -> list[TradeSnapshot]:
        return list(self.trades)


class BalanceTrader:
    def __init__(self, balances: dict[str, Decimal]) -> None:
        self.balances = balances

    async def onchain_outcome_balance(self, asset_id: str) -> Decimal:
        return self.balances[asset_id]


class PendingOrderTrader:
    def __init__(self, result: TradeResult) -> None:
        self.result = result
        self.order_ids: list[str] = []

    async def order_status(self, external_order_id: str) -> TradeResult:
        self.order_ids.append(external_order_id)
        return self.result


async def configure_reconciliation(database: Database) -> None:
    async with database.sessions() as session:
        session.add(
            ExecutionAccount(
                id=1,
                signer_address=FUNDER_ADDRESS,
                funder_address=FUNDER_ADDRESS,
                signature_type=3,
                keychain_service="test-service",
                keychain_account="test-account",
                status="ready",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            WhaleMarket(
                condition_id=CONDITION_ID,
                title="Whale market",
                outcomes_json='["Yes","No"]',
                outcome_prices_json='["0.5","0.5"]',
                clob_token_ids_json=f'["{ASSET_ID}"]',
                tags_json="[]",
                fee_rate=Decimal("0.05"),
                fee_exponent=Decimal("1"),
                refreshed_at=NOW,
            )
        )
        await session.commit()


def reconciliation_executor(
    database: Database,
    *,
    trades: list[TradeSnapshot],
    balance: str,
) -> WhaleFollowExecutor:
    follow_executor = WhaleFollowExecutor(
        database=database,
        client=ManualTradeClient(trades),  # type: ignore[arg-type]
        settings=database.settings,
        keychain=SimpleNamespace(),  # type: ignore[arg-type]
    )
    follow_executor._trader_cache = BalanceTrader(  # type: ignore[assignment]
        {ASSET_ID: Decimal(balance)}
    )
    follow_executor._trader_cache_key = (
        "test-service",
        "test-account",
        3,
        FUNDER_ADDRESS,
    )
    return follow_executor


async def insert_position(
    database: Database,
    *,
    size: str,
    cost: str,
    status: str,
    lifetime_bought_size: str = "0",
    lifetime_bought_usdc: str = "0",
    lifetime_fee_usdc: str = "0",
) -> int:
    async with database.sessions() as session:
        position = WhaleFollowPosition(
            asset_id=ASSET_ID,
            condition_id=CONDITION_ID,
            title="Whale market",
            outcome="Yes",
            outcome_index=0,
            neg_risk=False,
            market_slug="whale-market",
            event_slug="whale-event",
            icon_url=None,
            source_wallet=SOURCE_WALLET,
            source_whale_avg_price=Decimal("0.35"),
            cycle_no=1,
            size=Decimal(size),
            cost_usdc=Decimal(cost),
            lifetime_bought_size=Decimal(lifetime_bought_size),
            lifetime_bought_usdc=Decimal(lifetime_bought_usdc),
            lifetime_sold_size=ZERO,
            lifetime_sold_usdc=ZERO,
            lifetime_fee_usdc=Decimal(lifetime_fee_usdc),
            realized_pnl=ZERO,
            status=status,
            opened_at=NOW if status == "open" else None,
            closed_at=None,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(position)
        await session.commit()
        return position.id


async def insert_order(
    database: Database,
    *,
    position_id: int,
    side: str,
    requested_size: str,
    requested_usdc: str,
    key: str,
) -> int:
    async with database.sessions() as session:
        order = WhaleOrder(
            position_id=position_id,
            entry_id=None,
            idempotency_key=key,
            source_wallet=SOURCE_WALLET,
            asset_id=ASSET_ID,
            condition_id=CONDITION_ID,
            title="Whale market",
            outcome="Yes",
            outcome_index=0,
            neg_risk=False,
            side=side,
            requested_size=Decimal(requested_size),
            requested_usdc=Decimal(requested_usdc),
            limit_price=Decimal("0.65"),
            reference_price=Decimal("0.60"),
            whale_avg_price=Decimal("0.35"),
            filled_size=ZERO,
            filled_usdc=ZERO,
            fee_usdc=ZERO,
            status="planned",
            reason=None,
            signed_order_hash=None,
            execution_provider="unified_sdk",
            external_order_id=None,
            external_trade_id=None,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(order)
        await session.commit()
        return order.id


@pytest.mark.asyncio
async def test_pending_sell_is_reconciled_before_conflict_retry(database: Database):
    await configure_reconciliation(database)
    position_id = await insert_position(
        database,
        size="10",
        cost="5",
        status="open",
        lifetime_bought_size="10",
        lifetime_bought_usdc="5",
    )
    order_id = await insert_order(
        database,
        position_id=position_id,
        side="SELL",
        requested_size="10",
        requested_usdc="6",
        key="whale:pending-conflict-exit",
    )
    async with database.sessions() as session:
        position = await session.get(WhaleFollowPosition, position_id)
        order = await session.get(WhaleOrder, order_id)
        assert position is not None and order is not None
        position.status = "closing"
        order.source = "conflict_exit"
        order.status = "submitted"
        order.external_order_id = "external-pending-order"
        await session.commit()

    trader = PendingOrderTrader(
        TradeResult(
            status="partially_filled",
            external_order_id="external-pending-order",
            filled_size=Decimal("4"),
            filled_usdc=Decimal("2.4"),
            average_price=Decimal("0.6"),
        )
    )
    follow_executor = executor(database)
    follow_executor._trader_cache = trader  # type: ignore[assignment]
    follow_executor._trader_cache_key = (
        "test-service",
        "test-account",
        3,
        FUNDER_ADDRESS,
    )

    assert await follow_executor.reconcile_pending_orders() is None
    assert trader.order_ids == ["external-pending-order"]
    async with database.sessions() as session:
        position = await session.get(WhaleFollowPosition, position_id)
        order = await session.get(WhaleOrder, order_id)
        ledger = list(
            await session.scalars(
                select(WhaleFollowLedger).where(WhaleFollowLedger.order_id == order_id)
            )
        )
    assert position is not None and position.size == Decimal("6")
    assert position.status == "open"
    assert order is not None and order.status == "partially_filled"
    assert len(ledger) == 1 and ledger[0].source == "conflict_exit"


@pytest.mark.asyncio
async def test_buy_fill_updates_whale_ledger_once(
    database: Database,
):
    position_id = await insert_position(database, size="0", cost="0", status="opening")
    order_id = await insert_order(
        database,
        position_id=position_id,
        side="BUY",
        requested_size="10",
        requested_usdc="4",
        key="whale:buy-once",
    )
    result = TradeResult(
        status="filled",
        external_order_id="buy-order",
        external_trade_id="buy-trade",
        filled_size=Decimal("10"),
        filled_usdc=Decimal("4"),
        average_price=Decimal("0.4"),
        fee_usdc=Decimal("0.1"),
        signed_order_hash="0xbuy-signed",
    )
    follow_executor = executor(database)

    await follow_executor.apply_result(order_id, result)
    await follow_executor.apply_result(order_id, result)

    async with database.sessions() as session:
        position = await session.get(WhaleFollowPosition, position_id)
        order = await session.get(WhaleOrder, order_id)
        fills = list(
            (await session.scalars(select(WhaleFill).where(WhaleFill.order_id == order_id))).all()
        )
        ledger = list(
            (
                await session.scalars(
                    select(WhaleFollowLedger).where(WhaleFollowLedger.position_id == position_id)
                )
            ).all()
        )

    assert position is not None
    assert position.size == Decimal("10")
    assert_decimal(position.cost_usdc, "4.1")
    assert position.lifetime_bought_size == Decimal("10")
    assert position.lifetime_bought_usdc == Decimal("4")
    assert_decimal(position.lifetime_fee_usdc, "0.1")
    assert position.realized_pnl == ZERO
    assert position.status == "open"
    assert order is not None
    assert order.status == "filled"
    assert order.filled_size == Decimal("10")
    assert order.filled_usdc == Decimal("4")
    assert_decimal(order.fee_usdc, "0.1")
    assert len(fills) == 1
    assert len(ledger) == 1
    assert ledger[0].type == "buy"
    assert_decimal(ledger[0].amount_usdc, "4.1")
    assert_decimal(ledger[0].fee_usdc, "0.1")


@pytest.mark.asyncio
async def test_partial_then_full_sell_moves_proportional_cost_and_closes_position(
    database: Database,
):
    position_id = await insert_position(
        database,
        size="10",
        cost="5",
        status="open",
        lifetime_bought_size="10",
        lifetime_bought_usdc="4.8",
        lifetime_fee_usdc="0.2",
    )
    partial_order_id = await insert_order(
        database,
        position_id=position_id,
        side="SELL",
        requested_size="4",
        requested_usdc="2.4",
        key="whale:sell-partial",
    )
    follow_executor = executor(database)

    await follow_executor.apply_result(
        partial_order_id,
        TradeResult(
            status="filled",
            external_order_id="sell-order-partial",
            external_trade_id="sell-trade-partial",
            filled_size=Decimal("4"),
            filled_usdc=Decimal("2.4"),
            average_price=Decimal("0.6"),
            fee_usdc=Decimal("0.1"),
        ),
    )

    async with database.sessions() as session:
        partial = await session.get(WhaleFollowPosition, position_id)
        assert partial is not None
        assert partial.size == Decimal("6")
        assert partial.cost_usdc == Decimal("3")
        assert_decimal(partial.realized_pnl, "0.3")
        assert partial.lifetime_sold_size == Decimal("4")
        assert_decimal(partial.lifetime_sold_usdc, "2.4")
        assert_decimal(partial.lifetime_fee_usdc, "0.3")
        assert partial.status == "open"

    full_order_id = await insert_order(
        database,
        position_id=position_id,
        side="SELL",
        requested_size="6",
        requested_usdc="3.6",
        key="whale:sell-full",
    )
    await follow_executor.apply_result(
        full_order_id,
        TradeResult(
            status="filled",
            external_order_id="sell-order-full",
            external_trade_id="sell-trade-full",
            filled_size=Decimal("6"),
            filled_usdc=Decimal("3.6"),
            average_price=Decimal("0.6"),
            fee_usdc=Decimal("0.15"),
        ),
    )

    async with database.sessions() as session:
        closed = await session.get(WhaleFollowPosition, position_id)
        ledger = list(
            (
                await session.scalars(
                    select(WhaleFollowLedger)
                    .where(WhaleFollowLedger.position_id == position_id)
                    .order_by(WhaleFollowLedger.id)
                )
            ).all()
        )

    assert closed is not None
    assert closed.size == ZERO
    assert closed.cost_usdc == ZERO
    assert closed.lifetime_sold_size == Decimal("10")
    assert_decimal(closed.lifetime_sold_usdc, "6")
    assert_decimal(closed.lifetime_fee_usdc, "0.45")
    assert_decimal(closed.realized_pnl, "0.75")
    assert closed.status == "closed"
    assert closed.closed_at is not None
    assert [entry.type for entry in ledger] == ["sell", "sell"]
    assert_decimal(ledger[0].realized_pnl, "0.3")
    assert_decimal(ledger[1].realized_pnl, "0.45")


@pytest.mark.asyncio
async def test_manual_buy_and_sell_are_adopted_and_dust_closes_position(
    database: Database,
):
    await configure_reconciliation(database)
    position_id = await insert_position(
        database,
        size="44.272726",
        cost="20.025429",
        status="open",
        lifetime_bought_size="44.272726",
        lifetime_bought_usdc="19.479999",
        lifetime_fee_usdc="0.545430",
    )
    trades = [
        TradeSnapshot(
            asset_id=ASSET_ID,
            condition_id=CONDITION_ID,
            side="BUY",
            size=Decimal("20"),
            price=Decimal("0.51"),
            timestamp=NOW - timedelta(seconds=12),
            transaction_hash="0xmanual-buy",
        ),
        TradeSnapshot(
            asset_id=ASSET_ID,
            condition_id=CONDITION_ID,
            side="SELL",
            size=Decimal("64.27"),
            price=Decimal("0.96"),
            timestamp=NOW + timedelta(hours=1),
            transaction_hash="0xmanual-sell",
        ),
    ]
    follow_executor = reconciliation_executor(database, trades=trades, balance="0.002726")

    assert await follow_executor.reconcile_external_wallet_activity() is None
    assert await follow_executor.reconcile_external_wallet_activity() is None

    async with database.sessions() as session:
        position = await session.get(WhaleFollowPosition, position_id)
        ledger = list(
            (
                await session.scalars(
                    select(WhaleFollowLedger)
                    .where(WhaleFollowLedger.position_id == position_id)
                    .order_by(WhaleFollowLedger.timestamp, WhaleFollowLedger.id)
                )
            ).all()
        )

    assert position is not None
    assert position.size == ZERO
    assert position.cost_usdc == ZERO
    assert position.status == "closed"
    assert [row.type for row in ledger] == ["buy", "sell", "dust_writeoff"]
    assert [row.source for row in ledger] == ["manual", "manual", "reconciliation"]
    assert_decimal(ledger[0].size, "20")
    assert_decimal(ledger[0].amount_usdc, "10.4499")
    assert_decimal(ledger[1].size, "64.27")
    assert_decimal(ledger[1].amount_usdc, "61.57581")
    assert_decimal(position.realized_pnl, "31.100481")


@pytest.mark.asyncio
async def test_manual_buy_after_follow_expands_the_unified_position_once(
    database: Database,
):
    await configure_reconciliation(database)
    position_id = await insert_position(
        database,
        size="10",
        cost="4",
        status="open",
        lifetime_bought_size="10",
        lifetime_bought_usdc="4",
    )
    trade = TradeSnapshot(
        asset_id=ASSET_ID,
        condition_id=CONDITION_ID,
        side="BUY",
        size=Decimal("5"),
        price=Decimal("0.50"),
        timestamp=NOW + timedelta(minutes=2),
        transaction_hash="0xmanual-later",
    )
    follow_executor = reconciliation_executor(database, trades=[trade], balance="15")

    assert await follow_executor.reconcile_external_wallet_activity() is None
    assert await follow_executor.reconcile_external_wallet_activity() is None

    async with database.sessions() as session:
        position = await session.get(WhaleFollowPosition, position_id)
        ledger = list(
            (
                await session.scalars(
                    select(WhaleFollowLedger).where(WhaleFollowLedger.position_id == position_id)
                )
            ).all()
        )

    assert position is not None
    assert position.size == Decimal("15")
    assert_decimal(position.cost_usdc, "6.5625")
    assert len(ledger) == 1
    assert ledger[0].source == "manual"
