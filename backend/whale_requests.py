from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from time import monotonic
from typing import Literal

WhaleRequestStatus = Literal["pending", "success", "failed"]
WhaleRequestSource = Literal["http", "sdk"]


@dataclass(slots=True)
class WhaleRequestRecord:
    id: int
    scan_id: str
    status: WhaleRequestStatus
    source: WhaleRequestSource
    started_at: datetime
    finished_at: datetime | None
    method: str
    url: str
    query_params: dict[str, str | list[str]]
    http_status: int | None
    duration_ms: int | None
    error_type: str | None
    error_message: str | None
    response_excerpt: str | None


@dataclass(frozen=True, slots=True)
class WhaleRequestCapture:
    monitor: WhaleRequestMonitor
    scan_id: str


_CAPTURE: ContextVar[WhaleRequestCapture | None] = ContextVar(
    "whale_request_capture",
    default=None,
)


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def current_whale_request_capture() -> WhaleRequestCapture | None:
    return _CAPTURE.get()


@contextmanager
def capture_whale_requests(
    monitor: WhaleRequestMonitor | None,
    scan_id: str,
) -> Iterator[None]:
    if monitor is None:
        yield
        return
    token = _CAPTURE.set(WhaleRequestCapture(monitor=monitor, scan_id=scan_id))
    try:
        yield
    finally:
        _CAPTURE.reset(token)


class WhaleRequestMonitor:
    def __init__(self, *, capacity: int = 100) -> None:
        if capacity <= 0:
            raise ValueError("请求监测容量必须大于 0")
        self.capacity = capacity
        self._records: deque[WhaleRequestRecord] = deque()
        self._records_by_id: dict[int, WhaleRequestRecord] = {}
        self._started_monotonic: dict[int, float] = {}
        self._next_id = 1
        self._subscribers: set[asyncio.Queue[WhaleRequestRecord]] = set()
        self._lock = asyncio.Lock()

    async def begin(
        self,
        *,
        scan_id: str,
        method: str,
        url: str,
        query_params: dict[str, str | list[str]],
        source: WhaleRequestSource = "http",
    ) -> int:
        async with self._lock:
            record_id = self._next_id
            self._next_id += 1
            if len(self._records) >= self.capacity:
                expired = self._records.popleft()
                self._records_by_id.pop(expired.id, None)
                self._started_monotonic.pop(expired.id, None)
            record = WhaleRequestRecord(
                id=record_id,
                scan_id=scan_id,
                status="pending",
                source=source,
                started_at=utcnow(),
                finished_at=None,
                method=method.upper(),
                url=url,
                query_params=query_params,
                http_status=None,
                duration_ms=None,
                error_type=None,
                error_message=None,
                response_excerpt=None,
            )
            self._records.append(record)
            self._records_by_id[record_id] = record
            self._started_monotonic[record_id] = monotonic()
            subscribers = tuple(self._subscribers)
        self._publish(record, subscribers)
        return record_id

    async def complete(
        self,
        record_id: int,
        *,
        status: Literal["success", "failed"],
        http_status: int | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
        response_excerpt: str | None = None,
    ) -> None:
        async with self._lock:
            record = self._records_by_id.get(record_id)
            if record is None:
                return
            started = self._started_monotonic.pop(record_id, monotonic())
            record.status = status
            record.finished_at = utcnow()
            record.http_status = http_status
            record.duration_ms = max(0, round((monotonic() - started) * 1000))
            record.error_type = error_type
            record.error_message = error_message[:2000] if error_message else None
            record.response_excerpt = response_excerpt[:4096] if response_excerpt else None
            subscribers = tuple(self._subscribers)
            published = replace(record)
        self._publish(published, subscribers)

    async def snapshot(self) -> list[WhaleRequestRecord]:
        async with self._lock:
            return [replace(record) for record in reversed(self._records)]

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[WhaleRequestRecord]]:
        queue: asyncio.Queue[WhaleRequestRecord] = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subscribers.add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                self._subscribers.discard(queue)

    @staticmethod
    def _publish(
        record: WhaleRequestRecord,
        subscribers: tuple[asyncio.Queue[WhaleRequestRecord], ...],
    ) -> None:
        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(replace(record))
