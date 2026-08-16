from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from backend.config import Settings
from backend.db import Database
from backend.models import (
    CopyFill,
    CopyLedger,
    CopyOrder,
    CopyPosition,
    CopySubscription,
    WatchedWallet,
    WhaleFill,
    WhaleFollowLedger,
    WhaleFollowPosition,
    WhaleOrder,
    WhaleRedemption,
)
from backend.trading import TradeResult
from backend.whale import WhaleFollowExecutor

CONDITION_ID = "0x" + "8" * 64
ASSET_ID = "whale-asset"
FUNDER_ADDRESS = "0x" + "f" * 40
TRACKED_ADDRESS = "0x" + "a" * 40
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
            source_wallet=TRACKED_ADDRESS,
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
            source_wallet=TRACKED_ADDRESS,
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


async def copy_table_counts(database: Database) -> tuple[int, int, int, int]:
    async with database.sessions() as session:
        return (
            int(await session.scalar(select(func.count(CopyPosition.id))) or 0),
            int(await session.scalar(select(func.count(CopyOrder.id))) or 0),
            int(await session.scalar(select(func.count(CopyFill.id))) or 0),
            int(await session.scalar(select(func.count(CopyLedger.id))) or 0),
        )


@pytest.mark.asyncio
async def test_buy_fill_updates_whale_ledger_once_and_leaves_copy_tables_untouched(
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
    before_copy = await copy_table_counts(database)
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
    assert await copy_table_counts(database) == before_copy == (0, 0, 0, 0)


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
    before_copy = await copy_table_counts(database)
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
    assert await copy_table_counts(database) == before_copy == (0, 0, 0, 0)


async def insert_copy_collision(database: Database) -> int:
    async with database.sessions() as session:
        wallet = WatchedWallet(
            address=TRACKED_ADDRESS,
            proxy_wallet=TRACKED_ADDRESS,
            label="Tracked copy wallet",
            wallet_role="tracked",
            enabled=True,
            baseline_established=True,
            status="ok",
            consecutive_failures=0,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(wallet)
        await session.flush()
        subscription = CopySubscription(
            tracked_wallet_id=wallet.id,
            state="active",
            strategy_mode="normal",
            copy_ratio_percent=Decimal("10"),
            position_cap_usdc=Decimal("20"),
            large_increase_threshold_usdc=Decimal("100"),
            base_entry_threshold_usdc=Decimal("100"),
            base_entry_ratio_percent=Decimal("10"),
            tier_one_threshold_usdc=Decimal("50000"),
            tier_one_ratio_percent=Decimal("0.1"),
            tier_two_threshold_usdc=Decimal("100000"),
            tier_two_ratio_percent=Decimal("0.2"),
            total_exposure_cap_usdc=Decimal("160"),
            market_slippage_cents=Decimal("5"),
            baseline_event_id=0,
            last_processed_event_id=0,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(subscription)
        await session.flush()
        position = CopyPosition(
            subscription_id=subscription.id,
            asset_id="copy-asset",
            condition_id=CONDITION_ID,
            title="Shared condition",
            outcome="No",
            outcome_index=1,
            neg_risk=False,
            event_slug="shared-event",
            settlement_date=None,
            cycle_no=1,
            attributed_size=Decimal("7"),
            attributed_cost=Decimal("3"),
            reserved_buy_usdc=ZERO,
            realized_pnl=ZERO,
            status="open",
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(position)
        await session.commit()
        return position.id


class FakeRedemptionTrader:
    def __init__(self) -> None:
        self.start_redemption_calls = 0

    async def onchain_redemption_payout_rate(
        self,
        condition_id: str,
        outcome_index: int | None,
        neg_risk: bool,
    ) -> Decimal | None:
        return Decimal("1")

    async def onchain_outcome_balance(self, asset_id: str) -> Decimal:
        return Decimal("10")

    async def start_redemption(self, *, condition_id: str, neg_risk: bool):
        self.start_redemption_calls += 1
        return SimpleNamespace(condition_id=condition_id, neg_risk=neg_risk)

    async def wait_redemption(self, prepared) -> str:
        return "0xshould-not-run"


@pytest.mark.asyncio
async def test_copy_position_collision_marks_whale_redemption_manual_review_without_submission(
    database: Database,
):
    whale_position_id = await insert_position(
        database,
        size="10",
        cost="4",
        status="open",
        lifetime_bought_size="10",
        lifetime_bought_usdc="4",
    )
    copy_position_id = await insert_copy_collision(database)
    before_copy = await copy_table_counts(database)
    fake_trader = FakeRedemptionTrader()
    follow_executor = executor(database)
    async with database.sessions() as session:
        whale_position = await session.get(WhaleFollowPosition, whale_position_id)
        assert whale_position is not None
    redeemable = SimpleNamespace(
        asset_id=ASSET_ID,
        size=Decimal("10"),
        current_price=Decimal("1"),
        current_value=Decimal("10"),
    )

    await follow_executor._process_condition_redemption(
        fake_trader,  # type: ignore[arg-type]
        account=SimpleNamespace(funder_address=FUNDER_ADDRESS),  # type: ignore[arg-type]
        condition_id=CONDITION_ID,
        positions=[whale_position],
        redeemable=[redeemable],
    )

    async with database.sessions() as session:
        whale_position_after = await session.get(WhaleFollowPosition, whale_position_id)
        redemption = await session.scalar(
            select(WhaleRedemption).where(WhaleRedemption.position_id == whale_position_id)
        )
        copy_position_after = await session.get(CopyPosition, copy_position_id)

    assert fake_trader.start_redemption_calls == 0
    assert whale_position_after is not None
    assert whale_position_after.status == "open"
    assert whale_position_after.size == Decimal("10")
    assert redemption is not None
    assert redemption.status == "manual_review"
    assert "自动策略归因仓位" in (redemption.last_error or "")
    assert copy_position_after is not None
    assert copy_position_after.attributed_size == Decimal("7")
    assert copy_position_after.attributed_cost == Decimal("3")
    assert copy_position_after.status == "open"
    assert await copy_table_counts(database) == before_copy == (1, 0, 0, 0)
