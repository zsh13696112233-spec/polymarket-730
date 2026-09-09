from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from backend.config import Settings
from backend.db import Database
from backend.models import (
    EmailRecipient,
    EmailSettings,
    WhaleAutoFollowDecision,
    WhaleAutoMarketLock,
    WhaleBackfillSignalState,
    WhaleEmailDelivery,
    WhaleEntry,
    WhaleEntryRuleState,
    WhaleExclusion,
    WhaleFollowPosition,
    WhaleMarket,
    WhaleMarketScanPage,
    WhaleMarketScanState,
    WhaleOrder,
    WhaleScanRun,
    WhaleSettings,
    WhaleTrade,
    WhaleWallet,
)
from backend.polymarket import (
    LargeTradeSnapshot,
    OfficialTag,
    PolymarketAPIError,
    PositionSnapshot,
    WhaleHolderSnapshot,
    WhaleMarketPositionSnapshot,
    WhaleMarketSnapshot,
)
from backend.time_utils import utcnow
from backend.whale import (
    AutoFollowQuoteRejected,
    WhaleAggregate,
    WhaleDiscoveryScanner,
    _auto_follow_price,
    _auto_follow_price_band_changed,
    _auto_follow_reason_display,
    _decimal_display,
    _select_auto_follow_amount,
)
from backend.whale_email import (
    WhaleEmailCandidate,
    WhaleEmailNotifier,
    enqueue_due_weekly_summary,
    enqueue_whale_email_deliveries,
    list_whale_email_deliveries,
    weekly_email_summary_metrics,
    weekly_summary_period,
)
from backend.whale_requests import WhaleRequestMonitor


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


async def enable_email_notifications(database: Database) -> None:
    now = utcnow()
    async with database.sessions() as session:
        session.add(
            EmailSettings(
                id=1,
                notifications_enabled=True,
                smtp_host=None,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            EmailRecipient(
                email="alerts@example.com",
                enabled=True,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()


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
        end=start + timedelta(minutes=10),
        amount=Decimal("1000"),
    )

    assert [trade.transaction_hash for trade in trades] == ["0xnew"]
    assert hit_page_limit is False


async def test_rate_limit_retry_uses_exponential_fallback(database, monkeypatch):
    scanner = build_scanner(database)
    attempts = 0
    delays: list[float] = []

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PolymarketAPIError("Polymarket 接口请求过于频繁", rate_limited=True)
        return "ok"

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("backend.whale.asyncio.sleep", record_sleep)

    assert await scanner._retry(operation) == "ok"
    assert delays == [2.0, 4.0]


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

    async def fetch_discovery_markets(self, **kwargs):
        return []

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

    async def fetch_active_positions(
        self,
        user: str,
        *,
        condition_ids: Iterable[str] | None = None,
    ) -> list[PositionSnapshot]:
        assert user == self.wallet
        if self.position_error:
            raise RuntimeError("positions unavailable")
        if not self.position_available:
            return []
        positions = [
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
        conditions = set(condition_ids or [])
        return [item for item in positions if not conditions or item.condition_id in conditions]


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


async def test_scanner_blocks_auto_follow_while_trade_coverage_is_incomplete(database):
    client = PositionDiscoveryClient()
    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        assert settings is not None
        settings.new_account_auto_follow_enabled = True
        settings.coverage_incomplete_until = utcnow() + timedelta(hours=24)
        await session.commit()

    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,  # type: ignore[arg-type]
        settings=database.settings,
    )

    assert await scanner.tick() is True
    async with database.sessions() as session:
        decisions = list(await session.scalars(select(WhaleAutoFollowDecision)))
        entry = await session.scalar(select(WhaleEntry))
        settings = await session.get(WhaleSettings, 1)
        run = await session.scalar(select(WhaleScanRun))

    assert decisions == []
    assert entry is not None
    assert entry.follow_eligible is False
    assert entry.follow_ineligible_reason == "coverage_incomplete"
    assert settings is not None
    assert "自动跟单暂停" in (settings.last_scan_error or "")
    assert run is not None
    assert run.status == "degraded"
    assert run.coverage_complete is True


async def test_scanner_enqueues_one_combined_email_per_recipient_for_dual_trigger(database):
    client = PositionDiscoveryClient()
    await enable_email_notifications(database)
    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        assert settings is not None
        settings.large_amount_threshold_usdc = Decimal("100000")
        await session.commit()

    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,  # type: ignore[arg-type]
        settings=database.settings,
    )
    assert await scanner.tick() is True
    assert await scanner.tick() is True

    async with database.sessions() as session:
        deliveries = list(await session.scalars(select(WhaleEmailDelivery)))
    assert len(deliveries) == 1
    assert deliveries[0].rule_key == "new_account,large_amount"
    assert "新号大额 + 全量超大额" in deliveries[0].body_text
    assert "买入均价：0.5 USDC（50¢）" in deliveries[0].body_text

    async with database.sessions() as session:
        entry = await session.get(WhaleEntry, deliveries[0].entry_id)
        assert entry is not None
        entry.settlement_price = Decimal("1")
        await session.commit()

    delivery_log = await list_whale_email_deliveries(
        database,
        status="all",
        limit=50,
        offset=0,
    )
    assert delivery_log["items"][0]["subject"] == deliveries[0].subject
    assert delivery_log["items"][0]["body_text"] == deliveries[0].body_text
    assert delivery_log["items"][0]["market_summaries"] == [
        {
            "category_label": "其他",
            "outcome": "Yes",
            "avg_buy_price": Decimal("0.50"),
            "gross_buy_usdc": Decimal("150000"),
        }
    ]
    assert delivery_log["items"][0]["result"] == "hit"


async def seed_email_entry(
    database: Database,
    *,
    condition_id: str,
    asset_id: str,
    outcome: str,
    outcome_index: int,
    wallet_address: str,
    wallet_name: str,
    amount: str,
    avg_price: str,
) -> int:
    now = utcnow()
    async with database.sessions() as session:
        if await session.get(WhaleMarket, condition_id) is None:
            session.add(
                WhaleMarket(
                    condition_id=condition_id,
                    title="Real Madrid CF vs. Real Sociedad de Fútbol: O/U 3.5",
                    tags_json='[{"slug":"sports"}]',
                    outcomes_json='["Over","Under"]',
                    outcome_prices_json='["0.51","0.49"]',
                    clob_token_ids_json='["asset-over","asset-under"]',
                    refreshed_at=now,
                )
            )
        if await session.get(WhaleWallet, wallet_address) is None:
            session.add(
                WhaleWallet(
                    proxy_wallet=wallet_address,
                    display_name=wallet_name,
                    refreshed_at=now,
                )
            )
        entry = WhaleEntry(
            proxy_wallet=wallet_address,
            asset_id=asset_id,
            condition_id=condition_id,
            outcome=outcome,
            outcome_index=outcome_index,
            gross_buy_usdc=Decimal(amount),
            gross_buy_size=Decimal(amount) / Decimal(avg_price),
            net_size=Decimal(amount) / Decimal(avg_price),
            net_ratio=Decimal("100"),
            avg_buy_price=Decimal(avg_price),
            max_single_usdc=Decimal(amount),
            trade_count=1,
            first_buy_at=now,
            last_buy_at=now,
            status="holding",
            window_start=now - timedelta(hours=24),
            computed_at=now,
        )
        session.add(entry)
        await session.flush()
        session.add(
            WhaleEntryRuleState(
                entry_id=entry.id,
                rule_type="large_amount",
                active=True,
                first_triggered_at=now,
                last_qualified_at=now,
                threshold_usdc_snapshot=Decimal("500000"),
            )
        )
        await session.commit()
        return entry.id


async def test_same_scan_opposite_large_entries_enqueue_only_divergence_per_recipient(database):
    await enable_email_notifications(database)
    now = utcnow()
    async with database.sessions() as session:
        session.add(
            EmailRecipient(
                email="ops@example.com",
                enabled=True,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    condition_id = "0x" + "d" * 64
    under_id = await seed_email_entry(
        database,
        condition_id=condition_id,
        asset_id="asset-under",
        outcome="Under",
        outcome_index=1,
        wallet_address="0x2222222222222222222222222222222222222222",
        wallet_name="BreakTheBank",
        amount="512653.33",
        avg_price="0.4825",
    )
    over_id = await seed_email_entry(
        database,
        condition_id=condition_id,
        asset_id="asset-over",
        outcome="Over",
        outcome_index=0,
        wallet_address="0x3333333333333333333333333333333333333333",
        wallet_name="ripley86alien",
        amount="521964.41",
        avg_price="0.5189",
    )
    candidates = [
        WhaleEmailCandidate(under_id, frozenset({"large_amount"})),
        WhaleEmailCandidate(over_id, frozenset({"large_amount"})),
    ]

    async with database.sessions() as session:
        await enqueue_whale_email_deliveries(
            session,
            candidates=candidates,
            triggered_at=now,
        )
        await session.commit()
    async with database.sessions() as session:
        await enqueue_whale_email_deliveries(
            session,
            candidates=candidates,
            triggered_at=now,
        )
        await session.commit()

    async with database.sessions() as session:
        deliveries = list(await session.scalars(select(WhaleEmailDelivery)))
    assert len(deliveries) == 2
    assert {delivery.recipient_email for delivery in deliveries} == {
        "alerts@example.com",
        "ops@example.com",
    }
    delivery = deliveries[0]
    assert delivery.notification_kind == "divergence"
    assert delivery.entry_id is None
    assert delivery.dedupe_key == f"divergence:{condition_id}"
    assert "[PolyCopy] 分歧市场提醒" in delivery.subject
    assert "Under：512,653.33 USDC（49.55%）" in delivery.body_text
    assert "Over：521,964.41 USDC（50.45%）" in delivery.body_text
    assert "方向高度分歧" in delivery.body_text

    delivery_log = await list_whale_email_deliveries(
        database,
        status="all",
        limit=50,
        offset=0,
    )
    assert delivery_log["total"] == 2
    assert delivery_log["items"][0]["notification_kind"] == "divergence"
    assert delivery_log["items"][0]["entry_ids"] == sorted([under_id, over_id])
    assert delivery_log["items"][0]["market_summaries"] == [
        {
            "category_label": "传统体育",
            "outcome": "Over",
            "avg_buy_price": Decimal("0.5189"),
            "gross_buy_usdc": Decimal("521964.41"),
        },
        {
            "category_label": "传统体育",
            "outcome": "Under",
            "avg_buy_price": Decimal("0.4825"),
            "gross_buy_usdc": Decimal("512653.33"),
        },
    ]
    assert delivery_log["items"][0]["result"] == "not_applicable"


async def test_later_opposite_large_entry_upgrades_prior_single_email(database):
    await enable_email_notifications(database)
    now = utcnow()
    condition_id = "0x" + "e" * 64
    under_id = await seed_email_entry(
        database,
        condition_id=condition_id,
        asset_id="asset-under",
        outcome="Under",
        outcome_index=1,
        wallet_address="0x4444444444444444444444444444444444444444",
        wallet_name="Under Whale",
        amount="510000",
        avg_price="0.49",
    )
    async with database.sessions() as session:
        await enqueue_whale_email_deliveries(
            session,
            candidates=[WhaleEmailCandidate(under_id, frozenset({"large_amount"}))],
            triggered_at=now,
        )
        await session.commit()

    over_id = await seed_email_entry(
        database,
        condition_id=condition_id,
        asset_id="asset-over",
        outcome="Over",
        outcome_index=0,
        wallet_address="0x5555555555555555555555555555555555555555",
        wallet_name="Over Whale",
        amount="520000",
        avg_price="0.51",
    )
    async with database.sessions() as session:
        await enqueue_whale_email_deliveries(
            session,
            candidates=[WhaleEmailCandidate(over_id, frozenset({"large_amount"}))],
            triggered_at=now + timedelta(minutes=10),
        )
        await session.commit()

    async with database.sessions() as session:
        deliveries = list(
            await session.scalars(select(WhaleEmailDelivery).order_by(WhaleEmailDelivery.id))
        )
    assert len(deliveries) == 2
    assert [delivery.notification_kind for delivery in deliveries] == ["entry", "divergence"]
    assert "市场状态升级：方向分歧" in deliveries[1].subject
    assert all(delivery.entry_id != over_id for delivery in deliveries)


async def test_same_wallet_two_sides_is_not_treated_as_market_divergence(database):
    await enable_email_notifications(database)
    now = utcnow()
    condition_id = "0x" + "f" * 64
    wallet = "0x6666666666666666666666666666666666666666"
    under_id = await seed_email_entry(
        database,
        condition_id=condition_id,
        asset_id="asset-under",
        outcome="Under",
        outcome_index=1,
        wallet_address=wallet,
        wallet_name="Hedging Whale",
        amount="510000",
        avg_price="0.49",
    )
    over_id = await seed_email_entry(
        database,
        condition_id=condition_id,
        asset_id="asset-over",
        outcome="Over",
        outcome_index=0,
        wallet_address=wallet,
        wallet_name="Hedging Whale",
        amount="520000",
        avg_price="0.51",
    )
    async with database.sessions() as session:
        await enqueue_whale_email_deliveries(
            session,
            candidates=[
                WhaleEmailCandidate(under_id, frozenset({"large_amount"})),
                WhaleEmailCandidate(over_id, frozenset({"large_amount"})),
            ],
            triggered_at=now,
        )
        await session.commit()

    async with database.sessions() as session:
        deliveries = list(await session.scalars(select(WhaleEmailDelivery)))
    assert len(deliveries) == 2
    assert {delivery.notification_kind for delivery in deliveries} == {"entry"}


async def test_email_notifier_marks_delivery_sent(database, monkeypatch):
    client = PositionDiscoveryClient()
    await enable_email_notifications(database)
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,  # type: ignore[arg-type]
        settings=database.settings,
    )
    assert await scanner.tick() is True

    notifier = WhaleEmailNotifier(
        database=database,
        settings=replace(
            database.settings,
            smtp_host="smtp.example.com",
            smtp_from_email="sender@example.com",
            smtp_username="sender@example.com",
            smtp_password="authorization-code",
        ),
    )
    sent: list[str] = []
    monkeypatch.setattr(
        notifier,
        "_send",
        lambda delivery, _transport: sent.append(delivery.recipient_email),
    )
    assert await notifier.deliver_once() is True

    async with database.sessions() as session:
        delivery = await session.scalar(select(WhaleEmailDelivery))
    assert delivery is not None
    assert delivery.status == "sent"
    assert delivery.attempt_count == 1
    assert sent == ["alerts@example.com"]


async def test_weekly_summary_deduplicates_recipients_and_enqueues_once(database):
    await enable_email_notifications(database)
    report_time = datetime(2026, 8, 23, 16, 0)  # 周一 00:00，北京时间
    period_start = datetime(2026, 8, 16, 16, 0)
    hit_id = await seed_email_entry(
        database,
        condition_id="0x" + "1" * 64,
        asset_id="weekly-hit",
        outcome="Yes",
        outcome_index=0,
        wallet_address="0x1111111111111111111111111111111111111111",
        wallet_name="Hit Wallet",
        amount="100000",
        avg_price="0.50",
    )
    miss_id = await seed_email_entry(
        database,
        condition_id="0x" + "2" * 64,
        asset_id="weekly-miss",
        outcome="No",
        outcome_index=1,
        wallet_address="0x2222222222222222222222222222222222222222",
        wallet_name="Miss Wallet",
        amount="100000",
        avg_price="0.50",
    )
    special_id = await seed_email_entry(
        database,
        condition_id="0x" + "3" * 64,
        asset_id="weekly-special",
        outcome="Yes",
        outcome_index=0,
        wallet_address="0x3333333333333333333333333333333333333333",
        wallet_name="Special Wallet",
        amount="100000",
        avg_price="0.50",
    )
    pending_id = await seed_email_entry(
        database,
        condition_id="0x" + "4" * 64,
        asset_id="weekly-pending",
        outcome="Yes",
        outcome_index=0,
        wallet_address="0x4444444444444444444444444444444444444444",
        wallet_name="Pending Wallet",
        amount="100000",
        avg_price="0.50",
    )

    async with database.sessions() as session:
        settings = await session.get(EmailSettings, 1)
        assert settings is not None
        settings.weekly_summary_enabled = True
        settings.weekly_summary_enabled_at = period_start
        session.add(
            EmailRecipient(
                email="ops@example.com",
                enabled=True,
                created_at=period_start,
                updated_at=period_start,
            )
        )
        for entry_id, settlement_price, settled_at in (
            (hit_id, Decimal("1"), datetime(2026, 8, 18, 4, 0)),
            (miss_id, Decimal("0"), datetime(2026, 8, 19, 4, 0)),
            (special_id, Decimal("0.5"), datetime(2026, 8, 20, 4, 0)),
        ):
            entry = await session.get(WhaleEntry, entry_id)
            assert entry is not None
            entry.settlement_price = settlement_price
            entry.settled_at = settled_at
        for entry_id in (hit_id, miss_id, special_id, pending_id):
            entry = await session.get(WhaleEntry, entry_id)
            assert entry is not None
            for recipient in ("alerts@example.com", "ops@example.com"):
                session.add(
                    WhaleEmailDelivery(
                        entry_id=entry_id,
                        notification_kind="entry",
                        condition_id=entry.condition_id,
                        entry_ids_json=json.dumps([entry_id]),
                        dedupe_key=f"weekly-source:{entry_id}",
                        rule_key="large_amount",
                        rules_json='["large_amount"]',
                        recipient_email=recipient,
                        market_title="Weekly source",
                        wallet_label="Weekly wallet",
                        subject="Source alert",
                        body_text="Source alert body",
                        status="sent",
                        attempt_count=1,
                        created_at=period_start - timedelta(days=1),
                        sent_at=period_start - timedelta(days=1),
                    )
                )
        await session.commit()

    scheduled_for, computed_start, computed_end = weekly_summary_period(report_time)
    assert scheduled_for == report_time
    assert computed_start == period_start
    assert computed_end == report_time
    metrics = await weekly_email_summary_metrics(
        database,
        period_start=computed_start,
        period_end=computed_end,
    )
    assert metrics == {
        "settled_count": 3,
        "effective_count": 2,
        "hit_count": 1,
        "miss_count": 1,
        "special_count": 1,
        "pending_count": 1,
        "hit_rate_percent": Decimal("50"),
    }

    assert await enqueue_due_weekly_summary(database, now=report_time) == 2
    assert await enqueue_due_weekly_summary(database, now=report_time + timedelta(hours=1)) == 0
    async with database.sessions() as session:
        reports = list(
            await session.scalars(
                select(WhaleEmailDelivery).where(
                    WhaleEmailDelivery.notification_kind == "weekly_summary"
                )
            )
        )
    assert {report.recipient_email for report in reports} == {
        "alerts@example.com",
        "ops@example.com",
    }
    assert all("有效命中率：50%" in report.body_text for report in reports)
    assert all("特殊结算：1" in report.body_text for report in reports)
    assert all("仍待结算：1" in report.body_text for report in reports)
    delivery_log = await list_whale_email_deliveries(
        database,
        status="pending",
        limit=10,
        offset=0,
    )
    weekly_items = [
        item for item in delivery_log["items"] if item["notification_kind"] == "weekly_summary"
    ]
    assert len(weekly_items) == 2
    assert all(item["result"] == "not_applicable" for item in weekly_items)
    assert all(item["market_summaries"] == [] for item in weekly_items)


async def test_weekly_summary_waits_until_next_monday_after_midweek_enable(database):
    await enable_email_notifications(database)
    midweek = datetime(2026, 8, 19, 4, 0)
    async with database.sessions() as session:
        settings = await session.get(EmailSettings, 1)
        assert settings is not None
        settings.weekly_summary_enabled = True
        settings.weekly_summary_enabled_at = midweek
        await session.commit()

    assert await enqueue_due_weekly_summary(database, now=midweek) == 0
    assert await enqueue_due_weekly_summary(database, now=datetime(2026, 8, 23, 16, 0)) == 1
    async with database.sessions() as session:
        report = await session.scalar(
            select(WhaleEmailDelivery).where(
                WhaleEmailDelivery.notification_kind == "weekly_summary"
            )
        )
    assert report is not None
    assert "有效命中率：暂无有效样本" in report.body_text


async def test_email_notifier_retries_then_preserves_terminal_failure(database, monkeypatch):
    client = PositionDiscoveryClient()
    await enable_email_notifications(database)
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,  # type: ignore[arg-type]
        settings=database.settings,
    )
    assert await scanner.tick() is True
    notifier = WhaleEmailNotifier(
        database=database,
        settings=replace(
            database.settings,
            smtp_host="smtp.example.com",
            smtp_from_email="sender@example.com",
            smtp_username="sender@example.com",
            smtp_password="authorization-code",
        ),
    )

    def fail_send(_: WhaleEmailDelivery, _transport) -> None:
        raise OSError("SMTP unavailable")

    monkeypatch.setattr(notifier, "_send", fail_send)
    for attempt in range(1, 6):
        assert await notifier.deliver_once() is True
        async with database.sessions() as session:
            delivery = await session.scalar(select(WhaleEmailDelivery))
            assert delivery is not None
            assert delivery.attempt_count == attempt
            if attempt < 5:
                assert delivery.status == "retrying"
                assert delivery.next_attempt_at is not None
                delivery.next_attempt_at = utcnow()
                await session.commit()
    assert delivery.status == "failed"
    assert delivery.last_error == "SMTP unavailable"


async def test_scanner_does_not_notify_when_position_is_already_empty(database):
    client = PositionDiscoveryClient()
    client.current_position_size = Decimal("0")
    await enable_email_notifications(database)
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,  # type: ignore[arg-type]
        settings=database.settings,
    )
    assert await scanner.tick() is True
    async with database.sessions() as session:
        assert await session.scalar(select(WhaleEmailDelivery)) is None


