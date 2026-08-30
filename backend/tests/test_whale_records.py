from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from backend.config import Settings
from backend.db import Database
from backend.models import WhaleFollowLedger, WhaleFollowPosition
from backend.schemas import WhaleRecordListRead
from backend.whale import list_whale_records

NOW = datetime(2026, 8, 30, 8, 0, 0)
CONDITION_ID = "0x" + "7" * 64


@pytest.fixture
async def database() -> Database:
    database = Database(Settings(database_url="sqlite+aiosqlite:///:memory:", start_monitor=False))
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


def finished_position(asset_id: str, realized_pnl: str) -> WhaleFollowPosition:
    return WhaleFollowPosition(
        asset_id=asset_id,
        condition_id=CONDITION_ID,
        title=f"市场 {asset_id}",
        outcome="Yes",
        outcome_index=0,
        neg_risk=False,
        cycle_no=1,
        size=Decimal("0"),
        cost_usdc=Decimal("0"),
        lifetime_bought_size=Decimal("10"),
        lifetime_bought_usdc=Decimal("5"),
        lifetime_sold_size=Decimal("10"),
        lifetime_sold_usdc=Decimal("5") + Decimal(realized_pnl),
        lifetime_fee_usdc=Decimal("0"),
        realized_pnl=Decimal(realized_pnl),
        status="closed",
        opened_at=NOW,
        closed_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.asyncio
async def test_record_win_rate_excludes_conflict_exits_and_flats(database: Database) -> None:
    async with database.sessions() as session:
        positions = [
            finished_position("normal-win", "2"),
            finished_position("normal-loss", "-1"),
            finished_position("normal-flat", "0"),
            finished_position("conflict-win", "1"),
            finished_position("conflict-loss", "-2"),
            finished_position("chain-test-loss", "-0.5"),
        ]
        session.add_all(positions)
        await session.flush()
        session.add_all(
            WhaleFollowLedger(
                position_id=position.id,
                type="sell",
                source="conflict_exit",
                size=Decimal("10"),
                price=Decimal("0.5"),
                amount_usdc=Decimal("5") + position.realized_pnl,
                fee_usdc=Decimal("0"),
                realized_pnl=position.realized_pnl,
                timestamp=NOW,
            )
            for position in positions[3:]
        )
        chain_test_ledger = next(
            row
            for row in session.new
            if isinstance(row, WhaleFollowLedger) and row.position_id == positions[5].id
        )
        chain_test_ledger.source = "chain_test"
        await session.commit()

    payload = await list_whale_records(database, object())  # type: ignore[arg-type]
    validated = WhaleRecordListRead.model_validate(payload)

    assert validated.summary.closed_position_count == 6
    assert validated.summary.win_count == 1
    assert validated.summary.loss_count == 1
    assert validated.summary.excluded_conflict_exit_count == 2
    assert validated.summary.excluded_chain_test_count == 1
    assert validated.summary.win_rate_percent == Decimal("50")
    assert validated.summary.realized_pnl == Decimal("-1.5")
