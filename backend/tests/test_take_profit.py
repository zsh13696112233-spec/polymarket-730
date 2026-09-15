from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.config import Settings
from backend.db import Database
from backend.main import create_app
from backend.models import (
    ExecutionAccount,
    TakeProfitExecution,
    TakeProfitPolicy,
    TakeProfitProtection,
    WhaleFollowLedger,
    WhaleFollowPosition,
    WhaleOrder,
)
from backend.polymarket import MarketResolution, OrderBookLevel, OrderBookSnapshot
from backend.take_profit import TakeProfitService, ensure_no_take_profit_rebuy
from backend.tests.conftest import position
from backend.tests.test_wallet_positions import WALLET, wallet_executor  # noqa: F401
from backend.time_utils import utcnow
from backend.trading import TradeFillResult, TradeResult, TradingUnavailable
from backend.whale import WhaleFollowExecutor

D = Decimal
ALEMBIC_CONFIG = str(Path("backend/alembic.ini").resolve())


@pytest.fixture
async def setup_profit(wallet_executor):  # noqa: F811
    executor, trader, client = wallet_executor
    executor.settings = replace(executor.settings, start_monitor=True)
    executor.take_profit.settings = executor.settings
    client.fetch_order_book.return_value = OrderBookSnapshot(
        asset_id="99",
        bids=(OrderBookLevel(D("0.92"), D("100")),),
        asks=(),
        tick_size=D("0.01"),
        min_order_size=D("5"),
        neg_risk=False,
    )
    client.fetch_market_resolutions = AsyncMock(return_value={})
    service = executor.take_profit
    await service.update(WALLET, True, D("90"))
    await service.protect(WALLET, "99", True)
    return service, executor, trader, client


def fill_result(size="10", amount="9.2", fee="0", trade_id="trade-1"):
    return TradeResult(
        status="filled" if size == "10" else "partially_filled",
        external_order_id="remote-1",
        filled_size=D(size),
        filled_usdc=D(amount),
        fee_usdc=D(fee),
        fills=(
            TradeFillResult(
                external_trade_id=trade_id,
                size=D(size),
                price=D(amount) / D(size),
                amount=D(amount),
                fee_usdc=D(fee),
                transaction_hash="0xtx",
                bucket_index=0,
                settlement_status="confirmed",
            ),
        ),
    )


async def sell(service, executor, trader, result=None):
    trader.submit_prepared_market.return_value = result or fill_result()
    return await executor.execute_wallet_sell(
        await service.quote("99"), "auto-test", auto_take_profit=True
    )


async def test_baseline_new_external_positions_and_reenable(setup_profit):
    service, executor, _, client = setup_profit
    await service.protect(WALLET, "99", False)
    client.fetch_active_positions.return_value.append(position(asset_id="100"))
    await service.tick()
    data = await service.read()
    assert {row["asset_id"]: row["enabled"] for row in data["protections"]} == {
        "99": False,
        "100": True,
    }
    await service.update(WALLET, False, D("91"))
    client.fetch_active_positions.return_value.append(position(asset_id="101"))
    await service.update(WALLET, True, D("91"))
    data = await service.read()
    assert next(row for row in data["protections"] if row["asset_id"] == "101")["enabled"] is False
    async with executor.database.sessions() as session:
        assert (await session.get(TakeProfitPolicy, WALLET)).threshold_percent == D("91")


async def test_activation_failure_preserves_baseline_and_off_state(setup_profit):
    service, executor, _, client = setup_profit
    await service.update(WALLET, False, D("90"))
    client.fetch_active_positions.side_effect = TradingUnavailable("snapshot incomplete")
    with pytest.raises(TradingUnavailable):
        await service.update(WALLET, True, D("95"))
    async with executor.database.sessions() as session:
        policy = await session.get(TakeProfitPolicy, WALLET)
        assert not policy.enabled and policy.threshold_percent == D("90")
        assert (await session.get(TakeProfitProtection, (WALLET, "99"))).present


async def test_threshold_uses_net_price_floor_and_depth(setup_profit):
    service, _, trader, client = setup_profit
    quote = await service.quote("99")
    assert quote["price"] == D("0.90")
    assert quote["estimated_proceeds"] == D("9")
    # A fee makes the 90-cent floor insufficient even when the displayed bid is 92.
    trader.sell_market.return_value.fee_rate = D("0.2")
    quote = await service.quote("99")
    assert quote["price"] == D("0.92")
    assert quote["estimated_proceeds"] - quote["estimated_fee"] >= D("9")
    client.fetch_order_book.return_value = replace(
        client.fetch_order_book.return_value,
        bids=(OrderBookLevel(D("0.92"), D("1")), OrderBookLevel(D("0.90"), D("99"))),
    )
    with pytest.raises(ValueError, match="深度不足"):
        await service.quote("99")