async def test_163_smtp_connection_uses_keychain_authorization_code(database, monkeypatch):
    async with database.sessions() as session:
        now = utcnow()
        settings = EmailSettings(
            id=1,
            smtp_host="smtp.163.com",
            smtp_port=465,
            smtp_security="ssl",
            smtp_username="sender@163.com",
            smtp_from_email="sender@163.com",
            smtp_from_name="PolyCopy",
            smtp_keychain_service="com.polycopy.smtp",
            smtp_keychain_account="sender@163.com",
            created_at=now,
            updated_at=now,
        )
        session.add(settings)
        await session.commit()

    calls: list[tuple] = []

    class FakeKeychain:
        def get_secret(self, reference):
            calls.append(("secret", reference.service, reference.account))
            return "authorization-code"

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def login(self, username, password):
            calls.append(("login", username, password))

        def noop(self):
            calls.append(("noop",))
            return 250, b"OK"

        def send_message(self, message):
            calls.append(("send", message["To"], message["Subject"]))

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr("backend.whale_email.smtplib.SMTP_SSL", FakeSMTP)
    notifier = WhaleEmailNotifier(
        database=database,
        settings=database.settings,
        keychain=FakeKeychain(),  # type: ignore[arg-type]
    )
    result = await notifier.test_connection(recipient_email="receiver@example.com")

    assert result["message_sent"] is True
    assert ("connect", "smtp.163.com", 465, 15) in calls
    assert ("login", "sender@163.com", "authorization-code") in calls
    assert ("send", "receiver@example.com", "[PolyCopy] 163 邮件连接测试") in calls


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
    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        assert settings is not None
        persisted_cursor = settings.last_trade_cursor_at
    assert persisted_cursor is not None

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
    assert starts == [persisted_cursor - timedelta(seconds=120)]
    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        assert settings is not None
        assert settings.last_trade_cursor_at is not None
        assert settings.last_trade_cursor_at == persisted_cursor


@pytest.mark.parametrize("response_kind", ["empty", "stale"])
async def test_scanner_collects_delayed_trade_after_empty_or_stale_feed(
    database, monkeypatch, response_kind
):
    client = PositionDiscoveryClient()
    scan_time = utcnow()
    client.trade_timestamp = scan_time - timedelta(minutes=5)
    original_trade = (await client.fetch_large_trades())[0]
    delayed_trade = replace(
        original_trade,
        timestamp=original_trade.timestamp + timedelta(minutes=1),
        transaction_hash="0x" + "d" * 64,
    )
    response = [original_trade]

    async def fetch_trades(**_: Any) -> list[LargeTradeSnapshot]:
        return response

    monkeypatch.setattr(client, "fetch_large_trades", fetch_trades)
    monkeypatch.setattr("backend.whale.utcnow", lambda: scan_time)
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,  # type: ignore[arg-type]
        settings=database.settings,
    )
    assert await scanner.tick() is True

    scan_time += timedelta(minutes=1)
    response = (
        []
        if response_kind == "empty"
        else [replace(original_trade, timestamp=original_trade.timestamp - timedelta(seconds=30))]
    )
    assert await scanner.tick() is True
    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        assert settings.last_trade_cursor_at == original_trade.timestamp

    # A restart must preserve the trade watermark, including after an empty page.
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,  # type: ignore[arg-type]
        settings=database.settings,
    )
    scan_time += timedelta(minutes=1)
    response = [delayed_trade, original_trade]
    assert await scanner.tick() is True
    async with database.sessions() as session:
        stored = await session.scalar(
            select(WhaleTrade).where(WhaleTrade.transaction_hash == delayed_trade.transaction_hash)
        )
        settings = await session.get(WhaleSettings, 1)
        assert stored is not None
        assert stored.timestamp == delayed_trade.timestamp
        assert settings.last_trade_cursor_at == delayed_trade.timestamp


