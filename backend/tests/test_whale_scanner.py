from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from backend.config import Settings
from backend.db import Database
from backend.models import (
    WhaleEntry,
    WhaleEntryRuleState,
    WhaleExclusion,
    WhaleMarket,
    WhaleSettings,
    WhaleTrade,
    WhaleWallet,
)
from backend.polymarket import (
    LargeTradeSnapshot,
    PositionSnapshot,
    WhaleHolderSnapshot,
    WhaleMarketPositionSnapshot,
    WhaleMarketSnapshot,
)
from backend.time_utils import utcnow
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


async def test_incremental_trade_collection_discards_provider_rows_before_cursor(database):
    start = utcnow() - timedelta(minutes=5)
    base = {
        "proxy_wallet": "0x1111111111111111111111111111111111111111",
        "asset_id": "asset-yes",
        "condition_id": "0x" + "a" * 64,
        "side": "BUY",
        "size": Decimal("2000"),
        "price": Decimal("0.50"),
        "amount": Decimal("1000"),
        "title": "Cursor market",
        "outcome": "Yes",
        "outcome_index": 0,
        "market_slug": "cursor-market",
        "event_slug": "cursor-event",
        "icon_url": None,
        "display_name": "Cursor Wallet",
    }

    class Client:
        async def fetch_large_trades(self, **_: Any) -> list[LargeTradeSnapshot]:
            return [
                LargeTradeSnapshot(
                    **base,
                    timestamp=start + timedelta(seconds=1),
                    transaction_hash="0xnew",
                ),
                LargeTradeSnapshot(
                    **base,
                    timestamp=start - timedelta(seconds=1),
                    transaction_hash="0xold",
                ),
            ]

    scanner = WhaleDiscoveryScanner(
        database=database,
        client=Client(),  # type: ignore[arg-type]
        settings=database.settings,
    )
    trades, hit_page_limit = await scanner._collect_trades(
        start=start,
        amount=Decimal("1000"),
    )

    assert [trade.transaction_hash for trade in trades] == ["0xnew"]
    assert hit_page_limit is False


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