async def test_newer_bid_cannot_bypass_sell_slippage(setup_profit):
    service, _, _, client = setup_profit
    original = client.fetch_order_book.return_value
    client.fetch_order_book.side_effect = [
        original,
        replace(original, bids=(OrderBookLevel(D("0.99"), D("100")),)),
    ]
    assert (await service.quote("99"))["price"] == D("0.97")


async def test_inconsistent_external_cost_pauses_protection(setup_profit):
    service, _, _, client = setup_profit
    client.fetch_active_positions.return_value = [
        replace(position(asset_id="99"), initial_value=D("1"))
    ]
    with pytest.raises(ValueError, match="成本.*不一致"):
        await service.quote("99")


@pytest.mark.parametrize(
    "case", ["below", "unknown_cost", "loss", "disabled", "monitor", "trading", "protected"]
)
async def test_fail_closed_conditions(setup_profit, case):
    service, executor, trader, client = setup_profit
    if case == "below":
        client.fetch_order_book.return_value = replace(
            client.fetch_order_book.return_value, bids=(OrderBookLevel(D("0.89"), D("100")),)
        )
    elif case in {"unknown_cost", "loss"}:
        client.fetch_active_positions.return_value = [
            position(asset_id="99", avg_price="0" if case == "unknown_cost" else "0.95")
        ]
    elif case == "disabled":
        await service.update(WALLET, False, D("90"))
    elif case == "protected":
        await service.protect(WALLET, "99", False)
    elif case == "monitor":
        executor.settings = replace(executor.settings, start_monitor=False)
        service.settings = executor.settings
    else:
        executor.settings = replace(executor.settings, trading_enabled=False)
        service.settings = executor.settings
    with pytest.raises(ValueError):
        await service.quote("99")
    trader.submit_prepared_market.assert_not_awaited()


async def test_execution_rechecks_disabled_policy_and_depth(setup_profit):
    service, executor, trader, client = setup_profit
    quote = await service.quote("99")
    await service.update(WALLET, False, D("90"))
    with pytest.raises(ValueError, match="未开启"):
        await executor.execute_wallet_sell(quote, "disabled", auto_take_profit=True)
    await service.update(WALLET, True, D("90"))
    client.fetch_order_book.return_value = replace(
        client.fetch_order_book.return_value, bids=(OrderBookLevel(D("0.92"), D("1")),)
    )
    with pytest.raises(ValueError, match="深度不足"):
        await executor.execute_wallet_sell(quote, "thin", auto_take_profit=True)
    trader.submit_prepared_market.assert_not_awaited()


async def test_partial_fill_persists_snapshot_stats_and_rebuy_lock(setup_profit):
    service, executor, trader, client = setup_profit
    result = fill_result("6", "5.52", "0.02")
    order = await sell(service, executor, trader, result)
    assert order["status"] == "partially_filled"
    # Reconciliation replay cannot double count, and settings changes cannot rewrite history.
    await executor.apply_result(order["id"], result)
    await service.update(WALLET, True, D("95"))
    restarted = TakeProfitService(executor)
    data = await restarted.statistics()
    assert data["summary"]["filled_count"] == 1
    assert data["summary"]["realized_profit"] == D("3.10")
    assert data["summary"]["pending_resolution_count"] == 1
    assert data["summary"]["saved"] == 0
    assert data["items"][0]["threshold_percent"] == 90
    assert data["items"][0]["filled_size"] == 6
    async with executor.database.sessions() as session:
        with pytest.raises(ValueError, match="不再自动买回"):
            await ensure_no_take_profit_rebuy(session, WALLET, "99")
        await ensure_no_take_profit_rebuy(session, WALLET, "other-outcome")
        await ensure_no_take_profit_rebuy(session, "0x" + "b" * 40, "99")
    with pytest.raises(ValueError, match="不再自动买回"):
        await executor.execute_follow(
            SimpleNamespace(asset_id="99"), "rebuy", order_source="auto_follow"
        )
    client.fetch_active_positions.return_value = [position(asset_id="99", size="4")]
    trader.onchain_outcome_balance.return_value = D("4")
    trader.outcome_balance.return_value = D("4")
    with pytest.raises(ValueError, match="最小下单量"):
        await restarted.quote("99")


