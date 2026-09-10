from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.config import Settings
from backend.db import Database
from backend.home import home_overview
from backend.models import (
    ExecutionAccount,
    WhaleAutoFollowDecision,
    WhaleEntry,
    WhaleEntryRuleState,
    WhaleExclusion,
    WhaleFollowLedger,
    WhaleFollowPosition,
    WhaleOrder,
    WhaleSettings,
)

NOW = datetime(2026, 8, 29, 8, 0, 0)  # 16:00 in Beijing.
CONDITION_ID = "0x" + "1" * 64
FUNDER = "0x" + "f" * 40


def test_home_overview_route_returns_thirty_beijing_days(app_client_factory) -> None:
    client, _ = app_client_factory([[]])

    response = client.get("/api/home/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["opportunity_counts"] == {
        rule: {"last_1_day": 0, "last_3_days": 0, "last_5_days": 0, "last_7_days": 0}
        for rule in ("new_account", "large_amount")
    }
    assert payload["follow_counts"] == payload["opportunity_counts"]
    assert payload["timezone"] == "Asia/Shanghai"
    assert len(payload["daily"]) == 30
    assert payload["today"]["buy_count"] == 0
    assert payload["today"]["conflict_exit_proceeds_usdc"] == 0.0
    assert payload["today"]["conflict_exit_count"] == 0
    assert payload["today"]["excluded_conflict_exit_count"] == 0
    assert payload["today"]["excluded_chain_test_count"] == 0
    assert payload["wallet"]["available"] is False
    assert payload["wallet"]["winning_pnl_usdc"] == 0.0


@pytest.fixture
async def database() -> Database:
    database = Database(Settings(database_url="sqlite+aiosqlite:///:memory:", start_monitor=False))
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


class MarksClient:
    def __init__(self, prices: dict[str, Decimal | None]) -> None:
        self.prices = prices

    async def fetch_order_book(self, asset_id: str) -> SimpleNamespace:
        return SimpleNamespace(best_bid=self.prices.get(asset_id))