async def test_scanner_keeps_uninitialized_cursor_after_empty_feed(database, monkeypatch):
    client = PositionDiscoveryClient()

    async def no_trades(**_: Any) -> list[Any]:
        return []

    monkeypatch.setattr(client, "fetch_large_trades", no_trades)
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,  # type: ignore[arg-type]
        settings=database.settings,
    )
    assert await scanner.tick() is True
    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        assert settings.last_trade_cursor_at is None


async def test_scanner_does_not_advance_trade_cursor_after_collection_failure(
    database,
    monkeypatch,
):
    client = PositionDiscoveryClient()

    async def fail_collection(**_: Any) -> list[Any]:
        raise RuntimeError("trade collection failed")

    monkeypatch.setattr(client, "fetch_large_trades", fail_collection)
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,  # type: ignore[arg-type]
        settings=database.settings,
    )

    assert await scanner.tick() is False
    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        run = await session.scalar(select(WhaleScanRun))
        assert settings is not None
        assert settings.last_trade_cursor_at is None
        assert run is not None
        assert run.status == "failed"
        assert run.finished_at is not None
        assert run.error == "trade collection failed"


async def test_scanner_advances_cursor_but_marks_coverage_degraded_at_page_limit(
    database,
    monkeypatch,
):
    client = PositionDiscoveryClient()
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,  # type: ignore[arg-type]
        settings=database.settings,
    )

    async def page_limited(**_: Any) -> tuple[list[Any], bool]:
        return [], True

    monkeypatch.setattr(scanner, "_collect_trades", page_limited)

    assert await scanner.tick() is True
    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        run = await session.scalar(select(WhaleScanRun))
        assert settings is not None
        assert settings.last_trade_cursor_at is not None
        assert settings.coverage_incomplete_until is not None
        assert settings.coverage_incomplete_until > settings.last_trade_cursor_at
        assert "自动跟单暂停" in (settings.last_scan_error or "")
        assert run is not None
        assert run.status == "degraded"
        assert run.page_limit_hit is True
        assert run.coverage_complete is False
        assert run.requested_start is not None
        assert run.requested_end is not None


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

    client.trade_timestamp = utcnow() - timedelta(minutes=1)
    client.transaction_hash = "0xnew-add"
    assert await scanner.tick() is True

    async with database.sessions() as session:
        entry = await session.scalar(select(WhaleEntry))
    assert entry is not None
    assert entry.first_buy_at == monitored_build_at
    assert entry.last_buy_at == client.trade_timestamp


async def test_position_api_failure_preserves_last_holding_state(database, tmp_path):
    client = PositionDiscoveryClient()
    failure_log_path = tmp_path / "whale-failures.jsonl"
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,
        settings=database.settings,  # type: ignore[arg-type]
        request_monitor=WhaleRequestMonitor(failure_log_path=failure_log_path),
    )
    assert await scanner.tick() is True
    client.position_error = True
    scanner._position_cache.clear()  # Force the scheduled refresh to exercise failure handling.

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
    failure_log = failure_log_path.read_text(encoding="utf-8")
    failures = [json.loads(line) for line in failure_log.splitlines()]
    assert failures[-1] == {
        "logged_at": failures[-1]["logged_at"],
        "event": "position_verification_failed",
        "scan_id": failures[-1]["scan_id"],
        "wallet": client.wallet,
        "condition_ids": [client.condition_id],
        "error_type": "RuntimeError",
        "error_message": "positions unavailable",
        "failure_stage": "pre_order_position_verification",
        "auto_follow_blocked": True,
    }


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


async def test_scanner_refreshes_market_for_open_follow_position_without_entry(
    database,
    monkeypatch,
):
    client = PositionDiscoveryClient()
    now = utcnow()
    async with database.sessions() as session:
        session.add(
            WhaleFollowPosition(
                asset_id=client.asset_id,
                condition_id=client.condition_id,
                title="Chain test market",
                outcome="Yes",
                outcome_index=0,
                neg_risk=False,
                cycle_no=1,
                size=Decimal("5"),
                cost_usdc=Decimal("3"),
                lifetime_bought_size=Decimal("5"),
                lifetime_bought_usdc=Decimal("3"),
                lifetime_sold_size=Decimal("0"),
                lifetime_sold_usdc=Decimal("0"),
                lifetime_fee_usdc=Decimal("0"),
                realized_pnl=Decimal("0"),
                status="open",
                opened_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    active_market = (
        await client.fetch_active_whale_markets(
            min_liquidity_usdc=Decimal("5000"),
            min_volume_usdc=Decimal("10000"),
        )
    )[0]
    closed_market = replace(
        active_market,
        closed=True,
        accepting_orders=False,
        outcome_prices=(Decimal("0"), Decimal("1")),
    )
    active_calls: list[list[str]] = []
    closed_calls: list[list[str]] = []

    async def no_trades(**_: Any) -> list[Any]:
        return []

    async def active_markets(condition_ids: list[str]) -> list[WhaleMarketSnapshot]:
        active_calls.append(condition_ids)
        return []

    async def closed_markets(condition_ids: list[str]) -> list[WhaleMarketSnapshot]:
        closed_calls.append(condition_ids)
        return [closed_market]

    monkeypatch.setattr(client, "fetch_large_trades", no_trades)
    monkeypatch.setattr(client, "fetch_markets_with_tags", active_markets)
    monkeypatch.setattr(client, "fetch_closed_markets_with_tags", closed_markets, raising=False)
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,  # type: ignore[arg-type]
        settings=database.settings,
    )

    assert await scanner.tick() is True

    async with database.sessions() as session:
        assert await session.scalar(select(WhaleEntry)) is None
        market = await session.get(WhaleMarket, client.condition_id)
    assert active_calls == [[client.condition_id]]
    assert closed_calls == [[client.condition_id]]
    assert market is not None
    assert market.closed is True
    assert market.outcome_prices_json == '["0","1"]'


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


async def _auto_follow_config(database: Database, **updates: Any) -> dict[str, Any]:
    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        assert settings is not None
        for key, value in updates.items():
            setattr(settings, key, value)
        await session.commit()
        return {
            column.name: getattr(settings, column.name)
            for column in WhaleSettings.__table__.columns
        }


async def _seed_auto_market(database: Database, condition_id: str) -> None:
    now = utcnow()
    async with database.sessions() as session:
        session.add(
            WhaleMarket(
                condition_id=condition_id,
                title="自动跟单测试市场",
                market_slug="auto-follow-test",
                event_slug="auto-follow-test",
                outcomes_json='["Yes", "No"]',
                outcome_prices_json='["0.7", "0.3"]',
                clob_token_ids_json='["asset-yes", "asset-no"]',
                tags_json='[{"slug":"sports"}]',
                closed=False,
                active=True,
                accepting_orders=True,
                neg_risk=False,
                end_date=now + timedelta(days=1),
                end_date_is_date_only=False,
                liquidity=Decimal("10000"),
                volume_24h=Decimal("50000"),
                best_bid=Decimal("0.69"),
                best_ask=Decimal("0.70"),
                order_min_size=Decimal("5"),
                tick_size=Decimal("0.01"),
                fee_rate=Decimal("0"),
                fee_exponent=Decimal("0"),
                refreshed_at=now,
            )
        )
        await session.commit()


def _auto_aggregate(
    *,
    wallet: str,
    asset_id: str,
    condition_id: str,
    outcome: str = "Yes",
    outcome_index: int = 0,
) -> WhaleAggregate:
    now = utcnow()
    return WhaleAggregate(
        proxy_wallet=wallet,
        asset_id=asset_id,
        condition_id=condition_id,
        outcome=outcome,
        outcome_index=outcome_index,
        gross_buy_usdc=Decimal("600000"),
        gross_buy_size=Decimal("1000000"),
        sold_size=Decimal("0"),
        sold_usdc=Decimal("0"),
        net_size=Decimal("1000000"),
        net_ratio_percent=Decimal("100"),
        avg_buy_price=Decimal("0.60"),
        max_single_usdc=Decimal("600000"),
        trade_count=1,
        first_buy_at=now,
        last_buy_at=now,
        status="holding",
    )


async def _fill_auto_decision(
    database: Database,
    decision_id: int,
    *,
    position_id: int | None = None,
) -> int:
    now = utcnow()
    async with database.sessions() as session:
        decision = await session.get(WhaleAutoFollowDecision, decision_id)
        assert decision is not None
        position = (
            await session.get(WhaleFollowPosition, position_id) if position_id is not None else None
        )
        if position is None:
            position = WhaleFollowPosition(
                asset_id=decision.asset_id,
                condition_id=decision.condition_id,
                title="自动跟单测试市场",
                outcome=decision.outcome,
                outcome_index=decision.outcome_index,
                neg_risk=False,
                market_slug="auto-follow-test",
                event_slug="auto-follow-test",
                icon_url=None,
                source_wallet=decision.proxy_wallet,
                source_whale_avg_price=Decimal("0.60"),
                cycle_no=1,
                size=Decimal("0"),
                cost_usdc=Decimal("0"),
                lifetime_bought_size=Decimal("0"),
                lifetime_bought_usdc=Decimal("0"),
                lifetime_sold_size=Decimal("0"),
                lifetime_sold_usdc=Decimal("0"),
                lifetime_fee_usdc=Decimal("0"),
                realized_pnl=Decimal("0"),
                status="open",
                opened_at=now,
                closed_at=None,
                created_at=now,
                updated_at=now,
            )
            session.add(position)
            await session.flush()
        position.size += Decimal("5")
        position.cost_usdc += Decimal("3")
        position.lifetime_bought_size += Decimal("5")
        position.lifetime_bought_usdc += Decimal("3")
        position.updated_at = now
        order = WhaleOrder(
            position_id=position.id,
            entry_id=decision.entry_id,
            idempotency_key=f"auto:test-filled:{decision.id}",
            source="auto_follow",
            source_wallet=decision.proxy_wallet,
            asset_id=decision.asset_id,
            condition_id=decision.condition_id,
            title="自动跟单测试市场",
            outcome=decision.outcome,
            outcome_index=decision.outcome_index,
            neg_risk=False,
            side="BUY",
            requested_size=Decimal("5"),
            requested_usdc=Decimal("3"),
            limit_price=Decimal("0.60"),
            reference_price=Decimal("0.60"),
            whale_avg_price=Decimal("0.60"),
            filled_size=Decimal("5"),
            filled_usdc=Decimal("3"),
            fee_usdc=Decimal("0"),
            status="filled",
            reason=None,
            signed_order_hash=None,
            execution_provider="test",
            external_order_id=None,
            external_trade_id=None,
            created_at=now,
            updated_at=now,
        )
        session.add(order)
        await session.flush()
        decision.buy_order_id = order.id
        decision.status = "bought"
        decision.reason = "自动跟单买入已执行"
        decision.processed_at = now
        decision.updated_at = now
        await session.commit()
        return position.id


async def test_active_position_cache_refreshes_only_changed_candidate_markets(database):
    wallet = "0x7777777777777777777777777777777777777777"
    condition_id = "0x" + "7" * 64
    second_condition_id = "0x" + "8" * 64

    class PositionClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[str, ...]]] = []

        async def fetch_active_positions(
            self,
            user: str,
            *,
            condition_ids: Iterable[str] | None = None,
        ) -> list[PositionSnapshot]:
            self.calls.append((user, tuple(condition_ids or [])))
            return []

    client = PositionClient()
    scanner = WhaleDiscoveryScanner(
        database=database,
        client=client,  # type: ignore[arg-type]
        settings=database.settings,
    )
    active_targets = {wallet: {condition_id}}

    first, failed = await scanner._fetch_current_positions(
        [],
        additional_targets=active_targets,
    )
    second, failed_again = await scanner._fetch_current_positions(
        [],
        additional_targets=active_targets,
    )
    expanded_targets = {wallet: {condition_id, second_condition_id}}
    third, expanded_failed = await scanner._fetch_current_positions(
        [],
        additional_targets=expanded_targets,
    )
    fourth, candidate_failed = await scanner._fetch_current_positions(
        [
            _auto_aggregate(
                wallet=wallet,
                asset_id="asset-yes",
                condition_id=condition_id,
            )
        ],
        additional_targets=expanded_targets,
    )

    assert first == second == third == fourth == {wallet: {}}
    assert failed == failed_again == expanded_failed == candidate_failed == set()
    assert client.calls == [
        (wallet, (condition_id,)),
        (wallet, (second_condition_id,)),
        (wallet, (condition_id,)),
    ]