async def test_tracked_position_reuses_ledger_without_duplicate_profit(setup_profit):
    service, executor, trader, client = setup_profit
    now = utcnow()
    async with executor.database.sessions() as session:
        tracked = WhaleFollowPosition(
            asset_id="99",
            condition_id=client.fetch_active_positions.return_value[0].condition_id,
            title="测试市场",
            outcome="Yes",
            size=D("10"),
            cost_usdc=D("4.1"),
            status="open",
            cycle_no=1,
            created_at=now,
            updated_at=now,
        )
        session.add(tracked)
        await session.commit()
        position_id = tracked.id
    order = await sell(service, executor, trader)
    await executor.apply_result(order["id"], fill_result())
    async with executor.database.sessions() as session:
        assert (await session.get(WhaleFollowPosition, position_id)).status == "closed"
        ledger = list(await session.scalars(select(WhaleFollowLedger)))
        assert len(ledger) == 1
        assert ledger[0].source == "auto_take_profit"
        assert ledger[0].realized_pnl.quantize(D("0.000001")) == D("5.1")
    stats = await service.statistics()
    assert stats["summary"]["realized_profit"] == D("5.1")
    assert stats["items"][0]["cost_source"] == "ledger"


async def test_pending_manual_sell_blocks_automatic_sell(setup_profit):
    service, executor, trader, _ = setup_profit
    trader.submit_prepared_market.return_value = TradeResult(
        status="submitted", external_order_id="manual-1"
    )
    quote = await executor.quote_wallet_sell(
        "99", size=None, sell_all=True, order_type="FAK", price=None
    )
    await executor.execute_wallet_sell(quote, "manual")
    with pytest.raises(ValueError, match="已有卖出订单待对账"):
        await service.quote("99")
    async with executor.database.sessions() as session:
        assert await session.scalar(select(TakeProfitExecution)) is None
        assert (await session.scalar(select(WhaleOrder))).source == "wallet_manual"


@pytest.mark.parametrize(
    ("payout", "saved", "foregone"), [("0", "9.2", "0"), ("1", "0", "0.8"), ("0.5", "4.2", "0")]
)
async def test_final_resolution_after_exit_and_idempotent_statistics(
    setup_profit, payout, saved, foregone
):
    service, executor, trader, client = setup_profit
    await sell(service, executor, trader)
    condition = client.fetch_active_positions.return_value[0].condition_id
    client.fetch_active_positions.return_value = []
    client.fetch_market_resolutions.return_value = {
        condition: MarketResolution(condition, {"99": D(payout)}, utcnow())
    }
    await service.resolve()
    await service.resolve()
    data = await service.statistics()
    assert data["summary"]["saved"] == D(saved)
    assert data["summary"]["foregone"] == D(foregone)
    assert data["summary"]["net_impact"] == D(saved) - D(foregone)
    assert data["summary"]["pending_resolution_count"] == 0
    assert data["items"][0]["status"] == "settled"
    assert (await service.statistics(limit=1, offset=1))["items"] == []
    client.fetch_redeemable_positions.assert_not_awaited()


async def test_unknown_submission_keeps_reservation_across_restart(setup_profit):
    service, executor, trader, _ = setup_profit
    trader.submit_prepared_market.side_effect = TimeoutError()
    with pytest.raises(TradingUnavailable, match="待确认"):
        await sell(service, executor, trader)
    restarted = TakeProfitService(executor)
    with pytest.raises(ValueError, match="待对账"):
        await restarted.quote("99")
    async with executor.database.sessions() as session:
        assert await session.scalar(select(TakeProfitExecution)) is not None
        with pytest.raises(ValueError, match="正在自动止盈"):
            await ensure_no_take_profit_rebuy(session, WALLET, "99")
    assert (await restarted.statistics())["summary"]["pending_reconciliation_count"] == 1
    trader.submit_prepared_market.assert_awaited_once()


async def test_fee_metadata_failure_not_counted_as_zero_fee(setup_profit):
    service, executor, trader, _ = setup_profit
    trader.submit_prepared_market.side_effect = TradingUnavailable("fee metadata missing")
    with pytest.raises(TradingUnavailable):
        await sell(service, executor, trader)
    data = await service.statistics()
    assert data["summary"]["realized_profit"] == 0
    assert data["items"][0]["net_proceeds"] is None
    assert data["summary"]["pending_reconciliation_count"] == 1


async def test_switch_wallet_does_not_inherit_settings_or_stats(setup_profit):
    service, executor, trader, _ = setup_profit
    await sell(service, executor, trader)
    async with executor.database.sessions() as session:
        account = await session.get(ExecutionAccount, 1)
        account.funder_address = "0x" + "b" * 40
        await session.commit()
    assert (await service.read())["enabled"] is False
    assert (await service.statistics())["total"] == 0
    with pytest.raises(ValueError, match="钱包已变化"):
        await service.update(WALLET, True, D("90"))


async def test_reentrant_ticks_do_not_overlap(setup_profit):
    service, _, _, client = setup_profit
    await service._tick_lock.acquire()
    before = client.fetch_active_positions.await_count
    await service.tick()
    assert client.fetch_active_positions.await_count == before
    service._tick_lock.release()


