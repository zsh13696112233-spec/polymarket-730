from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from backend.config import Settings
from backend.db import Database
from backend.models import (
    ExecutionAccount,
    WhaleAutoFollowDecision,
    WhaleEmailDelivery,
    WhaleEntry,
    WhaleFollowLedger,
    WhaleFollowPosition,
    WhaleOrder,
)
from backend.schemas import EmailDeliveryRead
from backend.weekly_report import (
    build_report,
    enqueue_weekly_report,
    replay_position,
    report_period,
    settlement_result,
)
from backend.whale_email import WhaleEmailNotifier, list_whale_email_deliveries

D = Decimal
START = datetime(2026, 9, 6, 16)
END = datetime(2026, 9, 13, 16)
NOW = datetime(2026, 9, 13, 22)  # Monday 06:00 Beijing


@pytest.fixture
async def database():
    db = Database(
        Settings(
            database_url="sqlite+aiosqlite:///:memory:", start_monitor=False, trading_enabled=False
        )
    )
    await db.initialize()
    yield db
    await db.close()


def ledger(index, kind, size, amount, pnl="0", fee="0", order_id=None, at=None):
    return WhaleFollowLedger(
        id=index,
        position_id=1,
        order_id=order_id,
        type=kind,
        source="auto_follow",
        size=D(size),
        amount_usdc=D(amount),
        fee_usdc=D(fee),
        realized_pnl=D(pnl),
        timestamp=at or START + timedelta(hours=index),
    )


def position(size="0", cost="0", pnl="0"):
    return WhaleFollowPosition(
        id=1,
        asset_id="asset",
        condition_id="market",
        title="测试市场",
        outcome="Yes",
        size=D(size),
        cost_usdc=D(cost),
        realized_pnl=D(pnl),
        status="open" if D(size) else "redeemed",
        created_at=START,
        updated_at=NOW,
    )


@pytest.mark.parametrize(
    "now,expected_end",
    [
        (NOW - timedelta(seconds=1), END - timedelta(days=7)),
        (NOW, END),
        (NOW + timedelta(days=6), END),
        (NOW + timedelta(days=7), END + timedelta(days=7)),
        (datetime(2026, 1, 4, 22, tzinfo=UTC), datetime(2026, 1, 4, 16)),
    ],
)
def test_latest_due_period(now, expected_end):
    start, end = report_period(now)
    assert end == expected_end
    assert start == end - timedelta(days=7)


def test_mixed_lots_partial_exit_and_redemption_preserve_cost_and_fees():
    rows = [
        ledger(1, "buy", "10", "4.1", fee=".1", order_id=1),
        ledger(2, "buy", "10", "6.1", fee=".1", order_id=2),
        ledger(3, "buy", "10", "5.1", fee=".1"),
        ledger(4, "sell", "15", "11.7", pnl="4.05", fee=".3"),
    ]
    lots, valid = replay_position(rows, position("15", "7.65", "4.05"))
    assert valid
    assert lots[2].size == 5
    assert lots[2].realized == D(".85")
    assert lots[2].fee == D(".2")
    rows += [
        ledger(5, "buy", "10", "2.1", fee=".1", order_id=3),
        ledger(6, "redeem", "25", "25", pnl="15.25"),
    ]
    lots, valid = replay_position(rows, position(pnl="19.3"))
    assert valid
    assert lots[2].realized == D("2.8")
    assert lots[3].realized == D("7.9")
    assert sum(lot.realized for lot in lots.values()) == D("19.3")
    assert sum(lot.fee for lot in lots.values()) == D(".7")


@pytest.mark.parametrize("kind", ["resolved_loss", "dust_writeoff"])
def test_loss_and_dust_release_cost(kind):
    rows = [
        ledger(1, "buy", "10", "4.1", fee=".1", order_id=1),
        ledger(2, kind, "10", "0", pnl="-4.1"),
    ]
    lots, valid = replay_position(rows, position(pnl="-4.1"))
    assert valid
    assert lots[1].realized == D("-4.1")
    assert lots[1].size == 0


def test_incomplete_ledger_is_not_reported_as_exact_profit():
    rows = [ledger(1, "sell", "10", "5", pnl="1")]
    assert replay_position(rows, position(pnl="1"))[1] is False
    rows = [ledger(1, "buy", "10", "4", order_id=1)]
    assert replay_position(rows, position("11", "4"))[1] is False


