"""Read-only Linux collection service. Run only through the explicit server entry point."""

from __future__ import annotations

import asyncio
import json
import logging
import tomllib
from collections import defaultdict
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import monotonic

from fastapi import FastAPI, HTTPException, Query
from sqlalchemy import delete, func, select

from backend.collection_store import PublicDatabase, PublicStore, dumps, parse_time, row_dict
from backend.config import Settings
from backend.models import (
    CollectionEvent,
    CollectionMeta,
    WhaleBackfillSignalState,
    WhaleMarket,
    WhaleMarketScanState,
    WhaleTrade,
    WhaleWallet,
)
from backend.polymarket import PolymarketClient, WhaleMarketPositionSnapshot
from backend.time_utils import utcnow
from backend.whale import (
    WHALE_STATISTICS_CATEGORY_LABELS,
    PublicDataCollector,
    _whale_statistics_classification,
)

LOGGER = logging.getLogger(__name__)
ENRICHMENT_WALLETS_PER_ROUND = 12
ENRICHMENT_BUDGET_SECONDS = 25
HISTORY_BUDGET_SECONDS = 8
POSITION_REFRESH_SECONDS = 120


@dataclass(frozen=True)
class CollectionSettings:
    database_url: str = "sqlite+aiosqlite:///./data/collection.db"
    categories: tuple[str, ...] = ("sports", "esports")
    interval_seconds: int = 60
    filter_amount_usdc: str = "1000"
    window_hours: int = 24
    retention_hours: int = 72
    max_scan_pages: int = 20

    def __post_init__(self):
        if not self.categories or not set(self.categories) <= set(WHALE_STATISTICS_CATEGORY_LABELS):
            raise ValueError("采集分类无效")
        if (
            self.interval_seconds < 1
            or self.window_hours < 1
            or self.retention_hours < self.window_hours
        ):
            raise ValueError("采集周期或保留窗口无效")
        if not Decimal(self.filter_amount_usdc).is_finite() or Decimal(self.filter_amount_usdc) < 0:
            raise ValueError("成交下限无效")
        if not 1 <= self.max_scan_pages <= 100:
            raise ValueError("采集分页上限无效")

    @classmethod
    def from_file(cls, path: str):
        with Path(path).open("rb") as source:
            values = tomllib.load(source)
        if "categories" in values:
            values["categories"] = tuple(values["categories"])
        return cls(**values)