class PositionDiscoveryClient:
    def __init__(self) -> None:
        self.condition_id = "0x" + "a" * 64
        self.asset_id = "asset-yes"
        self.wallet = "0x1111111111111111111111111111111111111111"
        self.ghost_wallet = "0xa5ef39c3d3e10d0b270233af41cac69796b12966"
        self.profile_created_at = utcnow() - timedelta(days=2)
        self.trade_amount = Decimal("150000")
        self.trade_timestamp = utcnow() - timedelta(minutes=10)
        self.transaction_hash = "0xtrade"
        self.current_position_size = Decimal("240000")
        self.profile_available = True
        self.position_available = True
        self.position_error = False
        self.trade_outcome_index = 0
        self.trade_calls = 0
        self.holder_calls: list[dict[str, Any]] = []
        self.position_calls: list[str] = []

    async def fetch_active_whale_markets(
        self, *, min_liquidity_usdc: Decimal, min_volume_usdc: Decimal
    ) -> list[WhaleMarketSnapshot]:
        assert min_liquidity_usdc == Decimal("5000")
        assert min_volume_usdc == Decimal("10000")
        return [
            WhaleMarketSnapshot(
                condition_id=self.condition_id,
                title="Official position market",
                market_slug="official-position-market",
                event_slug="official-position-event",
                icon_url=None,
                tags=(),
                closed=False,
                active=True,
                accepting_orders=True,
                neg_risk=False,
                end_date=utcnow() + timedelta(hours=4),
                end_date_is_date_only=False,
                outcomes=("Yes", "No"),
                outcome_prices=(Decimal("0.60"), Decimal("0.40")),
                clob_token_ids=(self.asset_id, "asset-no"),
                liquidity=Decimal("50000"),
                volume_24h=Decimal("100000"),
                best_bid=Decimal("0.59"),
                best_ask=Decimal("0.61"),
                order_min_size=Decimal("5"),
                tick_size=Decimal("0.01"),
                fee_rate=Decimal("0"),
                fee_exponent=Decimal("1"),
            )
        ]

    async def fetch_markets_with_tags(self, condition_ids: list[str]) -> list[WhaleMarketSnapshot]:
        assert condition_ids == [self.condition_id]
        return await self.fetch_active_whale_markets(
            min_liquidity_usdc=Decimal("5000"),
            min_volume_usdc=Decimal("10000"),
        )

    async def fetch_top_holders(
        self,
        condition_ids: list[str],
        *,
        min_balance: int,
        limit: int,
    ) -> list[WhaleHolderSnapshot]:
        self.holder_calls.append(
            {
                "condition_ids": condition_ids,
                "min_balance": min_balance,
                "limit": limit,
            }
        )
        return [
            WhaleHolderSnapshot(
                proxy_wallet=self.wallet,
                asset_id=self.asset_id,
                amount=Decimal("30000"),
                outcome_index=0,
                display_name="Official Whale",
                profile_image_url="https://example.test/whale.png",
                verified_badge=True,
            ),
            WhaleHolderSnapshot(
                proxy_wallet=self.ghost_wallet,
                asset_id=self.asset_id,
                amount=Decimal("300000000"),
                outcome_index=0,
                display_name=None,
                profile_image_url=None,
                verified_badge=False,
            ),
        ]

    async def fetch_market_positions(
        self, condition_id: str, *, limit: int
    ) -> list[WhaleMarketPositionSnapshot]:
        assert limit == 20
        self.position_calls.append(condition_id)
        return [
            WhaleMarketPositionSnapshot(
                proxy_wallet=self.wallet,
                asset_id=self.asset_id,
                condition_id=self.condition_id,
                outcome="Yes",
                outcome_index=0,
                size=Decimal("300000"),
                total_bought=Decimal("30000"),
                avg_price=Decimal("0.50"),
                current_price=Decimal("0.60"),
                current_value=Decimal("18000"),
                display_name="Official Whale",
                profile_image_url="https://example.test/whale.png",
                verified_badge=True,
            ),
            WhaleMarketPositionSnapshot(
                proxy_wallet=self.ghost_wallet,
                asset_id=self.asset_id,
                condition_id=self.condition_id,
                outcome="Yes",
                outcome_index=0,
                size=Decimal("300000000"),
                total_bought=Decimal("0"),
                avg_price=Decimal("0.50"),
                current_price=Decimal("0.60"),
                current_value=Decimal("180000000"),
                display_name=None,
                profile_image_url=None,
                verified_badge=False,
            ),
        ]

    async def fetch_public_profile(self, address: str) -> SimpleNamespace | None:
        assert address == self.wallet
        if not self.profile_available:
            return None
        return SimpleNamespace(
            display_name="Official Whale",
            pseudonym="Official-Whale",
            profile_image_url="https://example.test/whale.png",
            created_at=self.profile_created_at,
            verified_badge=True,
            taker_tier=1,
            taker_tier_name="Tier 1",
            weighted_volume=Decimal("12345"),
        )

    async def fetch_tags(self) -> list[Any]:
        return []

    async def fetch_large_trades(self, **_: Any) -> list[Any]:
        self.trade_calls += 1
        return [
            LargeTradeSnapshot(
                proxy_wallet=self.wallet,
                asset_id=self.asset_id,
                condition_id=self.condition_id,
                side="BUY",
                size=self.trade_amount / Decimal("0.50"),
                price=Decimal("0.50"),
                amount=self.trade_amount,
                timestamp=self.trade_timestamp,
                title="Official position market",
                outcome="Yes",
                outcome_index=self.trade_outcome_index,
                market_slug="official-position-market",
                event_slug="official-position-event",
                icon_url=None,
                display_name="Official Whale",
                transaction_hash=self.transaction_hash,
            )
        ]

    async def fetch_active_positions(self, user: str) -> list[PositionSnapshot]:
        assert user == self.wallet
        if self.position_error:
            raise RuntimeError("positions unavailable")
        if not self.position_available:
            return []
        return [
            PositionSnapshot(
                asset_id=self.asset_id,
                condition_id=self.condition_id,
                title="Official position market",
                outcome="Yes",
                outcome_index=0,
                icon_url=None,
                event_slug="official-position-event",
                market_slug="official-position-market",
                size=self.current_position_size,
                avg_price=Decimal("0.50"),
                current_price=Decimal("0.60"),
                initial_value=self.current_position_size * Decimal("0.50"),
                current_value=self.current_position_size * Decimal("0.60"),
                cash_pnl=self.current_position_size * Decimal("0.10"),
                percent_pnl=Decimal("20"),
                total_bought=self.trade_amount / Decimal("0.50"),
                realized_pnl=Decimal("0"),
                end_date=utcnow() + timedelta(hours=4),
            )
        ]