@pytest.mark.parametrize(
    "price,settled,expected",
    [
        ("1", NOW, "命中"),
        ("0", NOW, "未命中"),
        (".5", NOW, "特殊结算"),
        ("1", None, "待结算"),
        ("1", NOW + timedelta(seconds=1), "待结算"),
    ],
)
def test_settlement_requires_verified_time(price, settled, expected):
    assert (
        settlement_result(WhaleEntry(settlement_price=D(price), settled_at=settled), NOW)
        == expected
    )


async def seed_cohort(database):
    async with database.sessions() as session:
        session.add(position(pnl="19.3"))
        session.add(
            ExecutionAccount(
                id=1,
                collateral_balance=D("120"),
                last_balance_at=NOW,
                created_at=START,
                updated_at=NOW,
            )
        )
        for index, rule in [(1, "large_amount"), (2, "new_account"), (3, "large_amount")]:
            entry = WhaleEntry(
                id=index,
                proxy_wallet=f"wallet-{index}",
                asset_id="asset",
                condition_id="market",
                outcome="Yes",
                outcome_index=0,
                gross_buy_usdc=D("10"),
                gross_buy_size=D("20"),
                net_size=D("20"),
                net_ratio=D("100"),
                avg_buy_price=D(".5"),
                max_single_usdc=D("10"),
                trade_count=1,
                first_buy_at=START,
                last_buy_at=START,
                status="holding",
                window_start=START,
                computed_at=NOW,
                settlement_price=D("1"),
                settled_at=NOW - timedelta(hours=1),
            )
            session.add(entry)
            session.add(
                WhaleOrder(
                    id=index,
                    position_id=1,
                    entry_id=index,
                    idempotency_key=f"order-{index}",
                    source="auto_follow",
                    asset_id="asset",
                    condition_id="market",
                    title=f"订单市场-{index}",
                    outcome="Yes",
                    side="BUY",
                    limit_price=D(".6"),
                    status="filled",
                    created_at=START,
                    updated_at=NOW,
                )
            )
            await session.flush()
            session.add(
                WhaleAutoFollowDecision(
                    entry_id=index,
                    proxy_wallet=f"wallet-{index}",
                    asset_id="asset",
                    condition_id="market",
                    outcome="Yes",
                    selected_rule=rule,
                    matched_rules_json='["new_account","large_amount"]',
                    category="sports",
                    buy_order_id=index,
                    created_at=START,
                    updated_at=NOW,
                )
            )
        await session.flush()
        session.add_all(
            [
                ledger(
                    1, "buy", "10", "4.1", fee=".1", order_id=1, at=START - timedelta(seconds=1)
                ),
                ledger(2, "buy", "5", "3.05", fee=".05", order_id=2),
                ledger(3, "buy", "5", "3.05", fee=".05", order_id=2),
                ledger(4, "buy", "10", "5.1", fee=".1"),
                ledger(5, "sell", "15", "11.7", pnl="4.05", fee=".3"),
                ledger(6, "buy", "10", "2.1", fee=".1", order_id=3),
                ledger(7, "redeem", "25", "25", pnl="15.25", at=NOW - timedelta(hours=1)),
            ]
        )
        await session.commit()


async def test_cohort_report_excludes_older_orders_and_counts_partial_fills_once(database):
    await seed_cohort(database)
    client = SimpleNamespace(
        fetch_order_book=AsyncMock(side_effect=AssertionError("closed positions need no quote"))
    )
    subject, body = await build_report(
        database, client, period_start=START, period_end=END, now=NOW
    )
    assert "2026-09-07 至 2026-09-13" in subject
    assert "#1 " not in body
    assert "订单明细" not in body and "订单市场-" not in body
    assert "口径：" not in body and "统计归因" not in body
    assert len(body.splitlines()) <= 25
    assert "成交：2 单；含费投入：8.20 USDC" in body
    assert "已实现盈亏：10.70 USDC" in body
    assert "现金＋已纳管持仓：120.00 USDC" in body
    assert "结算命中率：100.00%" in body
    assert "累计交易手续费：0.30 USDC" in body
    client.fetch_order_book.assert_not_called()