@pytest.mark.asyncio
@pytest.mark.parametrize("include_large", [False, True])
async def test_opportunity_counts_use_first_trigger_and_beijing_calendar_days(
    database, include_large
):
    midnight = datetime(2026, 8, 28, 16)  # Beijing August 29, 00:00.
    triggers = [
        midnight,
        midnight - timedelta(microseconds=1),
        midnight - timedelta(days=2),
        midnight - timedelta(days=2, microseconds=1),
        midnight - timedelta(days=4),
        midnight - timedelta(days=4, microseconds=1),
        midnight - timedelta(days=6),
        midnight - timedelta(days=6, microseconds=1),
        NOW + timedelta(seconds=1),  # Do not count future timestamps.
        NOW,  # Excluded wallet.
        NOW,  # A different direction for the first wallet.
        NOW,  # Another wallet buying the same direction.
    ]
    async with database.sessions() as session:
        session.add(
            WhaleSettings(
                id=1, created_at=NOW, updated_at=NOW, monitor_categories_json='["sports"]'
            )
        )
        for index, triggered_at in enumerate(triggers):
            wallet = FUNDER if index in (0, 10) else f"0x{index:040x}"
            entry = WhaleEntry(
                proxy_wallet=wallet,
                asset_id="yes" if index in (0, 11) else f"asset-{index}",
                condition_id=CONDITION_ID,
                outcome="No" if index == 10 else "Yes",
                outcome_index=1 if index == 10 else 0,
                gross_buy_usdc=Decimal("100000"),
                gross_buy_size=Decimal("200000"),
                net_size=0,
                net_ratio=0,
                avg_buy_price=Decimal("0.5"),
                max_single_usdc=Decimal("100000"),
                trade_count=1,
                first_buy_at=triggered_at - timedelta(days=2),
                last_buy_at=triggered_at,
                status="exited",
                window_start=triggered_at,
                computed_at=NOW,
                settlement_price=Decimal("1") if index % 2 else None,
                settled_at=NOW if index % 2 else None,
            )
            session.add(entry)
            await session.flush()
            session.add(
                WhaleEntryRuleState(
                    entry_id=entry.id,
                    rule_type="new_account",
                    active=False,
                    first_triggered_at=triggered_at,
                    last_qualified_at=NOW,
                    threshold_usdc_snapshot=Decimal("100000"),
                )
            )
            if include_large and index in (0, 7):
                session.add(
                    WhaleEntryRuleState(
                        entry_id=entry.id,
                        rule_type="large_amount",
                        active=False,
                        first_triggered_at=NOW,
                        last_qualified_at=NOW,
                        threshold_usdc_snapshot=Decimal("500000"),
                    )
                )
            followed = position(
                f"follow-{index}", status="closed", size="0", cost="0", realized="0", closed_at=NOW
            )
            session.add(followed)
            await session.flush()
            order = WhaleOrder(
                position_id=followed.id,
                idempotency_key=f"opportunity-{index}",
                source="follow" if index == 11 else "auto_follow",
                asset_id=entry.asset_id,
                condition_id=CONDITION_ID,
                title="跟单市场",
                outcome=entry.outcome,
                side="BUY",
                limit_price=Decimal("0.5"),
                status="failed" if index == 10 else "filled",
                created_at=triggered_at - timedelta(days=1),
                updated_at=NOW,
            )
            session.add(order)
            await session.flush()
            session.add(
                WhaleAutoFollowDecision(
                    entry_id=entry.id,
                    proxy_wallet=wallet,
                    asset_id=entry.asset_id,
                    condition_id=CONDITION_ID,
                    outcome=entry.outcome,
                    category="sports",
                    selected_rule="large_amount" if index == 9 else "new_account",
                    matched_rules_json='["new_account", "large_amount"]',
                    buy_order_id=order.id,
                    status="failed" if index == 10 else "exited",
                    created_at=triggered_at - timedelta(days=1),
                    updated_at=NOW,
                )
            )
            if index != 10:
                for filled_at in (triggered_at, triggered_at + timedelta(seconds=1)):
                    session.add(
                        ledger(
                            followed.id,
                            "buy",
                            source=order.source,
                            amount="1",
                            pnl="0",
                            timestamp=filled_at,
                            order_id=order.id,
                        )
                    )
            if index == 9:
                session.add(WhaleExclusion(proxy_wallet=wallet, created_at=NOW))
        await session.commit()

    payload = await home_overview(database, MarksClient({}), now=NOW)

    assert payload["opportunity_counts"] == {
        "new_account": {"last_1_day": 3, "last_3_days": 5, "last_5_days": 7, "last_7_days": 9},
        "large_amount": {
            key: 2 if include_large else 0
            for key in ("last_1_day", "last_3_days", "last_5_days", "last_7_days")
        },
    }

    assert payload["follow_counts"] == {
        "new_account": {"last_1_day": 1, "last_3_days": 3, "last_5_days": 5, "last_7_days": 7},
        "large_amount": {"last_1_day": 1, "last_3_days": 1, "last_5_days": 1, "last_7_days": 1},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("balance_age", "expected_stale"),
    [
        (timedelta(minutes=4, seconds=59), False),
        (timedelta(minutes=5, seconds=1), True),
    ],
)
async def test_home_balance_becomes_stale_after_five_minutes(
    database: Database,
    balance_age: timedelta,
    expected_stale: bool,
) -> None:
    async with database.sessions() as session:
        session.add(WhaleSettings(id=1, created_at=NOW, updated_at=NOW))
        session.add(
            ExecutionAccount(
                id=1,
                signer_address=FUNDER,
                funder_address=FUNDER,
                status="ready",
                collateral_balance=Decimal("100"),
                last_balance_at=NOW - balance_age,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.commit()

    payload = await home_overview(database, MarksClient({}), now=NOW)  # type: ignore[arg-type]

    assert payload["wallet"]["balance_stale"] is expected_stale


@pytest.mark.asyncio
async def test_home_system_is_not_healthy_when_scanner_is_stopped_or_stale(
    database: Database,
) -> None:
    async with database.sessions() as session:
        session.add(
            WhaleSettings(
                id=1,
                last_scan_at=NOW,
                scan_interval_seconds=60,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.commit()

    stopped = await home_overview(
        database,
        MarksClient({}),  # type: ignore[arg-type]
        now=NOW,
        scanner_running=False,
    )

    assert stopped["system"]["status"] == "error"
    assert stopped["system"]["last_scan_error"] == "后台扫描任务未运行"

    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        assert settings is not None
        settings.last_scan_at = NOW - timedelta(seconds=181)
        await session.commit()

    stale = await home_overview(
        database,
        MarksClient({}),  # type: ignore[arg-type]
        now=NOW,
        scanner_running=True,
    )

    assert stale["system"]["status"] == "error"
    assert stale["system"]["last_scan_error"] == "最后扫描时间已过期"


@pytest.mark.asyncio
async def test_home_system_stays_degraded_while_trade_coverage_is_incomplete(
    database: Database,
) -> None:
    coverage_until = NOW + timedelta(hours=24)
    async with database.sessions() as session:
        session.add(
            WhaleSettings(
                id=1,
                last_scan_at=NOW,
                coverage_incomplete_until=coverage_until,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.commit()

    payload = await home_overview(
        database,
        MarksClient({}),  # type: ignore[arg-type]
        now=NOW,
        scanner_running=True,
    )

    assert payload["system"]["status"] == "error"
    assert payload["system"]["coverage_incomplete_until"] == coverage_until
    assert "自动跟单暂停" in payload["system"]["last_scan_error"]


def position(
    asset_id: str,
    *,
    status: str,
    size: str,
    cost: str,
    realized: str,
    closed_at: datetime | None = None,
) -> WhaleFollowPosition:
    return WhaleFollowPosition(
        asset_id=asset_id,
        condition_id=CONDITION_ID,
        title=f"市场 {asset_id}",
        outcome="Yes",
        outcome_index=0,
        neg_risk=False,
        cycle_no=1,
        size=Decimal(size),
        cost_usdc=Decimal(cost),
        lifetime_bought_size=Decimal(size),
        lifetime_bought_usdc=Decimal(cost),
        lifetime_sold_size=Decimal("0"),
        lifetime_sold_usdc=Decimal("0"),
        lifetime_fee_usdc=Decimal("0"),
        realized_pnl=Decimal(realized),
        status=status,
        opened_at=NOW,
        closed_at=closed_at,
        created_at=NOW,
        updated_at=NOW,
    )


def ledger(
    position_id: int,
    kind: str,
    *,
    source: str,
    amount: str,
    pnl: str,
    timestamp: datetime,
    order_id: int | None = None,
) -> WhaleFollowLedger:
    return WhaleFollowLedger(
        position_id=position_id,
        order_id=order_id,
        type=kind,
        source=source,
        size=Decimal("1"),
        price=Decimal("0.5") if kind in {"buy", "sell"} else None,
        amount_usdc=Decimal(amount),
        fee_usdc=Decimal("0"),
        realized_pnl=Decimal(pnl),
        timestamp=timestamp,
    )


@pytest.mark.asyncio
async def test_home_overview_uses_beijing_days_and_all_whale_follow_sources(
    database: Database,
) -> None:
    async with database.sessions() as session:
        session.add(
            WhaleSettings(
                id=1,
                last_scan_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            ExecutionAccount(
                id=1,
                signer_address=FUNDER,
                funder_address=FUNDER,
                status="ready",
                collateral_balance=Decimal("100"),
                last_balance_at=NOW,
                cash_reserve_usdc=Decimal("60"),
                created_at=NOW,
                updated_at=NOW,
            )
        )
        rows = [
            position("open", status="open", size="25", cost="20", realized="2"),
            position("win", status="closed", size="0", cost="0", realized="4", closed_at=NOW),
            position(
                "loss",
                status="resolved_loss",
                size="0",
                cost="0",
                realized="-5",
                closed_at=NOW,
            ),
            position("flat", status="redeemed", size="0", cost="0", realized="0", closed_at=NOW),
        ]
        session.add_all(rows)
        await session.flush()
        session.add_all(
            [
                # 15:59 UTC is still the previous Beijing natural day.
                ledger(
                    rows[0].id,
                    "buy",
                    source="manual",
                    amount="3",
                    pnl="0",
                    timestamp=datetime(2026, 8, 28, 15, 59),
                ),
                ledger(
                    rows[0].id,
                    "buy",
                    source="auto_follow",
                    amount="10",
                    pnl="0",
                    timestamp=datetime(2026, 8, 28, 16, 1),
                ),
                ledger(
                    rows[1].id,
                    "buy",
                    source="auto_follow",
                    amount="4",
                    pnl="0",
                    timestamp=datetime(2026, 8, 29, 0, 30),
                ),
                ledger(
                    rows[0].id,
                    "sell",
                    source="conflict_exit",
                    amount="10",
                    pnl="2",
                    timestamp=datetime(2026, 8, 29, 1),
                ),
                ledger(
                    rows[1].id,
                    "redeem",
                    source="auto_redeem",
                    amount="9",
                    pnl="4",
                    timestamp=datetime(2026, 8, 29, 2),
                ),
                ledger(
                    rows[2].id,
                    "resolved_loss",
                    source="auto_redeem",
                    amount="0",
                    pnl="-5",
                    timestamp=datetime(2026, 8, 29, 3),
                ),
                ledger(
                    rows[3].id,
                    "redeem",
                    source="reconciliation",
                    amount="5",
                    pnl="0",
                    timestamp=datetime(2026, 8, 29, 4),
                ),
            ]
        )
        await session.commit()

    payload = await home_overview(
        database,
        MarksClient({"open": Decimal("0.9")}),  # type: ignore[arg-type]
        now=NOW,
    )

    assert len(payload["daily"]) == 30
    assert payload["daily"][-2]["buy_amount_usdc"] == Decimal("0")
    assert payload["daily"][-2]["conflict_exit_proceeds_usdc"] == Decimal("0")
    assert payload["today"]["buy_amount_usdc"] == Decimal("4")
    assert payload["today"]["buy_count"] == 1
    assert payload["today"]["conflict_exit_proceeds_usdc"] == Decimal("10")
    assert payload["today"]["conflict_exit_count"] == 1
    assert payload["today"]["realized_pnl_usdc"] == Decimal("1")
    assert payload["today"]["realized_cost_usdc"] == Decimal("23")
    assert payload["today"]["realized_roi_percent"] == Decimal("100") / Decimal("23")
    assert payload["today"]["win_count"] == 1
    assert payload["today"]["loss_count"] == 1
    assert payload["today"]["flat_count"] == 1
    assert payload["today"]["win_rate_percent"] == Decimal("50")
    assert payload["daily"][-1]["win_rate_percent"] == Decimal("50")
    assert payload["wallet"]["market_value_usdc"] == Decimal("22.5")
    assert payload["wallet"]["unrealized_pnl_usdc"] == Decimal("2.5")
    assert payload["wallet"]["winning_pnl_usdc"] == Decimal("5")
    assert payload["wallet"]["total_assets_usdc"] == Decimal("122.5")
    assert payload["wallet"]["available_cash_usdc"] == Decimal("40")


@pytest.mark.asyncio
async def test_home_buy_count_deduplicates_partial_fills_for_one_order(
    database: Database,
) -> None:
    async with database.sessions() as session:
        session.add(WhaleSettings(id=1, created_at=NOW, updated_at=NOW))
        followed = position("partial-fill", status="open", size="10", cost="7", realized="0")
        session.add(followed)
        await session.flush()
        order = WhaleOrder(
            position_id=followed.id,
            entry_id=None,
            idempotency_key="home-partial-fill",
            source="auto_follow",
            source_wallet=None,
            asset_id=followed.asset_id,
            condition_id=followed.condition_id,
            title=followed.title,
            outcome=followed.outcome,
            outcome_index=followed.outcome_index,
            neg_risk=False,
            side="BUY",
            requested_size=Decimal("10"),
            requested_usdc=Decimal("7"),
            limit_price=Decimal("0.7"),
            reference_price=Decimal("0.7"),
            whale_avg_price=None,
            filled_size=Decimal("10"),
            filled_usdc=Decimal("7"),
            fee_usdc=Decimal("0"),
            status="filled",
            reason=None,
            signed_order_hash=None,
            execution_provider="test",
            external_order_id=None,
            external_trade_id=None,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(order)
        await session.flush()
        session.add_all(
            [
                ledger(
                    followed.id,
                    "buy",
                    source="auto_follow",
                    amount="3",
                    pnl="0",
                    timestamp=NOW - timedelta(minutes=1),
                    order_id=order.id,
                ),
                ledger(
                    followed.id,
                    "buy",
                    source="auto_follow",
                    amount="4",
                    pnl="0",
                    timestamp=NOW,
                    order_id=order.id,
                ),
            ]
        )
        await session.commit()

    payload = await home_overview(
        database,
        MarksClient({"partial-fill": Decimal("0.7")}),  # type: ignore[arg-type]
        now=NOW,
    )

    assert payload["today"]["buy_amount_usdc"] == Decimal("7")
    assert payload["today"]["buy_count"] == 1


@pytest.mark.asyncio
async def test_home_win_rate_excludes_conflict_exit_positions(database: Database) -> None:
    async with database.sessions() as session:
        session.add(WhaleSettings(id=1, created_at=NOW, updated_at=NOW))
        rows = [
            position(
                "normal-win", status="closed", size="0", cost="0", realized="4", closed_at=NOW
            ),
            position(
                "normal-loss", status="closed", size="0", cost="0", realized="-3", closed_at=NOW
            ),
            position(
                "conflict-loss", status="closed", size="0", cost="0", realized="-2", closed_at=NOW
            ),
            position(
                "chain-test-loss", status="closed", size="0", cost="0", realized="-1", closed_at=NOW
            ),
        ]
        session.add_all(rows)
        await session.flush()
        session.add(
            ledger(
                rows[2].id,
                "sell",
                source="conflict_exit",
                amount="3",
                pnl="-2",
                timestamp=NOW,
            )
        )
        session.add(
            ledger(
                rows[3].id,
                "sell",
                source="chain_test",
                amount="4",
                pnl="-1",
                timestamp=NOW,
            )
        )
        session.add(
            ledger(
                rows[3].id,
                "buy",
                source="chain_test",
                amount="2",
                pnl="0",
                timestamp=NOW,
            )
        )
        await session.commit()

    payload = await home_overview(database, MarksClient({}), now=NOW)  # type: ignore[arg-type]

    assert payload["today"]["win_count"] == 1
    assert payload["today"]["loss_count"] == 1
    assert payload["today"]["flat_count"] == 0
    assert payload["today"]["excluded_conflict_exit_count"] == 1
    assert payload["today"]["excluded_chain_test_count"] == 1
    assert payload["today"]["win_rate_percent"] == Decimal("50")
    assert payload["today"]["buy_amount_usdc"] == Decimal("0")
    assert payload["today"]["buy_count"] == 0
    assert payload["today"]["realized_pnl_usdc"] == Decimal("-3")


@pytest.mark.asyncio
async def test_home_overview_empty_and_incomplete_valuation(database: Database) -> None:
    async with database.sessions() as session:
        session.add(WhaleSettings(id=1, created_at=NOW, updated_at=NOW))
        missing = position("missing", status="open", size="2", cost="1", realized="0")
        session.add(missing)
        await session.commit()

    payload = await home_overview(
        database,
        MarksClient({"missing": None}),  # type: ignore[arg-type]
        now=NOW,
    )

    assert payload["today"]["buy_count"] == 0
    assert payload["today"]["realized_roi_percent"] is None
    assert payload["today"]["win_rate_percent"] is None
    assert payload["wallet"]["available"] is False
    assert payload["wallet"]["balance_stale"] is True
    assert payload["wallet"]["valuation_complete"] is False
    assert payload["wallet"]["market_value_usdc"] is None
    assert payload["wallet"]["total_assets_usdc"] is None
    assert payload["wallet"]["unpriced_position_count"] == 1
    assert payload["wallet"]["winning_pnl_usdc"] == Decimal("1")


@pytest.mark.asyncio
async def test_home_winning_pnl_uses_remaining_cost_and_sums_open_positions(database):
    async with database.sessions() as session:
        session.add_all(
            [
                position(
                    "arsenal", status="open", size="44.370968", cost="28.03269016", realized="0"
                ),
                position("partial", status="closing", size="3", cost="2.1", realized="4"),
                position("finished", status="redeemed", size="0", cost="0", realized="20"),
            ]
        )
        await session.commit()

    payload = await home_overview(database, MarksClient({}), now=NOW)

    assert payload["wallet"]["winning_pnl_usdc"].quantize(Decimal("0.00000001")) == Decimal(
        "17.23827784"
    )
    assert payload["wallet"]["unrealized_pnl_usdc"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("running,expected", [(True, "degraded"), (False, "error")])
async def test_home_distinguishes_pending_market_backfill_from_failure(database, running, expected):
    async with database.sessions() as session:
        session.add(
            WhaleSettings(
                id=1,
                created_at=NOW,
                updated_at=NOW,
                last_scan_at=NOW,
                last_scan_error="重点市场 0xabc 历史补齐中，拆单回溯不完整，未用于新增信号",
            )
        )
        await session.commit()
    payload = await home_overview(database, MarksClient({}), now=NOW, scanner_running=running)
    assert payload["system"]["status"] == expected