async def test_scanner_finds_recent_large_buyers_and_confirms_current_position(database):
    client = PositionDiscoveryClient()
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,  # type: ignore[arg-type]
        settings=database.settings,
    )

    completed = await scanner.tick()

    async with database.sessions() as session:
        entries = list((await session.scalars(select(WhaleEntry))).all())
        states = list((await session.scalars(select(WhaleEntryRuleState))).all())
        wallet = await session.get(WhaleWallet, client.wallet)
        settings = await session.get(WhaleSettings, 1)
    assert completed is True, settings.last_scan_error if settings is not None else None
    assert len(entries) == 1
    entry = entries[0]
    assert entry.proxy_wallet == client.wallet
    assert entry.net_size == Decimal("240000")
    assert entry.avg_buy_price == Decimal("0.50")
    assert entry.gross_buy_usdc == Decimal("150000")
    assert entry.status == "holding"
    assert [(state.rule_type, state.active) for state in states] == [("new_account", True)]
    assert wallet is not None
    assert wallet.display_name == "Official Whale"
    assert wallet.profile_created_at == client.profile_created_at
    assert wallet.profile_missing is False
    assert wallet.verified_badge is True
    assert settings is not None
    assert settings.last_scan_error is None
    assert client.trade_calls == 1


async def test_scanner_uses_market_token_mapping_when_trade_outcome_index_is_invalid(database):
    client = PositionDiscoveryClient()
    client.trade_outcome_index = 999
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,  # type: ignore[arg-type]
        settings=database.settings,
    )

    assert await scanner.tick() is True

    async with database.sessions() as session:
        entry = await session.scalar(select(WhaleEntry))
    assert entry is not None
    assert entry.asset_id == client.asset_id
    assert entry.outcome_index == 0


async def test_scanner_restores_incremental_watermark_from_persisted_trades(
    database,
    monkeypatch,
):
    client = PositionDiscoveryClient()
    first_scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,  # type: ignore[arg-type]
        settings=database.settings,
    )
    assert await first_scanner.tick() is True

    starts = []

    async def no_new_trades(**kwargs: Any) -> list[Any]:
        starts.append(kwargs["start"])
        return []

    monkeypatch.setattr(client, "fetch_large_trades", no_new_trades)
    restarted_scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,  # type: ignore[arg-type]
        settings=database.settings,
    )
    assert await restarted_scanner.tick() is True
    assert starts == [client.trade_timestamp - timedelta(seconds=120)]


async def test_profile_refresh_honors_per_scan_limit(database):
    calls: list[str] = []

    class ProfileClient:
        async def fetch_public_profile(self, address: str) -> None:
            calls.append(address)
            return None

    scanner = WhaleDiscoveryScanner(
        database=database,
        client=ProfileClient(),  # type: ignore[arg-type]
        settings=replace(database.settings, whale_profile_batch_limit=2),
    )
    wallets = [f"0x{index:040x}" for index in range(1, 6)]
    await scanner._refresh_wallets(wallets, now=utcnow(), cache_hours=24)

    assert calls == wallets[:2]