async def test_unavailable_marks_stale_balance_and_unknown_rule(database):
    await seed_cohort(database)
    async with database.sessions() as session:
        p = await session.get(WhaleFollowPosition, 1)
        p.size = D("1")  # Reconciliation mismatch must invalidate exact P&L.
        p.status = "open"
        account = await session.get(ExecutionAccount, 1)
        account.last_balance_at = START
        decisions = list(await session.scalars(select(WhaleAutoFollowDecision)))
        for decision in decisions:
            decision.selected_rule = None
        await session.commit()
    client = SimpleNamespace(fetch_order_book=AsyncMock(side_effect=RuntimeError("offline")))
    refresh = AsyncMock(side_effect=RuntimeError("offline"))
    _, body = await build_report(
        database, client, period_start=START, period_end=END, now=NOW, refresh_balance=refresh
    )
    assert "【无法归属】" in body
    assert "待核对：2" in body
    assert "现金＋已纳管持仓：待核对 / 数据不完整" in body
    assert "刷新失败，使用缓存" in body and "数据过期或缺失" in body
    assert "已实现盈亏：10.70" not in body
    refresh.assert_awaited_once()


async def test_enqueue_dedupes_and_freezes_body_and_serializes_new_kind(database, monkeypatch):
    await seed_cohort(database)
    settings = Settings(weekly_report_to=("a@example.com", "a@example.com", "b@example.com"))
    client = SimpleNamespace(fetch_order_book=AsyncMock())
    assert await enqueue_weekly_report(database, settings, client, now=NOW) == 2

    async def unexpected(*args, **kwargs):
        raise AssertionError("must not regenerate persisted report")

    monkeypatch.setattr("backend.weekly_report.build_report", unexpected)
    assert await enqueue_weekly_report(database, settings, client, now=NOW + timedelta(days=2)) == 0
    assert (
        await enqueue_weekly_report(
            database,
            Settings(weekly_report_to=("c@example.com",)),
            client,
            now=NOW + timedelta(days=3),
        )
        == 1
    )
    result = await list_whale_email_deliveries(database, status="all", limit=50, offset=0)
    assert result["total"] == 3
    assert len({row["body_text"] for row in result["items"]}) == 1
    for row in result["items"]:
        assert (
            EmailDeliveryRead.model_validate(row).notification_kind == "weekly_auto_follow_report"
        )
        assert row["result"] == "not_applicable"


async def test_disabled_does_not_fetch_or_generate(database, monkeypatch):
    generate = AsyncMock(side_effect=AssertionError("disabled"))
    monkeypatch.setattr("backend.weekly_report.build_report", generate)
    assert await enqueue_weekly_report(database, Settings(), None, now=NOW) == 0
    generate.assert_not_called()


async def test_empty_week_still_sends_account_overview(database):
    client = SimpleNamespace(fetch_order_book=AsyncMock())
    assert (
        await enqueue_weekly_report(
            database, Settings(weekly_report_to=("a@example.com",)), client, now=NOW
        )
        == 1
    )
    async with database.sessions() as session:
        row = await session.scalar(select(WhaleEmailDelivery))
        assert "上周无自动买入成交" in row.body_text
        assert "现金＋已纳管持仓：待核对 / 数据不完整" in row.body_text


async def test_delivery_failure_retries_identical_body(database, monkeypatch):
    settings = Settings(
        weekly_report_to=("a@example.com",), start_monitor=False, trading_enabled=False
    )
    await enqueue_weekly_report(
        database, settings, SimpleNamespace(fetch_order_book=AsyncMock()), now=NOW
    )
    notifier = WhaleEmailNotifier(database=database, settings=settings)
    monkeypatch.setattr(notifier, "load_transport", AsyncMock(return_value=None))
    sent_bodies = []

    def send(row, transport):
        sent_bodies.append(row.body_text)
        if len(sent_bodies) == 1:
            raise RuntimeError("temporary failure")

    monkeypatch.setattr(notifier, "_send", send)
    monkeypatch.setattr("backend.whale_email.utcnow", lambda: NOW)
    assert await notifier.deliver_once()
    async with database.sessions() as session:
        row = await session.scalar(select(WhaleEmailDelivery))
        assert row.status == "retrying"
        assert row.attempt_count == 1
        row.next_attempt_at = NOW
        await session.commit()
    assert await notifier.deliver_once()
    assert sent_bodies[0] == sent_bodies[1]
    async with database.sessions() as session:
        row = await session.scalar(select(WhaleEmailDelivery))
        assert row.status == "sent"
        assert row.attempt_count == 2


