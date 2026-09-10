from __future__ import annotations

import asyncio
import sqlite3
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from backend.config import Settings
from backend.db import Database
from backend.models import ExecutionAccount, WhaleFill, WhaleFollowPosition, WhaleOrder
from backend.tests.conftest import position
from backend.time_utils import utcnow
from backend.trading import TradeFillResult, TradeResult, TradingUnavailable
from backend.whale import WhaleFollowExecutor

D = Decimal
WALLET = "0x" + "a" * 40
ALEMBIC_CONFIG = str(Path("backend/alembic.ini").resolve())


@pytest.fixture
async def wallet_executor():
    database = Database(
        Settings(
            database_url="sqlite+aiosqlite:///:memory:", start_monitor=False, trading_enabled=True
        )
    )
    await database.initialize()
    now = utcnow()
    async with database.sessions() as session:
        session.add(
            ExecutionAccount(
                id=1,
                funder_address=WALLET,
                signer_address=WALLET,
                signature_type=3,
                keychain_service="test",
                keychain_account="test",
                status="ready",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    client = SimpleNamespace(
        fetch_active_positions=AsyncMock(return_value=[position(asset_id="99")]),
        fetch_redeemable_positions=AsyncMock(return_value=[]),
        fetch_order_book=AsyncMock(
            return_value=SimpleNamespace(
                best_bid=D("0.5"), tick_size=D("0.01"), min_order_size=D("5")
            )
        ),
    )
    trader = SimpleNamespace(
        onchain_outcome_balance=AsyncMock(return_value=D("10")),
        outcome_balance=AsyncMock(return_value=D("10")),
        open_orders=AsyncMock(return_value=[]),
        sell_market=AsyncMock(
            return_value=SimpleNamespace(
                state=SimpleNamespace(neg_risk=False), trading=SimpleNamespace(fee_schedule=None)
            )
        ),
        prepare_limit=AsyncMock(
            return_value=SimpleNamespace(signed_order_hash="hash", external_order_id="remote-1")
        ),
        prepare_market=AsyncMock(
            return_value=SimpleNamespace(signed_order_hash="hash", external_order_id="remote-1")
        ),
        submit_prepared_limit=AsyncMock(
            return_value=TradeResult(status="submitted", external_order_id="remote-1")
        ),
        submit_prepared_market=AsyncMock(
            return_value=TradeResult(status="unfilled", external_order_id="remote-1")
        ),
        order_status=AsyncMock(
            return_value=TradeResult(status="live", external_order_id="remote-1")
        ),
        cancel_confirmed=AsyncMock(),
        trade_fee=AsyncMock(return_value=D("0")),
    )
    executor = WhaleFollowExecutor(
        database=database, client=client, settings=database.settings, keychain=SimpleNamespace()
    )
    executor._trader = AsyncMock(return_value=trader)
    try:
        yield executor, trader, client
    finally:
        await database.close()


async def quote(executor, **overrides):
    values = dict(size=None, sell_all=True, order_type="GTC", price=D("0.6"))
    values.update(overrides)
    return await executor.quote_wallet_sell("99", **values)


def remote_order(size="2", id="external"):
    return SimpleNamespace(
        id=id,
        token_id="99",
        maker_address=WALLET,
        side="SELL",
        original_size=D(size),
        size_matched=D("0"),
        outcome="Yes",
        order_type="GTC",
        price=D("0.6"),
        status="live",
    )


async def test_external_holdings_and_reservations(wallet_executor):
    executor, trader, _ = wallet_executor
    trader.open_orders.return_value = [remote_order()]
    data = await executor.wallet_positions()
    assert data["positions"][0]["available_size"] == D("8")
    assert data["orders"][0]["can_cancel"] is False
    assert (await quote(executor))["size"] == D("8")
    with pytest.raises(ValueError, match="可卖份额"):
        await quote(executor, size=D("9"), sell_all=False)


async def test_gtc_persists_without_creating_follow_position_and_deduplicates_occupancy(
    wallet_executor,
):
    executor, trader, _ = wallet_executor
    preview = await quote(executor)
    order = await executor.execute_wallet_sell(preview, "once")
    assert order["status"] == "submitted"
    assert order["can_cancel"]
    trader.open_orders.return_value = [remote_order("10", "remote-1")]
    data = await executor.wallet_positions()
    assert data["positions"][0]["reserved_size"] == D("10")
    async with executor.database.sessions() as session:
        saved = await session.get(WhaleOrder, order["id"])
        assert saved.execution_wallet == WALLET
        assert saved.order_type == "GTC"
        assert saved.position_id is None
        assert await session.scalar(select(WhaleFollowPosition)) is None
    with pytest.raises(ValueError):
        await executor.execute_wallet_sell(preview, "once")
    trader.submit_prepared_limit.assert_awaited_once()


@pytest.mark.parametrize("change", ["balance", "tick", "account", "reservation"])
async def test_changed_preview_fails_closed(wallet_executor, change):
    executor, trader, client = wallet_executor
    preview = await quote(executor)
    if change == "balance":
        trader.onchain_outcome_balance.return_value = D("9")
    elif change == "tick":
        client.fetch_order_book.return_value.tick_size = D("0.1")
    elif change == "account":
        async with executor.database.sessions() as session:
            account = await session.get(ExecutionAccount, 1)
            account.funder_address = "0x" + "b" * 40
            await session.commit()
    else:
        trader.open_orders.return_value = [remote_order()]
    with pytest.raises(ValueError):
        await executor.execute_wallet_sell(preview, change)
    trader.submit_prepared_limit.assert_not_awaited()


@pytest.mark.parametrize(
    "values",
    [
        dict(price=D("0.605")),
        dict(size=D("1"), sell_all=False),
        dict(size=D("5.001"), sell_all=False),
    ],
)
async def test_invalid_order_rules(wallet_executor, values):
    executor, _, _ = wallet_executor
    with pytest.raises(ValueError):
        await quote(executor, **values)


async def test_market_sell_no_bids_and_unknown_cost(wallet_executor):
    executor, _, client = wallet_executor
    client.fetch_active_positions.return_value = [position(asset_id="99", avg_price="0")]
    assert (await quote(executor))["estimated_pnl"] is None
    client.fetch_order_book.return_value.best_bid = None
    with pytest.raises(ValueError, match="买盘"):
        await quote(executor, order_type="FAK", price=None)
    assert (await quote(executor))["price"] == D("0.6")


async def test_uncertain_submission_reserves_and_never_retries(wallet_executor):
    executor, trader, _ = wallet_executor
    preview = await quote(executor)
    trader.submit_prepared_limit.side_effect = TradingUnavailable("network")
    with pytest.raises(TradingUnavailable, match="待确认"):
        await executor.execute_wallet_sell(preview, "timeout")
    data = await executor.wallet_positions()
    assert data["positions"][0]["available_size"] == 0
    assert data["orders"][0]["status"] == "reconciliation_pending"
    with pytest.raises(ValueError):
        await executor.execute_wallet_sell(preview, "another-token")
    trader.submit_prepared_limit.assert_awaited_once()


async def test_signing_failure_releases_reservation(wallet_executor):
    executor, trader, _ = wallet_executor
    trader.prepare_limit.side_effect = TradingUnavailable("signing")
    with pytest.raises(TradingUnavailable):
        await executor.execute_wallet_sell(await quote(executor), "sign")
    assert (await executor.wallet_positions())["positions"][0]["available_size"] == D("10")


async def test_restart_reconciles_gtc_partial_and_cancel_race(wallet_executor):
    executor, trader, _ = wallet_executor
    order = await executor.execute_wallet_sell(await quote(executor), "partial")
    fill = TradeFillResult(
        external_trade_id="trade",
        size=D("4"),
        price=D("0.6"),
        amount=D("2.4"),
        fee_usdc=D("0.01"),
        transaction_hash="0xtx",
        bucket_index=0,
        settlement_status="confirmed",
    )
    result = TradeResult(
        status="partially_filled_live",
        external_order_id="remote-1",
        filled_size=D("4"),
        filled_usdc=D("2.4"),
        fee_usdc=D("0.01"),
        fills=(fill,),
    )
    trader.order_status.return_value = result
    restarted = WhaleFollowExecutor(
        database=executor.database,
        client=executor.client,
        settings=executor.settings,
        keychain=SimpleNamespace(),
    )
    restarted._trader = AsyncMock(return_value=trader)
    assert await restarted.reconcile_pending_orders() is None
    assert await restarted.reconcile_pending_orders() is None
    async with executor.database.sessions() as session:
        assert len((await session.scalars(select(WhaleFill))).all()) == 1
        assert (await session.get(WhaleOrder, order["id"])).status == "partially_filled_live"
    preview = await restarted.quote_wallet_cancel(order["id"])
    trader.cancel_confirmed.side_effect = TradingUnavailable("cancel timeout")
    with pytest.raises(TradingUnavailable):
        await restarted.execute_wallet_cancel(preview)
    async with executor.database.sessions() as session:
        assert (await session.get(WhaleOrder, order["id"])).status == "reconciliation_pending"
    trader.cancel_confirmed.side_effect = None
    trader.order_status.return_value = TradeResult(
        status="cancelled",
        external_order_id="remote-1",
        filled_size=D("4"),
        filled_usdc=D("2.4"),
        fee_usdc=D("0.01"),
        fills=(fill,),
    )
    result = await restarted.execute_wallet_cancel(preview)
    assert result["status"] == "cancelled"
    assert result["filled_size"] == D("4")
    assert not result["can_cancel"]


async def test_trading_disabled_and_settled_positions(wallet_executor):
    executor, _, client = wallet_executor
    client.fetch_active_positions.return_value = []
    client.fetch_redeemable_positions.return_value = [position(asset_id="99")]
    data = await executor.wallet_positions()
    assert data["positions"][0]["status"] == "settled"
    assert data["positions"][0]["available_size"] == 0
    with pytest.raises(ValueError):
        await quote(executor)
    executor.settings = Settings(trading_enabled=False, start_monitor=False)
    with pytest.raises(ValueError, match="停用"):
        await quote(executor)


def test_wallet_api_confirmation_and_decimal_contract(app_client_factory):
    client, _ = app_client_factory([[]])
    executor = client.app.state.whale_executor
    preview = dict(
        wallet=WALLET,
        asset_id="99",
        title="Market",
        outcome="Yes",
        order_type="FAK",
        size=D("10"),
        price=D("0.6"),
        estimated_proceeds=D("6"),
        estimated_fee=D("0"),
        estimated_pnl=None,
    )
    executor.quote_wallet_sell = AsyncMock(return_value=preview)
    order = dict(
        id=1,
        external_order_id="upstream",
        asset_id="99",
        title="Market",
        outcome="Yes",
        order_type="FAK",
        price=D("0.6"),
        size=D("10"),
        filled_size=D("0"),
        status="submitted",
        can_cancel=True,
    )
    executor.execute_wallet_sell = AsyncMock(return_value=order)
    root = "/api/execution-account/positions/99/sell"
    response = client.post(root + "/preview", json={"sell_all": True, "order_type": "FAK"})
    assert response.status_code == 200
    assert response.json()["price"] == "0.6"
    token = response.json()["confirmation_id"]
    assert (
        client.post(
            root + "/execute", json={"confirmation_id": token, "confirmation_text": "wrong"}
        ).status_code
        == 422
    )
    payload = {"confirmation_id": token, "confirmation_text": "确认真实卖出"}
    assert client.post(root + "/execute", json=payload).status_code == 200
    assert client.post(root + "/execute", json=payload).status_code == 409
    executor.execute_wallet_sell.assert_awaited_once()
    token = client.post(root + "/preview", json={"sell_all": True}).json()["confirmation_id"]
    client.app.state.wallet_previews[token]["expires_at"] = utcnow() - timedelta(seconds=1)
    assert (
        client.post(root + "/execute", json={**payload, "confirmation_id": token}).status_code
        == 409
    )
    assert (
        client.post(root + "/preview", json={"sell_all": True, "order_type": "GTC"}).status_code
        == 422
    )


def test_cancel_api_requires_correct_order_and_text(app_client_factory):
    client, _ = app_client_factory([[]])
    executor = client.app.state.whale_executor
    order = dict(
        id=1,
        external_order_id="remote",
        asset_id="99",
        title="Market",
        outcome="Yes",
        order_type="GTC",
        price=D("0.6"),
        size=D("10"),
        filled_size=D("0"),
        status="live",
        can_cancel=True,
    )
    executor.quote_wallet_cancel = AsyncMock(return_value={"wallet": WALLET, "order": order})
    executor.execute_wallet_cancel = AsyncMock(return_value={**order, "status": "cancelled"})
    root = "/api/execution-account/orders/1/cancel"
    token = client.post(root + "/preview").json()["confirmation_id"]
    payload = {"confirmation_id": token, "confirmation_text": "确认撤单"}
    assert (
        client.post(root + "/execute", json={**payload, "confirmation_text": ""}).status_code == 422
    )
    assert client.post(root + "/execute", json=payload).status_code == 200
    assert client.post(root + "/execute", json=payload).status_code == 409
    token = client.post(root + "/preview").json()["confirmation_id"]
    assert (
        client.post(
            "/api/execution-account/orders/2/cancel/execute",
            json={**payload, "confirmation_id": token},
        ).status_code
        == 409
    )
    executor.execute_wallet_cancel.assert_awaited_once()


async def test_migration_upgrade_preserves_old_order(tmp_path):
    path = tmp_path / "upgrade.db"
    config = Config(ALEMBIC_CONFIG)
    config.attributes["database_url"] = f"sqlite+aiosqlite:///{path}"
    await asyncio.to_thread(command.upgrade, config, "0042_whale_scan_audit")
    with sqlite3.connect(path) as connection:
        connection.execute("""INSERT INTO whale_orders
            (idempotency_key, source, asset_id, condition_id, title, outcome, neg_risk, side,
             requested_size, requested_usdc, limit_price, filled_size, filled_usdc, fee_usdc,
             status, execution_provider, created_at, updated_at)
            VALUES ('old','follow','99','condition','Market','Yes',0,'SELL',10,6,.6,0,0,0,
                    'submitted','unified_sdk',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
    await asyncio.to_thread(command.upgrade, config, "head")
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT order_type, execution_wallet, requested_size FROM whale_orders"
        ).fetchone() == ("FAK", None, 10)


async def test_legacy_sell_respects_wallet_gtc_reservation(wallet_executor):
    executor, trader, _ = wallet_executor
    await executor.execute_wallet_sell(await quote(executor), "reserve")
    with pytest.raises(ValueError, match="可卖份额"):
        await executor.execute_sell(SimpleNamespace(asset_id="99", size=D("5")), "legacy")
    trader.submit_prepared_market.assert_not_awaited()


async def test_gtc_follow_ledger_does_not_write_off_live_remainder(wallet_executor):
    from backend.models import WhaleFollowLedger
    from backend.schemas import WhaleLedgerRead

    executor, _, _ = wallet_executor
    now = utcnow()
    async with executor.database.sessions() as session:
        tracked = WhaleFollowPosition(
            asset_id="99",
            condition_id="0x" + "1" * 64,
            title="测试市场",
            outcome="Yes",
            size=D("10"),
            cost_usdc=D("4"),
            status="open",
            cycle_no=1,
            created_at=now,
            updated_at=now,
        )
        session.add(tracked)
        await session.commit()
        position_id = tracked.id
    order = await executor.execute_wallet_sell(await quote(executor), "tracked")
    fill = TradeFillResult(
        external_trade_id="fill",
        size=D("9.99"),
        price=D("0.6"),
        amount=D("5.994"),
        fee_usdc=D("0"),
        transaction_hash="0xtx",
        bucket_index=0,
        settlement_status="confirmed",
    )
    result = TradeResult(
        status="partially_filled_live",
        external_order_id="remote-1",
        filled_size=D("9.99"),
        filled_usdc=D("5.994"),
        fills=(fill,),
    )
    await executor.apply_result(order["id"], result)
    await executor.apply_result(order["id"], result)
    assert not await executor.write_off_terminal_sell_remainder(position_id)
    async with executor.database.sessions() as session:
        tracked = await session.get(WhaleFollowPosition, position_id)
        assert tracked.size.quantize(D("0.01")) == D("0.01")
        ledger = list((await session.scalars(select(WhaleFollowLedger))).all())
        assert len(ledger) == 1
        assert ledger[0].source == "wallet_manual"
        WhaleLedgerRead.model_validate(ledger[0])


def test_wallet_decimal_response_never_uses_exponents():
    from backend.schemas import WalletPositionRead

    response = WalletPositionRead(
        asset_id="99",
        title="Market",
        outcome="Yes",
        size=D("1E-8"),
        reserved_size=D("0E-18"),
        available_size=D("1E-8"),
        price=None,
        market_value=None,
        cost=None,
        pnl=None,
        status="open",
    ).model_dump(mode="json")
    assert response["size"] == "0.00000001"
    assert response["reserved_size"] == "0.000000000000000000"


@pytest.mark.parametrize("has_orders", [False, True])
async def test_wallet_positions_consumes_sdk_items_not_pages(wallet_executor, has_orders):
    from polymarket.pagination import AsyncPaginator, Page

    from backend.tests.test_unified_trading import trader as sdk_trader

    executor, wallet_trader, _ = wallet_executor
    cursors = []

    async def fetch(cursor):
        cursors.append(cursor)
        if not has_orders:
            return Page(items=(), has_more=False)
        if cursor is None:
            return Page(items=(remote_order("2", "one"),), has_more=True, next_cursor="next")
        return Page(items=(remote_order("3", "two"),), has_more=False)

    sdk = sdk_trader(SimpleNamespace(list_open_orders=lambda: AsyncPaginator(fetch)))
    wallet_trader.open_orders = sdk.open_orders
    result = await executor.wallet_positions()
    assert result["positions"][0]["available_size"] == D("5" if has_orders else "10")
    assert len(result["orders"]) == (2 if has_orders else 0)
    assert cursors == ([None, "next"] if has_orders else [None])


@pytest.mark.parametrize(
    "payload",
    [
        {"sell_all": True, "order_type": "GTC", "price": "0.6"},
        {"sell_all": False, "size": "5"},
        {"sell_all": True, "price": "0.6"},
    ],
)
def test_one_click_api_rejects_limit_and_partial_orders(app_client_factory, payload):
    client, _ = app_client_factory([[]])
    executor = client.app.state.whale_executor
    executor.quote_wallet_sell = AsyncMock()
    response = client.post("/api/execution-account/positions/99/sell/preview", json=payload)
    assert response.status_code == 422
    executor.quote_wallet_sell.assert_not_awaited()


async def test_index_truncation_does_not_block_verified_arsenal_balance(wallet_executor):
    executor, trader, client = wallet_executor
    client.fetch_active_positions.return_value = [position(asset_id="99", size="6.6363")]
    trader.onchain_outcome_balance.return_value = D("6.636362")
    trader.outcome_balance.return_value = D("6.636362")
    result = (await executor.wallet_positions())["positions"][0]
    assert result["size"] == D("6.636362")
    assert result["available_size"] == D("6.636362")
    assert result["status"] == "open"
    assert result["price"] == D("0.5")
    preview = await quote(executor, order_type="FAK", price=None)
    assert preview["size"] == D("6.63")


async def test_settled_loss_remains_settled_despite_index_truncation(wallet_executor):
    executor, trader, client = wallet_executor
    client.fetch_active_positions.return_value = []
    client.fetch_redeemable_positions.return_value = [
        position(asset_id="99", size="51.1578", current_price="0")
    ]
    trader.onchain_outcome_balance.return_value = D("51.157893")
    trader.outcome_balance.return_value = D("51.157893")
    result = (await executor.wallet_positions())["positions"][0]
    assert result["status"] == "settled"
    assert result["available_size"] == 0
    assert result["price"] == 0
    assert result["market_value"] == 0
    client.fetch_order_book.assert_not_awaited()


async def test_settled_balance_failure_does_not_make_it_an_open_position(wallet_executor):
    executor, trader, client = wallet_executor
    client.fetch_active_positions.return_value = []
    client.fetch_redeemable_positions.return_value = [position(asset_id="99")]
    trader.onchain_outcome_balance.side_effect = TradingUnavailable("余额读取失败")
    result = (await executor.wallet_positions())["positions"][0]
    assert result["status"] == "settled"
    assert result["available_size"] == 0
    assert result["reason"] == "余额读取失败"


async def test_zero_verified_balance_is_not_a_current_position(wallet_executor):
    executor, trader, _ = wallet_executor
    trader.onchain_outcome_balance.return_value = D("0")
    trader.outcome_balance.return_value = D("0")
    assert (await executor.wallet_positions())["positions"] == []


@pytest.mark.parametrize("chain,clob", [("6.636362", "6.6363"), ("6.6365", "6.6365")])
async def test_actual_balance_conflicts_still_block_sell(wallet_executor, chain, clob):
    executor, trader, client = wallet_executor
    client.fetch_active_positions.return_value = [position(asset_id="99", size="6.6363")]
    trader.onchain_outcome_balance.return_value = D(chain)
    trader.outcome_balance.return_value = D(clob)
    result = (await executor.wallet_positions())["positions"][0]
    assert result["status"] == "unavailable"
    assert result["available_size"] == 0
    with pytest.raises(ValueError):
        await quote(executor, order_type="FAK", price=None)


@pytest.mark.parametrize("order_type", ["FAK", "GTC"])
async def test_uncertain_wallet_sell_reconciles_precomputed_id(wallet_executor, order_type):
    executor, trader, _ = wallet_executor
    preview = await quote(executor, order_type=order_type)
    submit = trader.submit_prepared_limit if order_type == "GTC" else trader.submit_prepared_market
    submit.side_effect = TradingUnavailable("lost response")
    with pytest.raises(TradingUnavailable, match="自动核对"):
        await executor.execute_wallet_sell(preview, "lost-response")
    async with executor.database.sessions() as session:
        order = await session.scalar(select(WhaleOrder))
        assert order.external_order_id == "remote-1"
        assert order.status == "reconciliation_pending"
    trader.order_status.side_effect = TradingUnavailable("not found")
    assert "对账失败" in await executor.reconcile_pending_orders()
    assert (await executor.wallet_positions())["positions"][0]["available_size"] == 0
    trader.order_status.side_effect = None
    trader.order_status.return_value = TradeResult(status="cancelled", external_order_id="remote-1")
    assert await executor.reconcile_pending_orders() is None
    assert (await executor.wallet_positions())["positions"][0]["available_size"] == D("10")
    submit.assert_awaited_once()
