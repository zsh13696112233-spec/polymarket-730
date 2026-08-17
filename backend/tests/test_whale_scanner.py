from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from backend.config import Settings
from backend.db import Database
from backend.models import WhaleSettings
from backend.monitor import utcnow
from backend.whale import WhaleDiscoveryScanner


@pytest.fixture
async def database() -> Database:
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        start_monitor=False,
    )
    database = Database(settings)
    await database.initialize()
    # 内存库走 create_all 而不是迁移，巨鲸设置的单例行要自己补上。
    now = utcnow()
    async with database.sessions() as session:
        session.add(WhaleSettings(id=1, created_at=now, updated_at=now))
        await session.commit()
    try:
        yield database
    finally:
        await database.close()


class ScanGate:
    """把一轮扫描卡在中间，用来观察手动扫描与后台轮次的相互影响。"""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.thresholds: list[Decimal] = []

    async def scan(self, config: dict[str, Any]) -> str | None:
        self.thresholds.append(config["cumulative_threshold_usdc"])
        self.started.set()
        await self.release.wait()
        return None


def build_scanner(database: Database) -> WhaleDiscoveryScanner:
    return WhaleDiscoveryScanner(
        database=database,
        client=SimpleNamespace(),  # type: ignore[arg-type]
        settings=database.settings,
    )


async def running_round(scanner: WhaleDiscoveryScanner, gate: ScanGate) -> asyncio.Task[bool]:
    task = asyncio.create_task(scanner.tick())
    await asyncio.wait_for(gate.started.wait(), timeout=5)
    return task


async def update_threshold(database: Database, value: str) -> None:
    async with database.sessions() as session:
        row = await session.get(WhaleSettings, 1)
        assert row is not None
        row.cumulative_threshold_usdc = Decimal(value)
        row.updated_at = utcnow()
        await session.commit()


async def current_threshold(database: Database) -> Decimal:
    async with database.sessions() as session:
        row = await session.get(WhaleSettings, 1)
        assert row is not None
        return row.cumulative_threshold_usdc


async def test_background_tick_skips_while_another_round_is_running(database, monkeypatch):
    scanner = build_scanner(database)
    gate = ScanGate()
    monkeypatch.setattr(scanner, "_scan", gate.scan)
    background = await running_round(scanner, gate)

    assert await scanner.tick() is False

    gate.release.set()
    assert await background is True
    assert len(gate.thresholds) == 1


async def test_manual_scan_reruns_when_running_round_predates_the_settings_change(
    database,
    monkeypatch,
):
    scanner = build_scanner(database)
    gate = ScanGate()
    monkeypatch.setattr(scanner, "_scan", gate.scan)
    stale_threshold = await current_threshold(database)
    background = await running_round(scanner, gate)
    await update_threshold(database, "44444")

    manual = asyncio.create_task(scanner.scan_now())
    await asyncio.sleep(0)
    gate.release.set()

    assert await manual is True
    assert await background is True
    assert gate.thresholds == [stale_threshold, Decimal("44444")]


async def test_manual_scan_only_waits_when_running_round_already_has_latest_settings(
    database,
    monkeypatch,
):
    scanner = build_scanner(database)
    gate = ScanGate()
    monkeypatch.setattr(scanner, "_scan", gate.scan)
    await update_threshold(database, "44444")
    background = await running_round(scanner, gate)

    manual = asyncio.create_task(scanner.scan_now())
    await asyncio.sleep(0)
    assert not manual.done()

    gate.release.set()
    assert await manual is True
    assert await background is True
    assert gate.thresholds == [Decimal("44444")]


async def test_manual_scan_reports_skipped_while_the_module_is_disabled(database, monkeypatch):
    scanner = build_scanner(database)
    gate = ScanGate()
    monkeypatch.setattr(scanner, "_scan", gate.scan)
    async with database.sessions() as session:
        row = await session.get(WhaleSettings, 1)
        assert row is not None
        row.enabled = False
        await session.commit()

    assert await scanner.scan_now() is False
    assert gate.thresholds == []