@pytest.mark.parametrize("dual_amount", [None, Decimal("25")])
async def test_auto_follow_decision_is_one_shot_and_large_rule_has_priority(database, dual_amount):
    condition_id = "0x" + "7" * 64
    wallet = "0x7777777777777777777777777777777777777777"
    await _seed_auto_market(database, condition_id)
    config = await _auto_follow_config(
        database,
        dual_match_auto_follow_amount_usdc=dual_amount,
        new_account_auto_follow_enabled=True,
        large_amount_auto_follow_enabled=True,
        large_amount_auto_follow_low_price_max_price=Decimal("0.65"),
        large_amount_auto_follow_low_price_amount_usdc=Decimal("5"),
    )
    aggregate = _auto_aggregate(
        wallet=wallet,
        asset_id="asset-yes",
        condition_id=condition_id,
    )
    scanner = build_scanner(database)
    positions = {
        wallet: {
            "asset-yes": SimpleNamespace(
                size=Decimal("1000000"),
                condition_id=condition_id,
                asset_id="asset-yes",
                avg_price=Decimal("0.60"),
            )
        }
    }
    kwargs = {
        "rule_matches": {(wallet, "asset-yes"): {"new_account", "large_amount"}},
        "positions_by_wallet": positions,
        "failed_wallets": set(),
        "config": config,
        "now": utcnow(),
        "window_start": utcnow() - timedelta(hours=24),
    }

    pending = await scanner._persist_entries([aggregate], **kwargs)
    await scanner._recover_interrupted_auto_decisions()
    repeated = await scanner._persist_entries([aggregate], **kwargs)

    async with database.sessions() as session:
        decisions = list(await session.scalars(select(WhaleAutoFollowDecision)))
    assert len(pending) == 1
    assert repeated == []
    assert len(decisions) == 1
    assert decisions[0].selected_rule == "large_amount"
    assert decisions[0].configured_amount_usdc == (dual_amount or Decimal("10"))
    if dual_amount is None:
        assert _auto_follow_price(decisions[0].configured_low_price_max_price) == Decimal("0.65")
        assert decisions[0].configured_low_price_amount_usdc == Decimal("5")
    else:
        assert decisions[0].configured_low_price_max_price is None
        assert decisions[0].configured_low_price_amount_usdc is None
    assert _auto_follow_price(decisions[0].configured_min_price) == Decimal("0.60")
    assert json.loads(decisions[0].matched_rules_json) == ["large_amount", "new_account"]
    assert decisions[0].status == "failed"
    assert decisions[0].reason == "服务重启前尚未提交自动买入，不补买"


async def test_auto_follow_allows_different_wallets_on_same_asset(database):
    condition_id = "0x" + "8" * 64
    wallets = [
        "0x8888888888888888888888888888888888888888",
        "0x9999999999999999999999999999999999999999",
    ]
    await _seed_auto_market(database, condition_id)
    config = await _auto_follow_config(database, large_amount_auto_follow_enabled=True)
    aggregates = [
        _auto_aggregate(wallet=wallet, asset_id="asset-yes", condition_id=condition_id)
        for wallet in wallets
    ]
    positions = {
        wallet: {
            "asset-yes": SimpleNamespace(
                size=Decimal("1000000"),
                condition_id=condition_id,
                asset_id="asset-yes",
                avg_price=Decimal("0.60"),
            )
        }
        for wallet in wallets
    }
    scanner = build_scanner(database)

    pending = await scanner._persist_entries(
        aggregates,
        rule_matches={(wallet, "asset-yes"): {"large_amount"} for wallet in wallets},
        positions_by_wallet=positions,
        failed_wallets=set(),
        config=config,
        now=utcnow(),
        window_start=utcnow() - timedelta(hours=24),
    )

    async with database.sessions() as session:
        decisions = list(await session.scalars(select(WhaleAutoFollowDecision)))
    assert len(pending) == 2
    assert len(decisions) == 2
    assert {decision.proxy_wallet for decision in decisions} == set(wallets)


async def test_auto_follow_price_rejection_records_observed_book_once(database):
    condition_id = "0x" + "2" * 64
    wallet = "0x2222222222222222222222222222222222222222"
    await _seed_auto_market(database, condition_id)
    config = await _auto_follow_config(database, large_amount_auto_follow_enabled=True)
    aggregate = _auto_aggregate(
        wallet=wallet,
        asset_id="asset-yes",
        condition_id=condition_id,
    )
    scanner = build_scanner(database)
    pending = await scanner._persist_entries(
        [aggregate],
        rule_matches={(wallet, "asset-yes"): {"large_amount"}},
        positions_by_wallet={
            wallet: {
                "asset-yes": SimpleNamespace(
                    size=Decimal("1000000"),
                    condition_id=condition_id,
                    asset_id="asset-yes",
                    avg_price=Decimal("0.60"),
                )
            }
        },
        failed_wallets=set(),
        config=config,
        now=utcnow(),
        window_start=utcnow() - timedelta(hours=24),
    )

    class PriceRejectedExecutor:
        async def quote_follow(self, **kwargs: Any):
            assert kwargs["minimum_price"] == Decimal("0.60")
            assert kwargs["maximum_price"] == Decimal("0.80")
            raise AutoFollowQuoteRejected(
                "实际买价 0.55 低于策略最低价 0.60",
                observed_best_ask=Decimal("0.55"),
            )

    scanner.executor = PriceRejectedExecutor()  # type: ignore[assignment]
    await scanner._process_auto_decisions(pending)

    async with database.sessions() as session:
        decision = await session.get(WhaleAutoFollowDecision, pending[0])
    assert decision is not None
    assert decision.status == "failed"
    assert decision.observed_best_ask.quantize(Decimal("0.01")) == Decimal("0.55")
    assert "低于策略最低价" in str(decision.reason)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0.75000000000000000000", "0.75"),
        ("0.85000000000000000000", "0.85"),
        ("0.000", "0"),
        ("100", "100"),
    ],
)
def test_decimal_display_removes_only_insignificant_zeroes(value: str, expected: str):
    assert _decimal_display(Decimal(value)) == expected


def test_auto_follow_price_removes_sqlite_float_tail_at_strategy_boundary():
    maximum = _auto_follow_price(Decimal("0.699999999999999956"))

    assert maximum == Decimal("0.70")
    assert not Decimal("0.70") > maximum


@pytest.mark.parametrize(
    ("best_ask", "expected_amount", "expected_low"),
    [
        ("0.20", "10", True),
        ("0.2999", "10", True),
        ("0.30", "15", False),
        ("0.75", "15", False),
    ],
)
def test_auto_follow_selects_low_amount_below_exclusive_boundary(
    best_ask: str,
    expected_amount: str,
    expected_low: bool,
):
    amount, selected_low = _select_auto_follow_amount(
        base_amount=Decimal("15"),
        best_ask=Decimal(best_ask),
        low_price_max_price=Decimal("0.30"),
        low_price_amount=Decimal("10"),
    )

    assert amount == Decimal(expected_amount)
    assert selected_low is expected_low


def test_auto_follow_rejects_execution_after_price_crosses_selected_band():
    assert _auto_follow_price_band_changed(
        best_ask=Decimal("0.30"),
        low_price_max_price=Decimal("0.30"),
        selected_low=True,
    )
    assert _auto_follow_price_band_changed(
        best_ask=Decimal("0.29"),
        low_price_max_price=Decimal("0.30"),
        selected_low=False,
    )
    assert not _auto_follow_price_band_changed(
        best_ask=Decimal("0.30"),
        low_price_max_price=Decimal("0.30"),
        selected_low=False,
    )


def test_auto_follow_reason_display_repairs_legacy_sqlite_float_tails():
    reason = "实际买价 0.998999999999999999 高于策略最高价 0.699999999999999956"

    assert _auto_follow_reason_display(reason) == "实际买价 0.999 高于策略最高价 0.7"


@pytest.mark.parametrize("priority_enabled", [True, False])
async def test_same_scan_cross_rule_signals_respect_priority_toggle(
    database,
    priority_enabled: bool,
):
    condition_id = "0x" + "9" * 64
    new_wallet = "0x9999999999999999999999999999999999999999"
    large_wallet = "0x8888888888888888888888888888888888888888"
    await _seed_auto_market(database, condition_id)
    config = await _auto_follow_config(
        database,
        new_account_auto_follow_enabled=True,
        large_amount_auto_follow_enabled=True,
        large_amount_conflict_priority_enabled=priority_enabled,
    )
    aggregates = [
        _auto_aggregate(
            wallet=new_wallet,
            asset_id="asset-yes",
            condition_id=condition_id,
        ),
        _auto_aggregate(
            wallet=large_wallet,
            asset_id="asset-no",
            condition_id=condition_id,
            outcome="No",
            outcome_index=1,
        ),
    ]
    scanner = build_scanner(database)

    pending = await scanner._persist_entries(
        aggregates,
        rule_matches={
            (new_wallet, "asset-yes"): {"new_account"},
            (large_wallet, "asset-no"): {"large_amount"},
        },
        positions_by_wallet={
            new_wallet: {
                "asset-yes": SimpleNamespace(
                    size=Decimal("1000000"),
                    condition_id=condition_id,
                    asset_id="asset-yes",
                    avg_price=Decimal("0.60"),
                )
            },
            large_wallet: {
                "asset-no": SimpleNamespace(
                    size=Decimal("1000000"),
                    condition_id=condition_id,
                    asset_id="asset-no",
                    avg_price=Decimal("0.60"),
                )
            },
        },
        failed_wallets=set(),
        config=config,
        now=utcnow(),
        window_start=utcnow() - timedelta(hours=24),
    )

    async with database.sessions() as session:
        decisions = {
            decision.asset_id: decision
            for decision in await session.scalars(select(WhaleAutoFollowDecision))
        }
        market_lock = await session.get(WhaleAutoMarketLock, condition_id)
    if priority_enabled:
        assert pending == [decisions["asset-no"].id]
        assert decisions["asset-no"].selected_rule == "large_amount"
        assert decisions["asset-no"].status == "pending"
        assert decisions["asset-yes"].status == "skipped"
        assert "全量超大额信号优先" in str(decisions["asset-yes"].reason)
        assert market_lock is None
    else:
        assert pending == []
        assert {decision.status for decision in decisions.values()} == {"conflict_locked"}
        assert market_lock is not None
        assert market_lock.exit_status == "not_required"