class CollectionWorker:
    def __init__(self, store: PublicStore, client, config: CollectionSettings):
        self.store = store
        self.config = config
        settings = replace(
            store.database.settings,
            whale_max_scan_pages=config.max_scan_pages,
            whale_profile_batch_limit=ENRICHMENT_WALLETS_PER_ROUND,
        )
        self.collector = PublicDataCollector(
            database=store.database, client=client, settings=settings
        )
        self.lock = asyncio.Lock()
        self.runtime = {"phase": "starting", "last_error": None}
        self._sources = {}

    @staticmethod
    def _checked_at(result):
        checked = result.get("checked_at")
        return parse_time(checked) if isinstance(checked, str) else checked or datetime.min

    async def _enrich(self, targets, versions, last_buys, previous, now):
        """Bound requests by wallet, prioritize live buys, and rotate historical work."""
        for event in previous:
            for wallet, result in json.loads(event.payload_json).get("positions", {}).items():
                self._sources.setdefault((wallet, event.condition_id), result)
        wanted = defaultdict(set)
        for condition, wallets in targets.items():
            for wallet in wallets:
                key = (wallet, condition)
                source = self._sources.get(key, {})
                if (
                    not source.get("ok")
                    or source.get("trade_version") != versions.get(key, "0")
                    or now - self._checked_at(source) >= timedelta(seconds=POSITION_REFRESH_SECONDS)
                ):
                    wanted[wallet].add(condition)

        # Keep two historical slots so a busy live feed cannot starve the baseline.
        def checked(wallet):
            return min(
                self._checked_at(self._sources.get((wallet, key), {})) for key in wanted[wallet]
            )

        def bought(wallet):
            return max(last_buys.get((wallet, key), datetime.min) for key in wanted[wallet])

        live = sorted(
            (wallet for wallet in wanted if now - bought(wallet) <= timedelta(seconds=180)),
            key=lambda wallet: (checked(wallet), -bought(wallet).timestamp(), wallet),
        )
        live_set = set(live)
        history = sorted(
            (wallet for wallet in wanted if wallet not in live_set),
            key=lambda wallet: (checked(wallet), wallet),
        )
        selected = live[: ENRICHMENT_WALLETS_PER_ROUND - 2] + history[:2]
        selected += [wallet for wallet in live + history if wallet not in selected][
            : ENRICHMENT_WALLETS_PER_ROUND - len(selected)
        ]
        semaphore = asyncio.Semaphore(3)

        async def fetch(wallet):
            conditions = sorted(wanted[wallet])
            # A wallet can hold thousands of markets; bound each request URL as well.
            conditions = sorted(
                conditions,
                key=lambda key: (self._checked_at(self._sources.get((wallet, key), {})), key),
            )[:100]
            async with semaphore:
                try:
                    current = await self.collector.client.fetch_active_positions(
                        user=wallet, condition_ids=conditions
                    )
                    if any(item.condition_id not in conditions for item in current):
                        raise ValueError("来源持仓市场不匹配")
                    checked_at = utcnow()
                    for condition in conditions:
                        self._sources[(wallet, condition)] = dict(
                            ok=True,
                            status="ready",
                            checked_at=checked_at,
                            trade_version=versions.get((wallet, condition), "0"),
                            positions=[
                                asdict(item) for item in current if item.condition_id == condition
                            ],
                        )
                except Exception:
                    for condition in conditions:
                        self._sources[(wallet, condition)] = dict(
                            ok=False, status="failed", checked_at=utcnow(), positions=[]
                        )

        self.runtime.update(
            phase="enrichment", selected_wallets=len(selected), pending_wallets=len(wanted)
        )
        try:
            async with asyncio.timeout(ENRICHMENT_BUDGET_SECONDS):
                await asyncio.gather(
                    self.collector._refresh_wallets(selected, now=now, cache_hours=24),
                    *(fetch(wallet) for wallet in selected),
                )
        except TimeoutError:
            # Completed results survive; cancelled/incomplete queries remain unknown.
            pass
        active = {
            (wallet, condition) for condition, wallets in targets.items() for wallet in wallets
        }
        self._sources = {key: value for key, value in self._sources.items() if key in active}

    def _source(self, wallet, condition, versions, profiles):
        key = (wallet, condition)
        result = self._sources.get(key, {})
        pending = (
            not result.get("ok")
            or result.get("trade_version") != versions.get(key, "0")
            or utcnow() - self._checked_at(result) > timedelta(seconds=180)
            or wallet not in profiles
        )
        if pending:
            return dict(
                result,
                ok=False,
                status=result.get("status") if result.get("status") == "failed" else "pending",
                checked_at=result.get("checked_at"),
                positions=result.get("positions", []),
            )
        return result

    async def tick(self):
        async with self.lock:
            started = monotonic()
            self.runtime.update(
                phase="trades", started_at=utcnow().isoformat() + "Z", last_error=None
            )
            now = utcnow()
            start = now - timedelta(hours=self.config.window_hours)
            database = self.store.database
            async with database.sessions() as session:
                meta = await session.get(CollectionMeta, 1)
                cursor = meta.cursor_at
                incomplete_until = meta.incomplete_until
            trades, truncated = await self.collector._collect_trades(
                start=max(start, cursor - timedelta(seconds=120)) if cursor else start,
                end=now,
                amount=Decimal(self.config.filter_amount_usdc),
            )
            # Resolve categories before persisting any feed detail.
            async with database.sessions() as session:
                before_categories = {
                    row.condition_id: _whale_statistics_classification(row.tags_json)["category"]
                    for row in (await session.scalars(select(WhaleMarket))).all()
                }
                known_conditions = list(before_categories)
            await self.collector._refresh_markets(
                list(set(known_conditions) | {item.condition_id for item in trades}), now=now
            )
            async with database.sessions() as session:
                markets = list((await session.scalars(select(WhaleMarket))).all())
                allowed = {
                    row.condition_id
                    for row in markets
                    if _whale_statistics_classification(row.tags_json)["category"]
                    in self.config.categories
                }
            moved = {
                row.condition_id
                for row in markets
                if row.condition_id in allowed
                and row.condition_id in before_categories
                and before_categories[row.condition_id]
                != _whale_statistics_classification(row.tags_json)["category"]
            }
            await self.collector._persist_trades(
                [item for item in trades if item.condition_id in allowed - moved]
            )
            await self.collector._persist_trades(
                [item for item in trades if item.condition_id in moved], backfill_until=now
            )
            self.runtime["phase"] = "history"
            positions = []
            _warnings = []
            self.collector._discovery_position_results = {}
            try:
                async with asyncio.timeout(HISTORY_BUDGET_SECONDS):
                    async with database.sessions() as session:
                        pending = list(
                            await session.scalars(
                                select(WhaleMarketScanState.condition_id)
                                .where(WhaleMarketScanState.batch_end.is_not(None))
                                .order_by(WhaleMarketScanState.last_checked_at)
                            )
                        )
                    for condition_id in dict.fromkeys(list(moved) + pending):
                        if condition_id in allowed:
                            await self.collector._backfill_market_trades(
                                condition_id, now=now, window_start=start
                            )
                    positions, _warnings = await self.collector._supplement_discovery(
                        dict(
                            monitor_categories_json=dumps(self.config.categories),
                            min_liquidity_usdc=0,
                        ),
                        now=now,
                        window_start=start,
                    )
            except TimeoutError:
                _warnings.append("历史补充采集未在本轮完成")
                # Only committed backfill pages/progress survive the deadline.
                positions = [
                    item
                    for values in self.collector._discovery_position_results.values()
                    for item in values
                ]
            # Focus markets are visited in rotation. Carry previous discoveries and
            # continue checking their source positions between those visits.
            async with database.sessions() as session:
                latest = (
                    select(func.max(CollectionEvent.id))
                    .where(CollectionEvent.deleted.is_(False))
                    .group_by(CollectionEvent.condition_id)
                )
                previous = list(
                    (
                        await session.scalars(
                            select(CollectionEvent).where(CollectionEvent.id.in_(latest))
                        )
                    ).all()
                )
            observed = self.collector._discovery_position_results
            for event in previous:
                if event.condition_id not in allowed or event.condition_id in observed:
                    continue
                for item in json.loads(event.payload_json).get("discoveries", []):
                    numeric = {
                        "size",
                        "total_bought",
                        "avg_price",
                        "current_price",
                        "current_value",
                    }
                    positions.append(
                        WhaleMarketPositionSnapshot(
                            **{
                                key: Decimal(value) if key in numeric else value
                                for key, value in item.items()
                            }
                        )
                    )
            await self.collector._persist_position_wallet_hints(positions, now=now)
            async with database.sessions() as session:
                markets = list((await session.scalars(select(WhaleMarket))).all())
                categories = {
                    row.condition_id: _whale_statistics_classification(row.tags_json)["category"]
                    for row in markets
                }
                allowed = {
                    key
                    for key, category in categories.items()
                    if category in self.config.categories
                }
                await session.execute(
                    delete(WhaleTrade).where(
                        (WhaleTrade.timestamp < now - timedelta(hours=self.config.retention_hours))
                        | WhaleTrade.condition_id.not_in(allowed)
                    )
                )
                rows = list(
                    (
                        await session.scalars(
                            select(WhaleTrade).where(WhaleTrade.timestamp >= start)
                        )
                    ).all()
                )
                gates = list((await session.scalars(select(WhaleBackfillSignalState))).all())
                progress = {
                    row.condition_id: row
                    for row in (await session.scalars(select(WhaleMarketScanState))).all()
                }
                await session.commit()
            targets = defaultdict(set)
            for row in rows + positions:
                if row.condition_id in allowed:
                    targets[row.condition_id].add(row.proxy_wallet)
            versions = {}
            last_buys = {}
            for row in rows:
                key = (row.proxy_wallet, row.condition_id)
                versions[key] = max(versions.get(key, 0), row.id)
                if row.side == "BUY":
                    last_buys[key] = max(last_buys.get(key, datetime.min), row.timestamp)
            versions = {key: str(value) for key, value in versions.items()}
            await self._enrich(targets, versions, last_buys, previous, now)
            async with database.sessions() as session:
                profiles = {
                    row.proxy_wallet: row_dict(row)
                    for row in (await session.scalars(select(WhaleWallet))).all()
                }
            bundles = {}
            for market in markets:
                key = market.condition_id
                if key not in allowed:
                    continue
                source_positions = {
                    wallet: self._source(wallet, key, versions, profiles)
                    for wallet in sorted(targets[key])
                }
                state = progress.get(key)
                bundles[key] = (
                    categories[key],
                    dict(
                        market=row_dict(market),
                        historical_until=now if key in moved else None,
                        trades=[row_dict(row) for row in rows if row.condition_id == key],
                        wallets=[
                            profiles[wallet]
                            for wallet in sorted(targets[key])
                            if wallet in profiles
                        ],
                        positions=source_positions,
                        discoveries=[
                            asdict(item) for item in positions if item.condition_id == key
                        ],
                        gates=[row_dict(row) for row in gates if row.condition_id == key],
                        coverage=row_dict(state) if state else None,
                        complete=state is None
                        or (state.batch_end is None and state.last_error is None),
                    ),
                )
            if truncated:
                incomplete_until = now + timedelta(hours=self.config.window_hours)
            complete = not truncated and (incomplete_until is None or incomplete_until <= now)
            coverage = {
                category: complete
                and not _warnings
                and all(
                    payload["complete"]
                    for selected, payload in bundles.values()
                    if selected == category
                )
                for category in self.config.categories
            }
            next_cursor = (
                now if truncated else max([cursor or start] + [item.timestamp for item in trades])
            )
            self.runtime["phase"] = "publishing"
            batch = await self.store.publish(
                bundles,
                coverage,
                completed_at=utcnow(),
                cursor_at=next_cursor,
                incomplete_until=incomplete_until,
            )
            self.runtime.update(
                phase="idle",
                last_duration_seconds=round(monotonic() - started, 2),
                ready_sources=sum(
                    value["ok"]
                    for _, payload in bundles.values()
                    for value in payload["positions"].values()
                ),
                pending_sources=sum(
                    not value["ok"]
                    for _, payload in bundles.values()
                    for value in payload["positions"].values()
                ),
            )
            LOGGER.info(
                "Collection batch=%s duration=%.2fs pending_sources=%s",
                batch,
                self.runtime["last_duration_seconds"],
                self.runtime["pending_sources"],
            )
            return batch