@pytest.mark.parametrize(("rate", "saved", "foregone"), [("0", "64", "0"), ("1", "0", "6")])
async def test_user_example_40_cost_64_exit_70_maximum(setup_profit, rate, saved, foregone):
    service, executor, trader, client = setup_profit
    client.fetch_active_positions.return_value = [
        position(asset_id="99", size="70", avg_price="0.5714285714285714")
    ]
    trader.onchain_outcome_balance.return_value = D("70")
    trader.outcome_balance.return_value = D("70")
    result = replace(fill_result("70", "64"), status="filled")
    trader.submit_prepared_market.return_value = result
    order = await executor.execute_wallet_sell(
        await service.quote("99"), "example", auto_take_profit=True
    )
    assert order["status"] == "filled"
    condition = client.fetch_active_positions.return_value[0].condition_id
    client.fetch_market_resolutions.return_value = {
        condition: MarketResolution(condition, {"99": D(rate)}, utcnow())
    }
    await service.resolve()
    summary = (await service.statistics())["summary"]
    assert summary["realized_profit"] == D("24")
    assert summary["saved"] == D(saved)
    assert summary["foregone"] == D(foregone)


@pytest.mark.parametrize("upgrade_existing", [False, True])
async def test_migration_and_application_consumption(tmp_path, upgrade_existing):
    url = f"sqlite+aiosqlite:///{tmp_path / 'migration.db'}"
    config = Config(ALEMBIC_CONFIG)
    config.attributes["database_url"] = url
    if upgrade_existing:
        await asyncio.to_thread(command.upgrade, config, "0048_dual_match_amount")
    database = Database(Settings(database_url=url, start_monitor=False, trading_enabled=False))

    async def seed_account():
        now = utcnow()
        async with database.sessions() as session:
            session.add(
                ExecutionAccount(
                    id=1,
                    funder_address=WALLET,
                    budget_usdc=D("123"),
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

    if upgrade_existing:
        await seed_account()
    await database.initialize()
    try:
        if not upgrade_existing:
            await seed_account()
        async with database.sessions() as session:
            assert (await session.get(ExecutionAccount, 1)).budget_usdc == D("123")
        executor = WhaleFollowExecutor(
            database=database,
            settings=database.settings,
            client=SimpleNamespace(
                fetch_active_positions=AsyncMock(return_value=[position(asset_id="99")])
            ),
            keychain=SimpleNamespace(),
        )
        await executor.take_profit.update(WALLET, True, D("90"))
        await executor.take_profit.protect(WALLET, "99", True)
        read = await executor.take_profit.read()
        assert read["enabled"] and not read["running"]
        assert read["protections"][0]["enabled"]
        assert (await executor.take_profit.statistics())["total"] == 0
    finally:
        await database.close()


def test_api_contract_and_no_implicit_execution():
    public = SimpleNamespace(
        fetch_active_positions=AsyncMock(return_value=[position(asset_id="99")])
    )
    app = create_app(
        settings=Settings(
            database_url="sqlite+aiosqlite:///:memory:", start_monitor=False, trading_enabled=False
        ),
        client=public,
    )
    with TestClient(app) as client:
        root = "/api/execution-account/take-profit"
        initial = client.get(root)
        assert initial.status_code == 200
        assert initial.json()["wallet"] is None
        assert initial.json()["threshold_percent"] == "90"
        public.fetch_active_positions.assert_not_awaited()

        async def seed():
            async with app.state.database.sessions() as session:
                session.add(
                    ExecutionAccount(
                        id=1, funder_address=WALLET, created_at=utcnow(), updated_at=utcnow()
                    )
                )
                await session.commit()

        client.portal.call(seed)
        for threshold in ["0", "100", "NaN", "90.001"]:
            assert (
                client.put(
                    root, json={"wallet": WALLET, "enabled": True, "threshold_percent": threshold}
                ).status_code
                == 422
            )
        enabled = client.put(
            root, json={"wallet": WALLET, "enabled": True, "threshold_percent": "90"}
        )
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["enabled"] and not enabled.json()["running"]
        assert enabled.json()["protections"][0]["enabled"] is False
        result = client.put(
            "/api/execution-account/positions/99/take-profit",
            json={"wallet": WALLET, "enabled": True},
        )
        assert result.status_code == 200
        assert result.json()["protections"][0]["enabled"]
        result = client.get(root + "/statistics?limit=1&offset=0")
        assert result.status_code == 200
        assert result.json()["summary"]["saved"] == "0"
        assert result.json()["items"] == []
        assert client.get(root + "/statistics?limit=101").status_code == 422
        assert public.fetch_active_positions.await_count == 2