@pytest.mark.parametrize(
    ("priority_enabled", "held_rule", "opposite_rule", "should_exit"),
    [
        (True, "large_amount", "new_account", False),
        (True, "new_account", "large_amount", True),
        (False, "large_amount", "new_account", True),
    ],
)
async def test_existing_position_respects_large_signal_priority(
    database,
    priority_enabled: bool,
    held_rule: str,
    opposite_rule: str,
    should_exit: bool,
):
    condition_id = "0x" + ("a" if should_exit else "b") * 64
    held_wallet = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    opposite_wallet = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    await _seed_auto_market(database, condition_id)
    config = await _auto_follow_config(
        database,
        new_account_auto_follow_enabled=True,
        large_amount_auto_follow_enabled=True,
        large_amount_conflict_priority_enabled=priority_enabled,
    )
    held = _auto_aggregate(
        wallet=held_wallet,
        asset_id="asset-yes",
        condition_id=condition_id,
    )
    scanner = build_scanner(database)
    initial_pending = await scanner._persist_entries(
        [held],
        rule_matches={(held_wallet, "asset-yes"): {held_rule}},
        positions_by_wallet={
            held_wallet: {
                "asset-yes": SimpleNamespace(
                    size=Decimal("1000000"),
                    condition_id=condition_id,
                    asset_id="asset-yes",
                    avg_price=Decimal("0.60"),
                )
            }
        },
        failed_wallets=set(),
        config=config,
        now=utcnow(),
        window_start=utcnow() - timedelta(hours=24),
    )
    assert len(initial_pending) == 1
    await _fill_auto_decision(database, initial_pending[0])

    opposite = _auto_aggregate(
        wallet=opposite_wallet,
        asset_id="asset-no",
        condition_id=condition_id,
        outcome="No",
        outcome_index=1,
    )
    pending = await scanner._persist_entries(
        [opposite],
        rule_matches={(opposite_wallet, "asset-no"): {opposite_rule}},
        positions_by_wallet={
            # 优先级模式下，原触发钱包退出也不能改变已成交仓位的策略等级。
            held_wallet: (
                {}
                if priority_enabled
                else {
                    "asset-yes": SimpleNamespace(
                        size=Decimal("1000000"),
                        condition_id=condition_id,
                        asset_id="asset-yes",
                        avg_price=Decimal("0.60"),
                    )
                }
            ),
            opposite_wallet: {
                "asset-no": SimpleNamespace(
                    size=Decimal("1000000"),
                    condition_id=condition_id,
                    asset_id="asset-no",
                    avg_price=Decimal("0.60"),
                )
            },
        },
        failed_wallets=set(),
        config=config,
        now=utcnow(),
        window_start=utcnow() - timedelta(hours=24),
    )

    async with database.sessions() as session:
        decisions = {
            decision.asset_id: decision
            for decision in await session.scalars(select(WhaleAutoFollowDecision))
        }
        market_lock = await session.get(WhaleAutoMarketLock, condition_id)
    assert pending == []
    assert decisions["asset-yes"].status == ("exit_pending" if should_exit else "bought")
    assert decisions["asset-no"].status == ("conflict_locked" if should_exit else "skipped")
    assert (market_lock is not None) is should_exit
    if market_lock is not None:
        assert market_lock.exit_status == "pending"
        assert market_lock.trigger_asset_id == "asset-no"


async def test_mixed_position_uses_large_priority_and_rechecks_rule_upgrade(database):
    condition_id = "0x" + "c" * 64
    new_wallet = "0xcccccccccccccccccccccccccccccccccccccccc"
    large_wallet = "0xdddddddddddddddddddddddddddddddddddddddd"
    opposite_wallet = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    await _seed_auto_market(database, condition_id)
    config = await _auto_follow_config(
        database,
        new_account_auto_follow_enabled=True,
        large_amount_auto_follow_enabled=True,
    )
    scanner = build_scanner(database)
    same_side = [
        _auto_aggregate(
            wallet=new_wallet,
            asset_id="asset-yes",
            condition_id=condition_id,
        ),
        _auto_aggregate(
            wallet=large_wallet,
            asset_id="asset-yes",
            condition_id=condition_id,
        ),
    ]
    initial_pending = await scanner._persist_entries(
        same_side,
        rule_matches={
            (new_wallet, "asset-yes"): {"new_account"},
            (large_wallet, "asset-yes"): {"large_amount"},
        },
        positions_by_wallet={
            new_wallet: {
                "asset-yes": SimpleNamespace(
                    size=Decimal("1000000"),
                    condition_id=condition_id,
                    asset_id="asset-yes",
                    avg_price=Decimal("0.60"),
                )
            },
            large_wallet: {
                "asset-yes": SimpleNamespace(
                    size=Decimal("1000000"),
                    condition_id=condition_id,
                    asset_id="asset-yes",
                    avg_price=Decimal("0.60"),
                )
            },
        },
        failed_wallets=set(),
        config=config,
        now=utcnow(),
        window_start=utcnow() - timedelta(hours=24),
    )
    assert len(initial_pending) == 2
    position_id = await _fill_auto_decision(database, initial_pending[0])
    await _fill_auto_decision(database, initial_pending[1], position_id=position_id)

    opposite = _auto_aggregate(
        wallet=opposite_wallet,
        asset_id="asset-no",
        condition_id=condition_id,
        outcome="No",
        outcome_index=1,
    )
    positions = {
        new_wallet: {
            "asset-yes": SimpleNamespace(
                size=Decimal("1000000"),
                condition_id=condition_id,
                asset_id="asset-yes",
                avg_price=Decimal("0.60"),
            )
        },
        large_wallet: {
            "asset-yes": SimpleNamespace(
                size=Decimal("1000000"),
                condition_id=condition_id,
                asset_id="asset-yes",
                avg_price=Decimal("0.60"),
            )
        },
        opposite_wallet: {
            "asset-no": SimpleNamespace(
                size=Decimal("1000000"),
                condition_id=condition_id,
                asset_id="asset-no",
                avg_price=Decimal("0.60"),
            )
        },
    }
    await scanner._persist_entries(
        [opposite],
        rule_matches={(opposite_wallet, "asset-no"): {"new_account"}},
        positions_by_wallet=positions,
        failed_wallets=set(),
        config=config,
        now=utcnow(),
        window_start=utcnow() - timedelta(hours=24),
    )
    async with database.sessions() as session:
        first_pass = list(await session.scalars(select(WhaleAutoFollowDecision)))
        assert await session.get(WhaleAutoMarketLock, condition_id) is None
    assert {decision.status for decision in first_pass if decision.asset_id == "asset-yes"} == {
        "bought"
    }
    assert (
        next(decision for decision in first_pass if decision.asset_id == "asset-no").status
        == "skipped"
    )

    await scanner._persist_entries(
        [opposite],
        rule_matches={(opposite_wallet, "asset-no"): {"new_account", "large_amount"}},
        positions_by_wallet=positions,
        failed_wallets=set(),
        config=config,
        now=utcnow(),
        window_start=utcnow() - timedelta(hours=24),
    )
    async with database.sessions() as session:
        upgraded = list(await session.scalars(select(WhaleAutoFollowDecision)))
        market_lock = await session.get(WhaleAutoMarketLock, condition_id)
    assert {decision.status for decision in upgraded if decision.asset_id == "asset-yes"} == {
        "exit_pending"
    }
    opposite_decision = next(decision for decision in upgraded if decision.asset_id == "asset-no")
    assert opposite_decision.status == "conflict_locked"
    assert json.loads(opposite_decision.matched_rules_json) == ["large_amount", "new_account"]
    assert market_lock is not None
    assert market_lock.trigger_asset_id == "asset-no"
    assert market_lock.exit_status == "pending"


async def test_same_scan_opposite_auto_signals_lock_both_sides_before_buy(database):
    condition_id = "0x" + "6" * 64
    yes_wallet = "0x6666666666666666666666666666666666666666"
    no_wallet = "0x5555555555555555555555555555555555555555"
    await _seed_auto_market(database, condition_id)
    config = await _auto_follow_config(database, large_amount_auto_follow_enabled=True)
    aggregates = [
        _auto_aggregate(
            wallet=yes_wallet,
            asset_id="asset-yes",
            condition_id=condition_id,
        ),
        _auto_aggregate(
            wallet=no_wallet,
            asset_id="asset-no",
            condition_id=condition_id,
            outcome="No",
            outcome_index=1,
        ),
    ]
    scanner = build_scanner(database)

    pending = await scanner._persist_entries(
        aggregates,
        rule_matches={
            (yes_wallet, "asset-yes"): {"large_amount"},
            (no_wallet, "asset-no"): {"large_amount"},
        },
        positions_by_wallet={
            yes_wallet: {
                "asset-yes": SimpleNamespace(
                    size=Decimal("1000000"),
                    condition_id=condition_id,
                    asset_id="asset-yes",
                    avg_price=Decimal("0.60"),
                )
            },
            no_wallet: {
                "asset-no": SimpleNamespace(
                    size=Decimal("1000000"),
                    condition_id=condition_id,
                    asset_id="asset-no",
                    avg_price=Decimal("0.60"),
                )
            },
        },
        failed_wallets=set(),
        config=config,
        now=utcnow(),
        window_start=utcnow() - timedelta(hours=24),
    )

    async with database.sessions() as session:
        decisions = list(await session.scalars(select(WhaleAutoFollowDecision)))
        market_lock = await session.get(WhaleAutoMarketLock, condition_id)
    assert pending == []
    assert {decision.status for decision in decisions} == {"conflict_locked"}
    assert market_lock is not None
    assert market_lock.exit_status == "not_required"
    assert market_lock.trigger_amount_usdc == Decimal("600000")