async def test_scanner_retains_raw_trade_but_skips_excluded_wallet_external_checks(database):
    client = PositionDiscoveryClient()
    async with database.sessions() as session:
        session.add(
            WhaleExclusion(
                proxy_wallet=client.wallet,
                label="Excluded Whale",
                created_at=utcnow(),
            )
        )
        await session.commit()

    async def unexpected_profile_check(address: str) -> None:
        raise AssertionError(f"excluded profile should not be checked: {address}")

    async def unexpected_position_check(user: str) -> list[PositionSnapshot]:
        raise AssertionError(f"excluded positions should not be checked: {user}")

    client.fetch_public_profile = unexpected_profile_check  # type: ignore[method-assign]
    client.fetch_active_positions = unexpected_position_check  # type: ignore[method-assign]
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,  # type: ignore[arg-type]
        settings=database.settings,
    )

    assert await scanner.tick() is True

    async with database.sessions() as session:
        trades = list((await session.scalars(select(WhaleTrade))).all())
        entries = list((await session.scalars(select(WhaleEntry))).all())
        wallet = await session.get(WhaleWallet, client.wallet)
    assert len(trades) == 1
    assert trades[0].proxy_wallet == client.wallet
    assert entries == []
    assert wallet is None


async def test_scanner_excludes_wallets_older_than_registration_window(database):
    client = PositionDiscoveryClient()
    client.profile_created_at = utcnow() - timedelta(days=8)
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,  # type: ignore[arg-type]
        settings=database.settings,
    )

    assert await scanner.tick() is True

    async with database.sessions() as session:
        entries = list((await session.scalars(select(WhaleEntry))).all())
    assert entries == []


@pytest.mark.parametrize(
    ("profile_age_days", "profile_available", "amount", "expected_rules"),
    [
        (2, True, "150000", {"new_account"}),
        (8, True, "600000", {"large_amount"}),
        (2, True, "600000", {"new_account", "large_amount"}),
        (2, False, "600000", {"large_amount"}),
    ],
)
async def test_scanner_applies_dual_rules_to_one_canonical_entry(
    database,
    profile_age_days,
    profile_available,
    amount,
    expected_rules,
):
    client = PositionDiscoveryClient()
    client.profile_created_at = utcnow() - timedelta(days=profile_age_days)
    client.profile_available = profile_available
    client.trade_amount = Decimal(amount)
    client.current_position_size = client.trade_amount / Decimal("0.50") * Decimal("0.80")
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,
        settings=database.settings,  # type: ignore[arg-type]
    )

    assert await scanner.tick() is True

    async with database.sessions() as session:
        entries = list((await session.scalars(select(WhaleEntry))).all())
        states = list((await session.scalars(select(WhaleEntryRuleState))).all())
    assert len(entries) == 1
    assert {state.entry_id for state in states} == {entries[0].id}
    assert {state.rule_type for state in states} == expected_rules
    assert all(state.active for state in states)


async def test_trigger_is_kept_as_history_when_position_was_already_exited(database):
    client = PositionDiscoveryClient()
    client.trade_amount = Decimal("600000")
    client.position_available = False
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,
        settings=database.settings,  # type: ignore[arg-type]
    )

    assert await scanner.tick() is True

    async with database.sessions() as session:
        entry = await session.scalar(select(WhaleEntry))
        states = list((await session.scalars(select(WhaleEntryRuleState))).all())
    assert entry is not None
    assert entry.status == "exited"
    assert entry.net_size == Decimal("0")
    assert {state.rule_type for state in states} == {"new_account", "large_amount"}
    assert all(not state.active for state in states)
    assert {state.inactive_reason for state in states} == {"position_exited"}


async def test_trigger_remains_current_after_buy_rolls_out_of_24_hour_window(
    database,
    monkeypatch,
):
    client = PositionDiscoveryClient()
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,
        settings=database.settings,  # type: ignore[arg-type]
    )
    assert await scanner.tick() is True
    async with database.sessions() as session:
        trades = list((await session.scalars(select(WhaleTrade))).all())
        for trade in trades:
            trade.timestamp = utcnow() - timedelta(hours=25)
        await session.commit()

    async def no_new_trades(**_: Any) -> list[Any]:
        return []

    monkeypatch.setattr(client, "fetch_large_trades", no_new_trades)
    assert await scanner.tick() is True

    async with database.sessions() as session:
        entry = await session.scalar(select(WhaleEntry))
        state = await session.scalar(select(WhaleEntryRuleState))
    assert entry is not None and entry.status == "holding"
    assert state is not None and state.active is True


