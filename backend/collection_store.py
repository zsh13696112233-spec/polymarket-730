"""Independent public database and fixed-watermark, category-filtered publication."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, delete, func, select

from backend.db import Database
from backend.models import (
    CollectionBatch,
    CollectionEvent,
    CollectionMeta,
    WhaleBackfillSignalState,
    WhaleMarket,
    WhaleMarketScanPage,
    WhaleMarketScanState,
    WhaleTrade,
    WhaleWallet,
)
from backend.time_utils import utcnow

PUBLIC_MODELS = (
    CollectionBatch,
    CollectionEvent,
    CollectionMeta,
    WhaleBackfillSignalState,
    WhaleMarket,
    WhaleMarketScanPage,
    WhaleMarketScanState,
    WhaleTrade,
    WhaleWallet,
)


def public_metadata() -> MetaData:
    metadata = MetaData()
    for model in PUBLIC_MODELS:
        model.__table__.to_metadata(metadata)
    return metadata


def wire(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def dumps(value) -> str:
    return json.dumps(value, default=wire, sort_keys=True, separators=(",", ":"))


def row_dict(row) -> dict:
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name != "id"
    }


def parse_time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("同步时间缺少 UTC 时区")
    return result.astimezone(UTC).replace(tzinfo=None)


class PublicDatabase(Database):
    async def initialize(self) -> None:
        if self.settings.database_url.endswith(":memory:"):
            async with self.engine.begin() as connection:
                await connection.run_sync(public_metadata().create_all)
            return
        config = Config()
        config.set_main_option(
            "script_location", str(Path(__file__).parent / "collection_migrations")
        )
        config.attributes["database_url"] = self.settings.database_url
        await asyncio.to_thread(command.upgrade, config, "head")


class PublicStore:
    def __init__(
        self, database: PublicDatabase, categories: tuple[str, ...], retention_hours: int = 72
    ):
        self.database = database
        self.categories = categories
        self.retention_hours = retention_hours

    async def initialize(self):
        await self.database.initialize()
        async with self.database.sessions() as session:
            meta = await session.get(CollectionMeta, 1)
            encoded = dumps(sorted(self.categories))
            if meta is None:
                session.add(CollectionMeta(id=1, source_id=uuid4().hex, categories_json=encoded))
            elif meta.categories_json != encoded:
                meta.scope_version += 1
                meta.categories_json = encoded
                # A newly enabled range needs a fresh full-window baseline.
                meta.cursor_at = None
            await session.commit()

    async def publish(
        self,
        bundles: dict[str, tuple[str, dict]],
        coverage: dict,
        *,
        completed_at=None,
        cursor_at=None,
        incomplete_until=None,
    ):
        now = completed_at or utcnow()
        async with self.database.sessions() as session:
            meta = await session.get(CollectionMeta, 1)
            batch = CollectionBatch(
                completed_at=now, scope_version=meta.scope_version, coverage_json=dumps(coverage)
            )
            session.add(batch)
            await session.flush()
            latest = select(func.max(CollectionEvent.id)).group_by(
                CollectionEvent.condition_id, CollectionEvent.category
            )
            old = {
                (row.condition_id, row.category): row
                for row in (
                    await session.scalars(
                        select(CollectionEvent).where(CollectionEvent.id.in_(latest))
                    )
                ).all()
            }
            current = {
                (key, category): dumps(payload)
                for key, (category, payload) in bundles.items()
                if category in self.categories
            }
            for key in old.keys() | current.keys():
                previous = old.get(key)
                payload = current.get(key)
                if payload is None and (previous is None or previous.deleted):
                    continue
                if (
                    previous is not None
                    and not previous.deleted
                    and previous.payload_json == payload
                ):
                    continue
                delta = json.loads(payload) if payload is not None else {}
                if payload is not None and previous is not None and not previous.deleted:
                    before = json.loads(previous.payload_json)
                    full = delta
                    delta = {key: value for key, value in full.items() if before.get(key) != value}
                    known = {item["fingerprint"] for item in before.get("trades", [])}
                    delta["trades"] = [
                        item for item in full.get("trades", []) if item["fingerprint"] not in known
                    ]
                    delta["trade_keys"] = [item["fingerprint"] for item in full.get("trades", [])]
                    delta["market"] = full["market"]
                    delta["partial"] = True
                session.add(
                    CollectionEvent(
                        batch_id=batch.id,
                        condition_id=key[0],
                        category=key[1],
                        deleted=payload is None,
                        payload_json=payload or "{}",
                        delta_json=dumps(delta),
                    )
                )
            await session.flush()
            cutoff = now - timedelta(hours=self.retention_hours)
            # Keep the predecessor for every key so any retained watermark has a snapshot.
            retained_floor = await session.scalar(
                select(func.min(CollectionBatch.id)).where(CollectionBatch.completed_at >= cutoff)
            )
            old_events = CollectionEvent.batch_id < retained_floor
            anchors = (
                select(func.max(CollectionEvent.id))
                .where(old_events)
                .group_by(CollectionEvent.condition_id, CollectionEvent.category)
            )
            await session.execute(
                delete(CollectionEvent).where(old_events, CollectionEvent.id.not_in(anchors))
            )
            await session.execute(
                delete(CollectionBatch).where(CollectionBatch.completed_at < cutoff)
            )
            if cursor_at is not None:
                meta.cursor_at = cursor_at
                meta.incomplete_until = incomplete_until
            await session.commit()
            return batch.id

    async def status(self):
        async with self.database.sessions() as session:
            meta = await session.get(CollectionMeta, 1)
            batch = await session.scalar(
                select(CollectionBatch).order_by(CollectionBatch.id.desc()).limit(1)
            )
            same_scope = batch is not None and batch.scope_version == meta.scope_version
            return dict(
                protocol_version=1,
                source_id=meta.source_id,
                scope_version=meta.scope_version,
                supported_categories=list(self.categories),
                latest_batch=batch.id if batch else 0,
                completed_at=wire(batch.completed_at) if batch else None,
                fresh=batch is not None and utcnow() - batch.completed_at <= timedelta(seconds=180),
                coverage=json.loads(batch.coverage_json)
                if same_scope
                else {key: False for key in self.categories},
            )

    async def page(
        self,
        *,
        categories: list[str],
        source_id: str,
        watermark: int,
        cursor: int = 0,
        after: int = 0,
        limit: int = 100,
        snapshot: bool = False,
    ):
        if not categories or not set(categories) <= set(self.categories):
            raise ValueError("分类不受服务器支持，请刷新服务器状态")
        async with self.database.sessions() as session:
            meta = await session.get(CollectionMeta, 1)
            batch = await session.get(CollectionBatch, watermark)
            if (
                source_id != meta.source_id
                or batch is None
                or batch.scope_version != meta.scope_version
            ):
                raise LookupError("数据源或快照已变化，请重新获取快照")
            if not snapshot and cursor:
                prior = await session.get(CollectionBatch, cursor)
                if prior is None or prior.scope_version != meta.scope_version:
                    raise LookupError("游标已过期，请重新获取快照")
            if cursor > watermark:
                raise ValueError("游标超过固定水位")
            target = watermark
            if not snapshot:
                next_batch = await session.scalar(
                    select(CollectionBatch.id)
                    .where(CollectionBatch.id > cursor, CollectionBatch.id <= watermark)
                    .order_by(CollectionBatch.id)
                    .limit(1)
                )
                target = next_batch or watermark
                batch = await session.get(CollectionBatch, target)
            query = select(CollectionEvent).where(
                CollectionEvent.category.in_(categories), CollectionEvent.id > after
            )
            if snapshot:
                latest = (
                    select(func.max(CollectionEvent.id))
                    .where(CollectionEvent.batch_id <= watermark)
                    .group_by(CollectionEvent.condition_id, CollectionEvent.category)
                )
                query = query.where(
                    CollectionEvent.id.in_(latest), CollectionEvent.deleted.is_(False)
                )
            else:
                query = query.where(
                    CollectionEvent.batch_id == target, CollectionEvent.batch_id > cursor
                )
            rows = list(
                (await session.scalars(query.order_by(CollectionEvent.id).limit(limit + 1))).all()
            )
            more = len(rows) > limit
            rows = rows[:limit]
            return dict(
                protocol_version=1,
                source_id=meta.source_id,
                scope_version=meta.scope_version,
                watermark=watermark,
                batch=target,
                complete=not more,
                next_after=rows[-1].id if rows else after,
                scanned_through=cursor if more else target,
                completed_at=wire(batch.completed_at),
                coverage=json.loads(batch.coverage_json),
                events=[
                    dict(
                        sequence=row.id,
                        condition_id=row.condition_id,
                        category=row.category,
                        deleted=row.deleted,
                        payload=json.loads(row.payload_json if snapshot else row.delta_json),
                    )
                    for row in rows
                ],
            )