async def test_conflict_exit_sells_entire_mixed_position(database):
    condition_id = "0x" + "4" * 64
    wallet = "0x4444444444444444444444444444444444444444"
    await _seed_auto_market(database, condition_id)
    config = await _auto_follow_config(database, large_amount_auto_follow_enabled=True)
    aggregate = _auto_aggregate(
        wallet=wallet,
        asset_id="asset-yes",
        condition_id=condition_id,
    )
    scanner = build_scanner(database)
    pending = await scanner._persist_entries(
        [aggregate],
        rule_matches={(wallet, "asset-yes"): {"large_amount"}},
        positions_by_wallet={
            wallet: {
                "asset-yes": SimpleNamespace(
                    size=Decimal("1000000"),
                    condition_id=condition_id,
                    asset_id="asset-yes",
                    avg_price=Decimal("0.60"),
                )
            }
        },
        failed_wallets=set(),
        config=config,
        now=utcnow(),
        window_start=utcnow() - timedelta(hours=24),
    )
    assert len(pending) == 1

    now = utcnow()
    async with database.sessions() as session:
        decision = await session.get(WhaleAutoFollowDecision, pending[0])
        assert decision is not None
        position = WhaleFollowPosition(
            asset_id="asset-yes",
            condition_id=condition_id,
            title="自动跟单测试市场",
            outcome="Yes",
            outcome_index=0,
            neg_risk=False,
            market_slug="auto-follow-test",
            event_slug="auto-follow-test",
            icon_url=None,
            source_wallet=wallet,
            source_whale_avg_price=Decimal("0.60"),
            cycle_no=1,
            # 5 份来自自动跟单、10 份来自后来合并的人工买入。
            size=Decimal("15"),
            cost_usdc=Decimal("10.5"),
            lifetime_bought_size=Decimal("15"),
            lifetime_bought_usdc=Decimal("10.5"),
            lifetime_sold_size=Decimal("0"),
            lifetime_sold_usdc=Decimal("0"),
            lifetime_fee_usdc=Decimal("0"),
            realized_pnl=Decimal("0"),
            status="open",
            opened_at=now,
            closed_at=None,
            created_at=now,
            updated_at=now,
        )
        session.add(position)
        await session.flush()
        buy_order = WhaleOrder(
            position_id=position.id,
            entry_id=decision.entry_id,
            idempotency_key="auto:test-mixed-position",
            source="auto_follow",
            source_wallet=wallet,
            asset_id="asset-yes",
            condition_id=condition_id,
            title="自动跟单测试市场",
            outcome="Yes",
            outcome_index=0,
            neg_risk=False,
            side="BUY",
            requested_size=Decimal("5"),
            requested_usdc=Decimal("3.5"),
            limit_price=Decimal("0.70"),
            reference_price=Decimal("0.70"),
            whale_avg_price=Decimal("0.60"),
            filled_size=Decimal("5"),
            filled_usdc=Decimal("3.5"),
            fee_usdc=Decimal("0"),
            status="filled",
            reason=None,
            signed_order_hash=None,
            execution_provider="test",
            external_order_id=None,
            external_trade_id=None,
            created_at=now,
            updated_at=now,
        )
        session.add(buy_order)
        await session.flush()
        decision.buy_order_id = buy_order.id
        decision.status = "exit_pending"
        session.add(
            WhaleAutoMarketLock(
                condition_id=condition_id,
                trigger_entry_id=decision.entry_id,
                trigger_wallet="0x3333333333333333333333333333333333333333",
                trigger_asset_id="asset-no",
                trigger_outcome="No",
                trigger_amount_usdc=Decimal("500000"),
                trigger_rules_json='["large_amount"]',
                reason="反向大额信号",
                exit_status="pending",
                last_error=None,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
        position_id = position.id
        buy_order_id = buy_order.id

    calls: list[tuple[str, Any]] = []

    class ConflictExecutor:
        async def write_off_terminal_sell_remainder(self, position_id: int) -> bool:
            calls.append(("dust_check", position_id))
            return False

        async def quote_sell(self, *, position_id: int, size: Any, sell_all: bool):
            calls.append(("quote", (position_id, size, sell_all)))
            return SimpleNamespace(position_id=position_id, asset_id="asset-yes")

        async def execute_sell(self, quote: Any, confirmation_id: str, *, order_source: str):
            calls.append(("execute", (confirmation_id, order_source)))
            async with database.sessions() as session:
                current = await session.get(WhaleFollowPosition, quote.position_id)
                assert current is not None
                assert current.size == Decimal("15")
                current.size = Decimal("0")
                current.cost_usdc = Decimal("0")
                current.status = "closed"
                current.closed_at = utcnow()
                await session.commit()
            return buy_order_id

    scanner.executor = ConflictExecutor()  # type: ignore[assignment]
    await scanner._process_conflict_exits()

    assert calls[0] == ("dust_check", position_id)
    assert calls[1] == ("quote", (position_id, None, True))
    assert calls[2][0] == "execute"
    assert calls[2][1][1] == "conflict_exit"
    async with database.sessions() as session:
        decision = await session.get(WhaleAutoFollowDecision, pending[0])
        market_lock = await session.get(WhaleAutoMarketLock, condition_id)
    assert decision is not None and decision.status == "exit_completed"
    assert market_lock is not None and market_lock.exit_status == "completed"


@pytest.mark.parametrize(
    ("slugs", "categories", "allowed"),
    [
        (["sports"], ["sports", "science_tech"], True),
        (["sports", "esports"], ["sports"], False),
        (["sports", "esports"], ["esports"], True),
        (["science"], ["science_tech"], True),
        (["politics"], ["sports", "science_tech"], False),
        ([], ["other"], True),
        ([], ["sports"], False),
    ],
)
async def test_monitor_categories_gate_both_rules_and_downstream_candidates(
    database, slugs, categories, allowed
):
    class CategorizedClient(PositionDiscoveryClient):
        async def fetch_markets_with_tags(self, condition_ids):
            markets = await super().fetch_markets_with_tags(condition_ids)
            return [replace(item, tags=tuple({"slug": slug} for slug in slugs)) for item in markets]

    client = CategorizedClient()
    await enable_email_notifications(database)
    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        settings.monitor_categories_json = json.dumps(categories)
        settings.large_amount_threshold_usdc = Decimal("100000")
        await session.commit()
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)

    assert await scanner.tick() is True
    assert await scanner.tick() is True

    async with database.sessions() as session:
        assert len(list(await session.scalars(select(WhaleEntry)))) == int(allowed)
        states = list(await session.scalars(select(WhaleEntryRuleState)))
        assert {state.rule_type for state in states} == (
            {"new_account", "large_amount"} if allowed else set()
        )
        assert len(list(await session.scalars(select(WhaleEmailDelivery)))) == int(allowed)
        assert len(list(await session.scalars(select(WhaleAutoFollowDecision)))) == int(allowed)


async def test_narrowing_monitor_categories_preserves_history_and_refreshes_positions(database):
    client = PositionDiscoveryClient()  # No tags: classified as other.
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    assert await scanner.tick() is True
    async with database.sessions() as session:
        original = await session.scalar(select(WhaleEntryRuleState))
        original_id, original_trigger = original.id, original.first_triggered_at
        settings = await session.get(WhaleSettings, 1)
        settings.monitor_categories_json = '["sports"]'
        # The next scan would also hit large_amount if the new filter were ignored.
        settings.large_amount_threshold_usdc = Decimal("100000")
        await session.commit()
    client.current_position_size = Decimal("120000")
    scanner._position_cache.clear()  # Simulate expiry between scheduled scans.
    assert await scanner.tick() is True
    async with database.sessions() as session:
        states = list(await session.scalars(select(WhaleEntryRuleState)))
        assert len(states) == 1
        assert (states[0].id, states[0].first_triggered_at) == (original_id, original_trigger)
        assert states[0].active is True
        entry = await session.scalar(select(WhaleEntry))
        assert entry.net_size == Decimal("120000")
    client.position_available = False
    scanner._position_cache.clear()
    assert await scanner.tick() is True
    async with database.sessions() as session:
        entry = await session.scalar(select(WhaleEntry))
        state = await session.scalar(select(WhaleEntryRuleState))
        assert entry.status == "exited"
        assert state.active is False
        assert state.first_triggered_at == original_trigger


@pytest.mark.parametrize("remaining", ["3000", "150000"])
async def test_reduced_whale_is_visible_but_cannot_be_followed(database, remaining):
    client = PositionDiscoveryClient()
    client.current_position_size = Decimal(remaining)
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    assert await scanner.tick()
    async with database.sessions() as session:
        entry = await session.scalar(select(WhaleEntry))
        assert entry.status == "reduced"
        assert entry.follow_eligible is False
        assert entry.follow_ineligible_reason == "position_reduced"
        assert entry.position_cost_usdc == Decimal(remaining) * Decimal("0.5")


async def test_opposite_position_outside_trade_window_is_detected(database):
    class Client(PositionDiscoveryClient):
        async def fetch_active_positions(self, user, *, condition_ids=None):
            positions = await super().fetch_active_positions(user, condition_ids=condition_ids)
            return positions + [
                replace(
                    positions[0],
                    asset_id="asset-no",
                    outcome="No",
                    outcome_index=1,
                    size=Decimal("200000"),
                )
            ]

    client = Client()
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    assert await scanner.tick()
    async with database.sessions() as session:
        entry = await session.scalar(select(WhaleEntry))
        assert entry.hedged is True
        assert entry.opposite_size == Decimal("200000")
        assert entry.follow_eligible is False
        assert entry.follow_ineligible_reason == "position_hedged"


async def test_unchanged_candidate_uses_cache_but_new_buy_refreshes(database):
    class Client(PositionDiscoveryClient):
        def __init__(self):
            super().__init__()
            self.checks = 0

        async def fetch_active_positions(self, user, *, condition_ids=None):
            self.checks += 1
            return await super().fetch_active_positions(user, condition_ids=condition_ids)

    client = Client()
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    assert await scanner.tick()
    async with database.sessions() as session:
        checked_at = (await session.scalar(select(WhaleEntry))).position_checked_at
    assert await scanner.tick()
    assert client.checks == 1
    async with database.sessions() as session:
        assert (await session.scalar(select(WhaleEntry))).position_checked_at == checked_at
    client.transaction_hash = "0xnew-buy"
    client.trade_timestamp = utcnow()
    assert await scanner.tick()
    assert client.checks == 2


async def test_excluded_category_never_fetches_wallet_profile(database):
    calls = []

    class Client(PositionDiscoveryClient):
        async def fetch_public_profile(self, address):
            calls.append(address)
            return await super().fetch_public_profile(address)

    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        settings.monitor_categories_json = '["sports"]'
        await session.commit()
    scanner = WhaleDiscoveryScanner(database=database, client=Client(), settings=database.settings)
    assert await scanner.tick()
    assert calls == []


class SupplementalDiscoveryClient(PositionDiscoveryClient):
    def __init__(self):
        super().__init__()
        self.catalog_calls = 0
        self.small_trade_calls = 0
        self.small_trades = []

    async def fetch_discovery_markets(self, **kwargs):
        self.catalog_calls += 1
        return await self.fetch_active_whale_markets(
            min_liquidity_usdc=Decimal("5000"),
            min_volume_usdc=Decimal("10000"),
        )

    async def fetch_large_trades(self, **kwargs):
        if kwargs.get("condition_ids"):
            assert kwargs["condition_ids"] == [self.condition_id]
            assert kwargs["filter_amount_usdc"] == 0
            self.small_trade_calls += 1
            trades = [
                item
                for item in self.small_trades
                if kwargs["start"] <= item.timestamp.replace(microsecond=0) <= kwargs["end"]
            ]
            return trades[kwargs["offset"] : kwargs["offset"] + kwargs["limit"]]
        return []


async def test_split_buys_are_discovered_and_deduplicated(database):
    client = SupplementalDiscoveryClient()
    original = (await PositionDiscoveryClient.fetch_large_trades(client))[0]
    client.small_trades = [
        replace(
            original, transaction_hash=f"0xsmall{i}", size=Decimal("1500"), amount=Decimal("750")
        )
        for i in range(200)
    ]
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    assert await scanner.tick()
    assert await scanner.tick()
    async with database.sessions() as session:
        entry = await session.scalar(select(WhaleEntry))
        assert entry.gross_buy_usdc == Decimal("150000")
        assert entry.trade_count == 200
        assert entry.discovery_source == "trades"
        assert len(list(await session.scalars(select(WhaleTrade)))) == 200
    assert client.catalog_calls == client.small_trade_calls == 1
    assert client.position_calls == [client.condition_id]


async def test_old_position_is_discovered_without_automatic_buy(database):
    client = SupplementalDiscoveryClient()
    await enable_email_notifications(database)
    await _auto_follow_config(database, new_account_auto_follow_enabled=True)
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    assert await scanner.tick()
    async with database.sessions() as session:
        entries = list(await session.scalars(select(WhaleEntry)))
        assert len(entries) == 1  # totalBought=0 ghost wallet is excluded.
        assert entries[0].discovery_source == "positions"
        assert entries[0].trade_count == 0
        assert entries[0].position_cost_usdc == Decimal("120000")
        assert not list(await session.scalars(select(WhaleAutoFollowDecision)))
        assert not list(await session.scalars(select(WhaleAutoMarketLock)))
        delivery = await session.scalar(select(WhaleEmailDelivery))
        assert "持仓补充发现成本（买入时间未知）" in delivery.body_text
        assert "窗口累计买入" not in delivery.body_text


async def test_incomplete_split_window_does_not_create_trade_signals(database):
    client = SupplementalDiscoveryClient()
    original = (await PositionDiscoveryClient.fetch_large_trades(client))[0]
    client.small_trades = [replace(original, transaction_hash=f"0xsmall{i}") for i in range(2000)]
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    assert await scanner.tick()
    async with database.sessions() as session:
        assert not list(await session.scalars(select(WhaleTrade)))
        assert not list(await session.scalars(select(WhaleAutoFollowDecision)))
        assert (await session.scalar(select(WhaleEntry))).discovery_source == "positions"
        settings = await session.get(WhaleSettings, 1)
        assert settings.last_scan_error == (
            "重点市场 Official position market 历史补齐中，拆单回溯不完整，未用于新增信号"
        )
    assert client.small_trade_calls == 4


async def test_supplemental_failure_does_not_interrupt_main_discovery(database, monkeypatch):
    client = PositionDiscoveryClient()

    async def unavailable(**kwargs):
        raise PolymarketAPIError("unavailable")

    monkeypatch.setattr(client, "fetch_discovery_markets", unavailable)
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)

    async def retry_once(operation, **kwargs):
        return await operation(**kwargs)

    monkeypatch.setattr(scanner, "_retry", retry_once)
    assert await scanner.tick()
    async with database.sessions() as session:
        assert (await session.scalar(select(WhaleEntry))).discovery_source == "trades"
        assert "补充发现不可用" in (await session.get(WhaleSettings, 1)).last_scan_error


async def test_supplement_skips_excluded_category_before_trade_or_position_queries(database):
    client = SupplementalDiscoveryClient()
    await _auto_follow_config(database, monitor_categories_json='["sports"]')
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    assert await scanner.tick()
    assert client.small_trade_calls == 0
    assert client.position_calls == []


@pytest.mark.parametrize("kind", ["reduced", "hedged", "exited", "failed"])
async def test_source_position_is_rechecked_before_quote_and_execution(database, kind, monkeypatch):
    from backend.tests.test_whale_execution import follow_quote
    from backend.whale import WhaleFollowExecutor

    client = PositionDiscoveryClient()
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    assert await scanner.tick()
    async with database.sessions() as session:
        entry = await session.scalar(select(WhaleEntry))
        entry_id = entry.id
    original = client.fetch_active_positions

    async def changed_positions(user, *, condition_ids=None):
        if kind == "failed":
            raise PolymarketAPIError("持仓查询失败")
        if kind == "exited":
            return []
        positions = await original(user, condition_ids=condition_ids)
        if kind == "reduced":
            return [replace(positions[0], size=Decimal("1"))]
        return positions + [replace(positions[0], asset_id="asset-no", outcome_index=1)]

    monkeypatch.setattr(client, "fetch_active_positions", changed_positions)
    executor = WhaleFollowExecutor(
        database=database, client=client, settings=database.settings, keychain=SimpleNamespace()
    )
    error = PolymarketAPIError if kind == "failed" else ValueError
    match = {"failed": "持仓查询失败", "reduced": "减仓", "hedged": "双向", "exited": "退出"}[kind]
    with pytest.raises(error, match=match):
        await executor.quote_follow(
            asset_id=client.asset_id, amount_usdc=Decimal("15"), entry_id=entry_id
        )
    quote = replace(
        follow_quote(),
        entry_id=entry_id,
        asset_id=client.asset_id,
        condition_id=client.condition_id,
        source_wallet=client.wallet,
    )
    with pytest.raises(error, match=match):
        await executor.execute_follow(quote, "test-confirmation")
    async with database.sessions() as session:
        assert not list(await session.scalars(select(WhaleOrder)))


