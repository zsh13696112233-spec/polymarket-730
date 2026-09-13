"""Process-local scheduling for public HTTP reads; authenticated SDK traffic is separate."""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic


@dataclass
class _HostState:
    next_at: float = 0
    cooldown_until: float = 0
    last_limited_at: float | None = None
    backoff_step: int = 0


class PublicRequestScheduler:
    def __init__(
        self,
        *,
        interval: float = 0.2,
        trades_interval: float = 0.5,
        clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.interval = interval
        self.trades_interval = trades_interval
        self._clock = clock
        self._sleep = sleep
        self._jitter = jitter
        self._hosts: dict[str, _HostState] = {}
        self._trades_next: dict[str, float] = {}

    def _delay(self, host: str, path: str) -> float:
        state = self._hosts.setdefault(host, _HostState())
        return max(
            0,
            state.next_at - self._clock(),
            state.cooldown_until - self._clock(),
            self._trades_next.get(host, 0) - self._clock() if path == "/trades" else 0,
        )

    @asynccontextmanager
    async def slot(self, host: str, path: str, semaphore: asyncio.Semaphore) -> AsyncIterator[None]:
        while True:
            delay = self._delay(host, path)
            if delay > 0:
                await self._sleep(delay)
                continue
            await semaphore.acquire()
            # Another task may have sent a request or received 429 while we waited.
            if self._delay(host, path) > 0:
                semaphore.release()
                continue
            now = self._clock()
            self._hosts[host].next_at = now + self.interval
            if path == "/trades":
                self._trades_next[host] = now + self.trades_interval
            break
        try:
            yield
        finally:
            semaphore.release()

    def rate_limited(self, host: str, retry_after: float | None) -> None:
        now = self._clock()
        state = self._hosts.setdefault(host, _HostState())
        # Time spent unable to send during cooldown is not evidence of recovery.
        if (
            state.last_limited_at is None
            or now - max(state.last_limited_at, state.cooldown_until) >= 60
        ):
            state.backoff_step = 0
        # Responses already in flight belong to the same cooldown episode.
        if state.cooldown_until <= now:
            fallback = min(60, 5 * 2 ** min(state.backoff_step, 4))
            state.backoff_step += 1
            delay = retry_after if retry_after is not None else fallback + self._jitter()
            state.cooldown_until = now + delay
        elif retry_after is not None:
            state.cooldown_until = max(state.cooldown_until, now + retry_after)
        state.last_limited_at = now