async def test_open_cohort_marked_to_market_and_partial_proceeds(database):
    await seed_cohort(database)
    async with database.sessions() as session:
        await session.delete(await session.get(WhaleFollowLedger, 7))
        p = await session.get(WhaleFollowPosition, 1)
        p.size, p.cost_usdc, p.realized_pnl, p.status = D("25"), D("9.75"), D("4.05"), "open"
        await session.commit()
    client = SimpleNamespace(
        fetch_order_book=AsyncMock(return_value=SimpleNamespace(best_bid=D(".7")))
    )
    _, body = await build_report(database, client, period_start=START, period_end=END, now=NOW)
    assert "已实现盈亏：0.85 USDC；浮动盈亏：5.35 USDC；合计盈亏：6.20 USDC" in body
    assert "现金＋已纳管持仓：137.50 USDC" in body
    assert "仍有持仓：2" in body
    assert "结算命中率：100.00%" in body


async def test_first_fill_boundary_not_later_fill_controls_cohort(database):
    await seed_cohort(database)
    async with database.sessions() as session:
        first = await session.get(WhaleFollowLedger, 2)
        first.timestamp = START - timedelta(seconds=1)
        third = await session.get(WhaleFollowLedger, 6)
        third.timestamp = END
        await session.commit()
    _, body = await build_report(
        database,
        SimpleNamespace(fetch_order_book=AsyncMock()),
        period_start=START,
        period_end=END,
        now=NOW,
    )
    assert "上周无自动买入成交" in body
    assert "#2 " not in body and "#3 " not in body


async def test_start_boundary_included_and_post_week_fill_is_tracked(database):
    await seed_cohort(database)
    async with database.sessions() as session:
        first = await session.get(WhaleFollowLedger, 2)
        first.timestamp = START
        later = await session.get(WhaleFollowLedger, 3)
        later.timestamp = END  # Before redeem, but after partial sell: ledger no longer aligns.
        await session.commit()
    _, body = await build_report(
        database,
        SimpleNamespace(fetch_order_book=AsyncMock()),
        period_start=START,
        period_end=END,
        now=NOW,
    )
    assert "成交：1 单；含费投入：6.10 USDC" in body
    assert "含费投入：6.10 USDC" in body
    assert "待核对" in body


async def test_catch_up_before_monday_deadline_does_not_send_upcoming_week(database):
    settings = Settings(weekly_report_to=("a@example.com",))
    client = SimpleNamespace(fetch_order_book=AsyncMock())
    assert (
        await enqueue_weekly_report(database, settings, client, now=NOW - timedelta(seconds=1)) == 1
    )
    async with database.sessions() as session:
        row = await session.scalar(select(WhaleEmailDelivery))
        assert row.dedupe_key == "weekly-auto-follow:2026-08-31"
    assert await enqueue_weekly_report(database, settings, client, now=NOW) == 1
    assert await enqueue_weekly_report(database, settings, client, now=NOW + timedelta(days=2)) == 0
    async with database.sessions() as session:
        rows = list(await session.scalars(select(WhaleEmailDelivery)))
        assert len(rows) == 2


async def test_application_writes_to_migrated_database(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'report.db'}",
        weekly_report_to=("a@example.com",),
        start_monitor=False,
        trading_enabled=False,
    )
    db = Database(settings)
    try:
        await db.initialize()
        assert (
            await enqueue_weekly_report(
                db, settings, SimpleNamespace(fetch_order_book=AsyncMock()), now=NOW
            )
            == 1
        )
        await db.close()
        # Simulated process restart uses the durable outbox rather than generating again.
        assert await enqueue_weekly_report(db, settings, None, now=NOW) == 0
    finally:
        await db.close()


async def test_report_generation_failure_does_not_block_other_notifications(
    database, monkeypatch, caplog
):
    import asyncio

    report = AsyncMock(side_effect=RuntimeError("sensitive upstream payload"))
    old_report = AsyncMock()
    notifier = WhaleEmailNotifier(database=database, settings=Settings(), weekly_report=report)
    deliver = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr("backend.whale_email.enqueue_due_weekly_summary", old_report)
    monkeypatch.setattr(notifier, "deliver_once", deliver)
    with pytest.raises(asyncio.CancelledError):
        await notifier._run()
    report.assert_awaited_once()
    old_report.assert_awaited_once()
    deliver.assert_awaited_once()
    assert "周报生成失败" in caplog.text
    assert "sensitive upstream payload" not in caplog.text