async def test_repeated_position_discovery_preserves_reduction_baseline(database, monkeypatch):
    client = SupplementalDiscoveryClient()
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    assert await scanner.tick()
    original = client.fetch_market_positions

    async def reduced_snapshot(condition_id, *, limit):
        return [
            replace(item, size=Decimal("210000"))
            for item in await original(condition_id, limit=limit)
        ]

    monkeypatch.setattr(client, "fetch_market_positions", reduced_snapshot)
    client.current_position_size = Decimal("210000")
    async with database.sessions() as session:
        state = await session.get(WhaleMarketScanState, client.condition_id)
        state.last_checked_at = None
        await session.commit()
    scanner._position_cache.clear()
    assert await scanner.tick()
    async with database.sessions() as session:
        entry = await session.scalar(select(WhaleEntry))
        assert entry.gross_buy_size == Decimal("300000")
        assert entry.net_ratio == Decimal("70")
        assert entry.status == "reduced"
        assert not entry.follow_eligible


async def test_supplemental_discovery_respects_wallet_exclusions(database):
    client = SupplementalDiscoveryClient()
    async with database.sessions() as session:
        session.add(
            WhaleExclusion(proxy_wallet=client.wallet, label="excluded", created_at=utcnow())
        )
        await session.commit()
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    assert await scanner.tick()
    async with database.sessions() as session:
        assert not list(await session.scalars(select(WhaleEntry)))


async def test_position_discovery_can_transition_to_a_new_real_buy(database):
    client = SupplementalDiscoveryClient()
    await _auto_follow_config(database, new_account_auto_follow_enabled=True)
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    assert await scanner.tick()
    client.trade_timestamp = utcnow()
    client.small_trades = await PositionDiscoveryClient.fetch_large_trades(client)
    async with database.sessions() as session:
        state = await session.get(WhaleMarketScanState, client.condition_id)
        state.last_checked_at = None
        await session.commit()
    assert await scanner.tick()
    async with database.sessions() as session:
        entry = await session.scalar(select(WhaleEntry))
        assert entry.discovery_source == "trades"
        assert entry.first_buy_at == client.trade_timestamp
        assert len(list(await session.scalars(select(WhaleAutoFollowDecision)))) == 1


async def test_candidate_cache_expiry_refreshes_and_preserves_failed_hedge_state(
    database, monkeypatch
):
    class Client(PositionDiscoveryClient):
        async def fetch_active_positions(self, user, *, condition_ids=None):
            positions = await super().fetch_active_positions(user, condition_ids=condition_ids)
            return positions + [replace(positions[0], asset_id="asset-no")]

    client = Client()
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    assert await scanner.tick()
    for key, (_, positions) in scanner._position_cache.items():
        scanner._position_cache[key] = (0, positions)
    client.position_error = True

    async def retry_once(operation, **kwargs):
        return await operation(**kwargs)

    monkeypatch.setattr(scanner, "_retry", retry_once)
    assert await scanner.tick()
    async with database.sessions() as session:
        entry = await session.scalar(select(WhaleEntry))
        assert entry.hedged
        assert not entry.follow_eligible
        assert entry.follow_ineligible_reason == "position_check_failed"


@pytest.mark.parametrize(
    ("slugs", "categories", "allowed"),
    [
        (["sports"], ["sports"], True),
        (["sports", "esports"], ["sports"], False),
        (["sports", "esports"], ["esports"], True),
        (["politics"], ["sports"], False),
    ],
)
async def test_supplemental_discovery_serializes_official_tags(
    database, slugs, categories, allowed
):
    class TaggedClient(SupplementalDiscoveryClient):
        async def fetch_discovery_markets(self, **kwargs):
            markets = await super().fetch_discovery_markets(**kwargs)
            return [
                replace(
                    market,
                    tags=tuple(
                        OfficialTag(id=str(index), slug=slug, label=slug.title())
                        for index, slug in enumerate(slugs)
                    ),
                )
                for market in markets
            ]

    client = TaggedClient()
    await _auto_follow_config(database, monitor_categories_json=json.dumps(categories))
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    completed = await scanner.tick()
    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        assert completed, settings.last_scan_error
        assert settings.last_scan_error is None
        assert len(list(await session.scalars(select(WhaleEntry)))) == int(allowed)
        if allowed:
            market = await session.get(WhaleMarket, client.condition_id)
            assert json.loads(market.tags_json) == [
                {"id": str(index), "slug": slug, "label": slug.title()}
                for index, slug in enumerate(slugs)
            ]
    assert client.small_trade_calls == int(allowed)
    assert len(client.position_calls) == int(allowed)


async def test_market_backfill_splits_checkpoints_and_then_queries_only_increment(database):
    client = SupplementalDiscoveryClient()
    original = (await PositionDiscoveryClient.fetch_large_trades(client))[0]
    now = utcnow().replace(microsecond=0)
    start = now - timedelta(hours=24)
    client.small_trades = [
        replace(
            original, timestamp=start + timedelta(seconds=30 * i), transaction_hash=f"0xbackfill{i}"
        )
        for i in range(2100)
    ]
    complete = False
    for attempt in range(40):
        scanner = WhaleDiscoveryScanner(
            database=database, client=client, settings=database.settings
        )
        before = client.small_trade_calls
        await scanner._backfill_market_trades(
            client.condition_id, now=now + timedelta(minutes=attempt), window_start=start
        )
        assert client.small_trade_calls - before <= 4
        async with database.sessions() as session:
            state = await session.get(WhaleMarketScanState, client.condition_id)
            trades = list(await session.scalars(select(WhaleTrade)))
            if state.batch_end is None:
                assert state.coverage_end == now
                assert len(trades) == 2100
                assert not list(await session.scalars(select(WhaleMarketScanPage)))
                complete = True
                break
            assert trades == []
            assert state.coverage_end is None
    assert complete
    next_now = now + timedelta(minutes=attempt + 15)
    client.small_trades.append(
        replace(
            original, timestamp=next_now - timedelta(seconds=30), transaction_hash="0xincrement"
        )
    )
    before = client.small_trade_calls
    await scanner._backfill_market_trades(client.condition_id, now=next_now, window_start=start)
    assert client.small_trade_calls - before == 1
    async with database.sessions() as session:
        assert len(list(await session.scalars(select(WhaleTrade)))) == 2101


async def test_market_backfill_same_second_pages_resume_and_overlap_deduplicates(
    database, monkeypatch
):
    monkeypatch.setattr("backend.whale.FOCUS_TRADE_PAGES", 1)
    client = SupplementalDiscoveryClient()
    original = (await PositionDiscoveryClient.fetch_large_trades(client))[0]
    now = utcnow().replace(microsecond=0)
    client.small_trades = [
        replace(original, timestamp=now, transaction_hash=f"0xsame{i}") for i in range(1100)
    ]
    for attempt in range(3):
        scanner = WhaleDiscoveryScanner(
            database=database, client=client, settings=database.settings
        )
        await scanner._backfill_market_trades(client.condition_id, now=now, window_start=now)
        async with database.sessions() as session:
            state = await session.get(WhaleMarketScanState, client.condition_id)
            trades = list(await session.scalars(select(WhaleTrade)))
            if attempt < 2:
                assert json.loads(state.pending_ranges_json)[0][2] == (attempt + 1) * 500
                assert not trades
            else:
                assert state.batch_end is None
                assert len(trades) == 1100
    # Re-reading the overlap must preserve identical fills within one transaction.
    for _ in range(3):
        await scanner._backfill_market_trades(client.condition_id, now=now, window_start=now)
    async with database.sessions() as session:
        assert len(list(await session.scalars(select(WhaleTrade)))) == 1100


async def test_market_backfill_rejects_ignored_time_bounds(database):
    client = SupplementalDiscoveryClient()
    original = (await PositionDiscoveryClient.fetch_large_trades(client))[0]
    now = utcnow().replace(microsecond=0)

    async def ignores_bounds(**kwargs):
        return [replace(original, timestamp=now - timedelta(days=2))]

    client.fetch_large_trades = ignores_bounds
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    await scanner._backfill_market_trades(
        client.condition_id, now=now, window_start=now - timedelta(days=1)
    )
    async with database.sessions() as session:
        state = await session.get(WhaleMarketScanState, client.condition_id)
        assert "start/end" in state.last_error
        assert state.coverage_end is None
        assert not list(await session.scalars(select(WhaleTrade)))


