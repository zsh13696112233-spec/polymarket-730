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
    WhaleFollowLedger,
    WhaleFollowPosition,
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
    assert payload["timezone"] == "Asia/Shanghai"
    assert len(payload["daily"]) == 30
    assert payload["today"]["buy_count"] == 0
    assert payload["wallet"]["available"] is False


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
) -> WhaleFollowLedger:
    return WhaleFollowLedger(
        position_id=position_id,
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
    assert payload["daily"][-2]["buy_amount_usdc"] == Decimal("3")
    assert payload["today"]["buy_amount_usdc"] == Decimal("10")
    assert payload["today"]["buy_count"] == 1
    assert payload["today"]["realized_pnl_usdc"] == Decimal("1")
    assert payload["today"]["realized_cost_usdc"] == Decimal("23")
    assert payload["today"]["realized_roi_percent"] == Decimal("100") / Decimal("23")
    assert payload["today"]["win_count"] == 1
    assert payload["today"]["loss_count"] == 1
    assert payload["today"]["flat_count"] == 1
    assert payload["today"]["win_rate_percent"] == Decimal("50")
    assert payload["wallet"]["market_value_usdc"] == Decimal("22.5")
    assert payload["wallet"]["unrealized_pnl_usdc"] == Decimal("2.5")
    assert payload["wallet"]["total_assets_usdc"] == Decimal("122.5")
    assert payload["wallet"]["available_cash_usdc"] == Decimal("40")


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