async def test_monitored_build_time_is_frozen_when_rolling_window_moves(database):
    client = PositionDiscoveryClient()
    client.trade_timestamp = utcnow() - timedelta(hours=23)
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,
        settings=database.settings,  # type: ignore[arg-type]
    )
    assert await scanner.tick() is True
    async with database.sessions() as session:
        entry = await session.scalar(select(WhaleEntry))
        assert entry is not None
        monitored_build_at = entry.first_buy_at
        trades = list((await session.scalars(select(WhaleTrade))).all())
        for trade in trades:
            trade.timestamp = utcnow() - timedelta(hours=25)
        await session.commit()

    client.trade_timestamp = utcnow() - timedelta(minutes=5)
    client.transaction_hash = "0xnew-add"
    assert await scanner.tick() is True

    async with database.sessions() as session:
        entry = await session.scalar(select(WhaleEntry))
    assert entry is not None
    assert entry.first_buy_at == monitored_build_at
    assert entry.last_buy_at == client.trade_timestamp


async def test_position_api_failure_preserves_last_holding_state(database):
    client = PositionDiscoveryClient()
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,
        settings=database.settings,  # type: ignore[arg-type]
    )
    assert await scanner.tick() is True
    client.position_error = True

    assert await scanner.tick() is True

    async with database.sessions() as session:
        entry = await session.scalar(select(WhaleEntry))
        state = await session.scalar(select(WhaleEntryRuleState))
        settings = await session.get(WhaleSettings, 1)
    assert entry is not None
    assert entry.status == "holding"
    assert entry.net_size == Decimal("240000")
    assert entry.follow_eligible is False
    assert entry.follow_ineligible_reason == "position_check_failed"
    assert state is not None and state.active is True
    assert settings is not None
    assert "已保留上次状态" in (settings.last_scan_error or "")


async def test_settlement_is_mapped_to_the_entry_outcome_and_deactivates_rule(database):
    client = PositionDiscoveryClient()
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,
        settings=database.settings,  # type: ignore[arg-type]
    )
    assert await scanner.tick() is True
    now = utcnow()
    async with database.sessions() as session:
        market = await session.get(WhaleMarket, client.condition_id)
        assert market is not None
        market.closed = True
        market.active = False
        market.accepting_orders = False
        market.outcome_prices_json = '["1", "0"]'
        await session.commit()

    await scanner._update_entry_settlements(now=now)

    async with database.sessions() as session:
        entry = await session.scalar(select(WhaleEntry))
        state = await session.scalar(select(WhaleEntryRuleState))
    assert entry is not None
    assert entry.settlement_price == Decimal("1")
    assert entry.settled_at == now
    assert state is not None
    assert state.active is False
    assert state.inactive_reason == "market_closed"


async def test_settlement_repairs_invalid_outcome_index_from_market_token_mapping(database):
    client = PositionDiscoveryClient()
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,
        settings=database.settings,  # type: ignore[arg-type]
    )
    assert await scanner.tick() is True
    now = utcnow()
    async with database.sessions() as session:
        entry = await session.scalar(select(WhaleEntry))
        market = await session.get(WhaleMarket, client.condition_id)
        assert entry is not None
        assert market is not None
        entry.outcome_index = 999
        market.closed = True
        market.active = False
        market.accepting_orders = False
        market.outcome_prices_json = '["0", "1"]'
        await session.commit()

    await scanner._update_entry_settlements(now=now)

    async with database.sessions() as session:
        entry = await session.scalar(select(WhaleEntry))
    assert entry is not None
    assert entry.outcome_index == 0
    assert entry.settlement_price == Decimal("0")
    assert entry.settled_at == now