async def test_market_backfill_publication_is_atomic_and_restartable(database, monkeypatch):
    client = SupplementalDiscoveryClient()
    client.small_trades = await PositionDiscoveryClient.fetch_large_trades(client)
    now = utcnow().replace(microsecond=0)
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    persist = scanner._persist_trades

    async def interrupted(trades, *, session=None, backfill_until=None):
        await persist(trades, session=session, backfill_until=backfill_until)
        raise RuntimeError("interrupted before coverage commit")

    monkeypatch.setattr(scanner, "_persist_trades", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        await scanner._backfill_market_trades(
            client.condition_id, now=now, window_start=now - timedelta(days=1)
        )
    async with database.sessions() as session:
        assert not list(await session.scalars(select(WhaleTrade)))
        assert list(await session.scalars(select(WhaleMarketScanPage)))
        assert (await session.get(WhaleMarketScanState, client.condition_id)).coverage_end is None
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    before = client.small_trade_calls
    await scanner._backfill_market_trades(
        client.condition_id, now=now, window_start=now - timedelta(days=1)
    )
    assert client.small_trade_calls == before
    async with database.sessions() as session:
        assert len(list(await session.scalars(select(WhaleTrade)))) == len(client.small_trades)
        assert (await session.get(WhaleMarketScanState, client.condition_id)).coverage_end == now


async def test_backfill_warning_survives_unselected_round_and_initial_history_cannot_buy(database):
    client = SupplementalDiscoveryClient()
    original = (await PositionDiscoveryClient.fetch_large_trades(client))[0]
    client.small_trades = [replace(original, transaction_hash=f"0xhistory{i}") for i in range(2000)]
    await _auto_follow_config(database, new_account_auto_follow_enabled=True)
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    assert await scanner.tick()
    before = client.small_trade_calls
    assert await scanner.tick()
    assert client.small_trade_calls == before
    async with database.sessions() as session:
        assert (await session.get(WhaleSettings, 1)).last_scan_error == (
            "重点市场 Official position market 历史补齐中，拆单回溯不完整，未用于新增信号"
        )
    # Resume with a new scanner instance each round, preserving the original time window.
    for _ in range(40):
        async with database.sessions() as session:
            state = await session.get(WhaleMarketScanState, client.condition_id)
            state.last_checked_at = None
            await session.commit()
        scanner = WhaleDiscoveryScanner(
            database=database, client=client, settings=database.settings
        )
        assert await scanner.tick()
        async with database.sessions() as session:
            state = await session.get(WhaleMarketScanState, client.condition_id)
            if state.batch_end is None:
                assert len(list(await session.scalars(select(WhaleTrade)))) == 2000
                assert not list(await session.scalars(select(WhaleAutoFollowDecision)))
                assert (await session.get(WhaleSettings, 1)).last_scan_error is None
                break
    else:
        pytest.fail("backfill did not complete")


async def test_backfill_identical_fills_and_window_extension(database):
    client = SupplementalDiscoveryClient()
    original = (await PositionDiscoveryClient.fetch_large_trades(client))[0]
    now = utcnow().replace(microsecond=0)
    repeated = replace(
        original, timestamp=now - timedelta(minutes=30), transaction_hash="0xidentical"
    )
    client.small_trades = [repeated, repeated]
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    await scanner._backfill_market_trades(
        client.condition_id, now=now, window_start=now - timedelta(hours=1)
    )
    older = replace(original, timestamp=now - timedelta(hours=2), transaction_hash="0xolder")
    client.small_trades.append(older)
    await scanner._backfill_market_trades(
        client.condition_id, now=now, window_start=now - timedelta(hours=24)
    )
    async with database.sessions() as session:
        trades = list(await session.scalars(select(WhaleTrade)))
        assert len(trades) == 3
        assert sum(trade.transaction_hash == "0xidentical" for trade in trades) == 2
        assert (await session.get(WhaleMarketScanState, client.condition_id)).coverage_start == (
            now - timedelta(hours=24)
        )


@pytest.mark.parametrize("failure", ["repeated_page", "offset_cap"])
async def test_backfill_same_second_failures_never_publish_partial_data(database, failure):
    client = SupplementalDiscoveryClient()
    original = (await PositionDiscoveryClient.fetch_large_trades(client))[0]
    now = utcnow().replace(microsecond=0)
    if failure == "repeated_page":

        async def repeated_page(**kwargs):
            return [
                replace(original, timestamp=now, transaction_hash=f"0xdup{i}") for i in range(500)
            ]

        client.fetch_large_trades = repeated_page
    else:
        from datetime import UTC

        stamp = int(now.replace(tzinfo=UTC).timestamp())
        async with database.sessions() as session:
            session.add(
                WhaleMarketScanState(
                    condition_id=client.condition_id,
                    batch_start=now,
                    batch_end=now,
                    pending_ranges_json=json.dumps([[stamp, stamp, 10500]]),
                )
            )
            await session.commit()
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    await scanner._backfill_market_trades(client.condition_id, now=now, window_start=now)
    async with database.sessions() as session:
        state = await session.get(WhaleMarketScanState, client.condition_id)
        assert (
            "重复" in state.last_error if failure == "repeated_page" else "容量" in state.last_error
        )
        assert state.coverage_end is None
        assert state.batch_end is not None
        assert not list(await session.scalars(select(WhaleTrade)))


async def test_empty_supplement_does_not_block_core_signal(database):
    class CoreClient(SupplementalDiscoveryClient):
        async def fetch_large_trades(self, **kwargs):
            if kwargs.get("condition_ids"):
                return []
            return await PositionDiscoveryClient.fetch_large_trades(self)

    client = CoreClient()
    await _auto_follow_config(database, new_account_auto_follow_enabled=True)
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    assert await scanner.tick()
    async with database.sessions() as session:
        assert (
            await session.get(WhaleMarketScanState, client.condition_id)
        ).auto_follow_after is None
        assert len(list(await session.scalars(select(WhaleAutoFollowDecision)))) == 1


async def test_old_snapshot_completion_keeps_catching_up(database):
    from datetime import UTC

    client = SupplementalDiscoveryClient()
    old_end = utcnow().replace(microsecond=0) - timedelta(minutes=31)
    start = old_end - timedelta(hours=24)
    async with database.sessions() as session:
        session.add(
            WhaleMarketScanState(
                condition_id=client.condition_id,
                batch_start=start,
                batch_end=old_end,
                pending_ranges_json=json.dumps(
                    [
                        [
                            int(start.replace(tzinfo=UTC).timestamp()),
                            int(old_end.replace(tzinfo=UTC).timestamp()),
                            0,
                        ]
                    ]
                ),
            )
        )
        await session.commit()
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    now = old_end + timedelta(minutes=31)
    await scanner._backfill_market_trades(client.condition_id, now=now, window_start=start)
    async with database.sessions() as session:
        state = await session.get(WhaleMarketScanState, client.condition_id)
        assert state.coverage_end == old_end
        assert state.batch_end == now
        assert json.loads(state.pending_ranges_json)
    await scanner._backfill_market_trades(
        client.condition_id, now=now + timedelta(minutes=1), window_start=start
    )
    async with database.sessions() as session:
        state = await session.get(WhaleMarketScanState, client.condition_id)
        assert state.coverage_end == now
        assert state.batch_end is None


@pytest.mark.parametrize("same_wallet", [False, True])
async def test_core_signal_survives_unrelated_wallet_backfill(database, same_wallet):
    class Client(SupplementalDiscoveryClient):
        async def fetch_large_trades(self, **kwargs):
            if kwargs.get("condition_ids"):
                original = (await PositionDiscoveryClient.fetch_large_trades(self))[0]
                return [
                    replace(
                        original,
                        proxy_wallet=original.proxy_wallet if same_wallet else "0x" + "c" * 40,
                        asset_id="other-direction" if same_wallet else original.asset_id,
                        timestamp=self.trade_timestamp - timedelta(hours=2),
                        transaction_hash="0xunrelatedhistory",
                    )
                ]
            return await PositionDiscoveryClient.fetch_large_trades(self)

    client = Client()
    await _auto_follow_config(database, new_account_auto_follow_enabled=True)
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    assert await scanner.tick()
    async with database.sessions() as session:
        decisions = list(await session.scalars(select(WhaleAutoFollowDecision)))
        assert len(decisions) == 1, (
            "A separate wallet historical fill suppressed the valid core signal"
        )


async def test_new_buy_after_history_is_eligible(database, monkeypatch):
    now = utcnow().replace(microsecond=0)
    monkeypatch.setattr("backend.whale.utcnow", lambda: now)
    client = SupplementalDiscoveryClient()
    await _auto_follow_config(database, new_account_auto_follow_enabled=True)
    client.small_trades = await PositionDiscoveryClient.fetch_large_trades(client)
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    assert await scanner.tick()
    async with database.sessions() as session:
        assert not list(await session.scalars(select(WhaleAutoFollowDecision)))
        assert (await session.scalar(select(WhaleEntry))).discovery_source == "trades"
        state = await session.get(WhaleMarketScanState, client.condition_id)
        state.last_checked_at = None
        await session.commit()
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    assert await scanner.tick()  # Restart without a new buy must not replay history.
    async with database.sessions() as session:
        assert not list(await session.scalars(select(WhaleAutoFollowDecision)))
        state = await session.get(WhaleMarketScanState, client.condition_id)
        state.last_checked_at = None
        await session.commit()
    now += timedelta(minutes=1)
    client.trade_timestamp = now
    fresh = (await PositionDiscoveryClient.fetch_large_trades(client))[0]
    client.small_trades.append(replace(fresh, transaction_hash="0xnewbuy"))
    assert await scanner.tick()
    async with database.sessions() as session:
        entry = await session.scalar(select(WhaleEntry))
        assert entry.last_buy_at == client.trade_timestamp
        assert len(list(await session.scalars(select(WhaleAutoFollowDecision)))) == 1, (
            "History consumed the first-trigger state permanently"
        )
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    assert await scanner.tick()
    async with database.sessions() as session:
        assert len(list(await session.scalars(select(WhaleAutoFollowDecision)))) == 1
        gate = await session.scalar(select(WhaleBackfillSignalState))
        assert gate.awaiting_new_buy is False


@pytest.mark.parametrize(
    ("rules", "enabled", "categories", "expected_amount"),
    [
        (["new_account"], True, ["sports"], Decimal("5")),
        (["large_amount"], True, ["sports"], Decimal("10")),
        (["new_account", "large_amount"], False, ["sports"], None),
        (["new_account", "large_amount"], True, ["politics"], None),
    ],
)
async def test_dual_amount_preserves_eligibility_and_does_not_top_up(
    database, rules, enabled, categories, expected_amount
):
    condition_id = "0x" + "7" * 64
    wallet = "0x7777777777777777777777777777777777777777"
    await _seed_auto_market(database, condition_id)
    config = await _auto_follow_config(
        database,
        dual_match_auto_follow_amount_usdc=Decimal("25"),
        new_account_auto_follow_enabled=enabled,
        large_amount_auto_follow_enabled=enabled,
        new_account_auto_follow_categories_json=json.dumps(categories),
        large_amount_auto_follow_categories_json=json.dumps(categories),
        large_amount_auto_follow_low_price_max_price=Decimal("0.65"),
        large_amount_auto_follow_low_price_amount_usdc=Decimal("5"),
    )
    aggregate = _auto_aggregate(
        wallet=wallet,
        asset_id="asset-yes",
        condition_id=condition_id,
    )
    scanner = build_scanner(database)
    positions = {
        wallet: {
            "asset-yes": SimpleNamespace(
                size=Decimal("1000000"),
                condition_id=condition_id,
                asset_id="asset-yes",
                avg_price=Decimal("0.60"),
            )
        }
    }
    kwargs = {
        "rule_matches": {(wallet, "asset-yes"): set(rules)},
        "positions_by_wallet": positions,
        "failed_wallets": set(),
        "config": config,
        "now": utcnow(),
        "window_start": utcnow() - timedelta(hours=24),
    }

    pending = await scanner._persist_entries([aggregate], **kwargs)
    async with database.sessions() as session:
        decision = await session.scalar(select(WhaleAutoFollowDecision))
        assert decision.configured_amount_usdc == expected_amount
        assert decision.status == ("pending" if expected_amount is not None else "skipped")
        original_amount = decision.configured_amount_usdc
    assert len(pending) == (1 if expected_amount is not None else 0)
    kwargs["rule_matches"] = {(wallet, "asset-yes"): {"new_account", "large_amount"}}
    assert await scanner._persist_entries([aggregate], **kwargs) == []
    async with database.sessions() as session:
        decisions = list(await session.scalars(select(WhaleAutoFollowDecision)))
        assert len(decisions) == 1
        assert decisions[0].configured_amount_usdc == original_amount


@pytest.mark.parametrize("phase", ["quote", "execute"])
@pytest.mark.parametrize("result", ["recovered", "exited", "exhausted", "cancelled"])
async def test_buy_position_rate_limit_retries_before_proceeding(
    database, monkeypatch, phase, result
):
    from backend.tests.test_whale_execution import follow_quote
    from backend.whale import WhaleFollowExecutor

    client = PositionDiscoveryClient()
    scanner = WhaleDiscoveryScanner(database=database, client=client, settings=database.settings)
    assert await scanner.tick()
    async with database.sessions() as session:
        entry_id = (await session.scalar(select(WhaleEntry))).id
    original = client.fetch_active_positions
    calls = 0
    delays = []
    continued = []

    async def limited_positions(user, *, condition_ids=None):
        nonlocal calls
        calls += 1
        assert user == client.wallet
        assert condition_ids == [client.condition_id]
        if calls <= 2 or result == "exhausted":
            raise PolymarketAPIError(
                "Polymarket 接口请求过于频繁",
                rate_limited=True,
                retry_after=15 if calls == 1 else None,
            )
        if result == "exited":
            return []
        return await original(user, condition_ids=condition_ids)

    async def record_sleep(delay):
        delays.append(delay)
        if result == "cancelled":
            raise asyncio.CancelledError

    class ReachedNextBuyCheck(Exception):
        pass

    async def next_buy_check(*args):
        continued.append(phase)
        raise ReachedNextBuyCheck

    monkeypatch.setattr(client, "fetch_active_positions", limited_positions)
    monkeypatch.setattr("backend.whale.asyncio.sleep", record_sleep)
    executor = WhaleFollowExecutor(
        database=database, client=client, settings=database.settings, keychain=SimpleNamespace()
    )
    # Stop at the next check: no credentials or real order submission are needed.
    monkeypatch.setattr(executor, "_account", next_buy_check)
    monkeypatch.setattr(client, "fetch_order_book", next_buy_check, raising=False)
    expected = {
        "recovered": ReachedNextBuyCheck,
        "exited": ValueError,
        "exhausted": PolymarketAPIError,
        "cancelled": asyncio.CancelledError,
    }[result]
    with pytest.raises(expected) as raised:
        if phase == "quote":
            await executor.quote_follow(
                asset_id=client.asset_id, amount_usdc=Decimal("15"), entry_id=entry_id
            )
        else:
            quote = replace(
                follow_quote(),
                entry_id=entry_id,
                asset_id=client.asset_id,
                condition_id=client.condition_id,
                source_wallet=client.wallet,
            )
            await executor.execute_follow(quote, "rate-limit-test", order_source="auto_follow")
    if result == "exited":
        assert "退出" in str(raised.value)
    assert continued == ([phase] if result == "recovered" else [])
    assert calls == {"recovered": 3, "exited": 3, "exhausted": 4, "cancelled": 1}[result]
    assert delays == (
        [15] if result == "cancelled" else [15, 20, 40] if result == "exhausted" else [15, 20]
    )
    async with database.sessions() as session:
        assert not list(await session.scalars(select(WhaleOrder)))