def create_collection_app(
    config: CollectionSettings | None = None, *, client=None, start_worker: bool = True
):
    config = config or CollectionSettings()

    @asynccontextmanager
    async def lifespan(app):
        database = PublicDatabase(
            Settings(database_url=config.database_url, trading_enabled=False, start_monitor=False)
        )
        store = PublicStore(database, config.categories, config.retention_hours)
        await store.initialize()
        upstream = client or PolymarketClient(
            data_api_url="https://data-api.polymarket.com",
            gamma_api_url="https://gamma-api.polymarket.com",
            timeout=12,
            direct=True,
        )
        worker = CollectionWorker(store, upstream, config)
        app.state.store = store
        app.state.worker = worker

        async def run():
            while True:
                started = monotonic()
                try:
                    await worker.tick()
                except Exception as error:
                    worker.runtime.update(phase="failed", last_error=type(error).__name__)
                    LOGGER.exception("Public collection round failed")
                # The configured cadence includes processing time; never overlap rounds.
                await asyncio.sleep(max(1, config.interval_seconds - (monotonic() - started)))

        task = asyncio.create_task(run()) if start_worker else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            if client is None:
                await upstream.close()
            await database.close()

    app = FastAPI(
        title="PolyCopy 公共采集服务",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/api/collection/v1/status")
    async def status():
        return {
            **await app.state.store.status(),
            "runtime": dict(app.state.worker.runtime),
            "parameters": {
                key: value for key, value in asdict(config).items() if key != "database_url"
            },
        }

    async def page(snapshot, categories, source_id, watermark, cursor, after, limit):
        try:
            return await app.state.store.page(
                snapshot=snapshot,
                categories=categories.split(","),
                source_id=source_id,
                watermark=watermark,
                cursor=cursor,
                after=after,
                limit=limit,
            )
        except LookupError as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.get("/api/collection/v1/snapshot")
    async def snapshot(
        categories: str = Query(max_length=200),
        source_id: str = Query(max_length=64),
        watermark: int = Query(ge=1),
        after: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=200),
    ):
        return await page(True, categories, source_id, watermark, 0, after, limit)

    @app.get("/api/collection/v1/changes")
    async def changes(
        categories: str = Query(max_length=200),
        source_id: str = Query(max_length=64),
        watermark: int = Query(ge=1),
        cursor: int = Query(0, ge=0),
        after: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=200),
    ):
        return await page(False, categories, source_id, watermark, cursor, after, limit)

    return app


def configured_app():
    import os

    return create_collection_app(
        CollectionSettings.from_file(
            os.environ.get("POLYCOPY_COLLECTION_CONFIG", "collection.toml")
        )
    )
