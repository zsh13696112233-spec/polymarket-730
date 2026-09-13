from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from backend.polymarket import PolymarketAPIError, PolymarketClient
from backend.public_requests import PublicRequestScheduler
from backend.whale_requests import WhaleRequestMonitor, capture_whale_requests


class Clock:
    def __init__(self):
        self.now = 100.0
        self.delays = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.delays.append(seconds)
        self.now += seconds


def scheduler(clock, **kwargs):
    return PublicRequestScheduler(clock=clock, sleep=clock.sleep, jitter=lambda: 0, **kwargs)


async def test_host_and_trade_spacing_are_shared_and_other_hosts_are_independent():
    clock = Clock()
    limiter = scheduler(clock)
    semaphore = asyncio.Semaphore(2)
    times = []
    for host, path in [
        ("data", "/trades"),
        ("gamma", "/markets"),
        ("data", "/positions"),
        ("data", "/trades"),
    ]:
        async with limiter.slot(host, path, semaphore):
            times.append(clock())
    assert times == pytest.approx([100, 100, 100.2, 100.5])


async def test_cooldown_merges_inflight_errors_and_backs_off_then_resets():
    clock = Clock()
    limiter = scheduler(clock, interval=0)
    semaphore = asyncio.Semaphore(1)
    limiter.rate_limited("data", None)
    limiter.rate_limited("data", None)
    async with limiter.slot("gamma", "/markets", semaphore):
        assert clock() == 100
    async with limiter.slot("data", "/positions", semaphore):
        assert clock() == 105
    limiter.rate_limited("data", None)
    async with limiter.slot("data", "/positions", semaphore):
        assert clock() == 115
    limiter.rate_limited("data", 37)
    limiter.rate_limited("data", 20)
    async with limiter.slot("data", "/positions", semaphore):
        assert clock() == 152
    clock.now += 61
    limiter.rate_limited("data", None)
    async with limiter.slot("data", "/positions", semaphore):
        assert clock() == 218


async def test_waiting_for_cooldown_is_cancellable_without_holding_network_permit():
    waiting = asyncio.Event()
    clock = Clock()

    async def sleep(seconds):
        waiting.set()
        await asyncio.Future()

    limiter = PublicRequestScheduler(clock=clock, sleep=sleep, jitter=lambda: 0)
    semaphore = asyncio.Semaphore(1)
    limiter.rate_limited("data", None)

    async def send():
        async with limiter.slot("data", "/positions", semaphore):
            pytest.fail("request sent during cooldown")

    task = asyncio.create_task(send())
    await waiting.wait()
    assert not semaphore.locked()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    clock.now += 6
    async with limiter.slot("data", "/positions", semaphore):
        pass
    assert not semaphore.locked()


async def test_cooldown_is_rechecked_after_waiting_for_concurrency():
    clock = Clock()
    limiter = scheduler(clock, interval=0)
    semaphore = asyncio.Semaphore(0)
    sent = []

    async def send():
        async with limiter.slot("data", "/positions", semaphore):
            sent.append(clock())

    task = asyncio.create_task(send())
    await asyncio.sleep(0)
    limiter.rate_limited("data", 10)
    semaphore.release()
    await task
    assert sent == [110]
    assert not semaphore.locked()


async def test_concurrent_waiters_resume_smoothly():
    clock = Clock()
    limiter = scheduler(clock)
    semaphore = asyncio.Semaphore(3)
    limiter.rate_limited("data", 5)
    starts = []

    async def send():
        async with limiter.slot("data", "/positions", semaphore):
            starts.append(clock())
            await asyncio.sleep(0)

    await asyncio.gather(*(send() for _ in range(5)))
    assert starts == pytest.approx([105, 105.2, 105.4, 105.6, 105.8])


async def test_http_429_cools_other_reads_without_automatic_retry_and_survives_pool_reset():
    clock = Clock()
    requests = []

    def respond(request):
        requests.append((request.url.host, request.url.path, clock()))
        return httpx.Response(429 if len(requests) == 1 else 200, json=[])

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(respond),
        scheduler=scheduler(clock),
    )
    monitor = WhaleRequestMonitor()
    try:
        with capture_whale_requests(monitor, "shared"):
            with pytest.raises(PolymarketAPIError) as error:
                await client._get_json("https://data.test/trades", params={})
            assert error.value.rate_limited
            assert len(requests) == 1
            await client._reset_http_client(client._http)
            await client._get_json("https://gamma.test/markets", params={})
            await client._get_json("https://data.test/positions", params={})
        assert requests == [
            ("data.test", "/trades", 100),
            ("gamma.test", "/markets", 100),
            ("data.test", "/positions", 105),
        ]
        records = await monitor.snapshot()
        assert len(records) == 3
        assert all(row.source == "http" for row in records)
        assert [row.http_status for row in records] == [200, 200, 429]
    finally:
        await client.close()


@pytest.mark.parametrize(
    "value,expected", [("37", 37), ("0", 0), ("-1", 0), ("bad", None), ("nan", None), ("inf", None)]
)
def test_retry_after_seconds(value, expected):
    assert PolymarketClient._parse_retry_after(value) == expected


def test_retry_after_http_date():
    raw = format_datetime(datetime.now(UTC) + timedelta(seconds=60), usegmt=True)
    assert 58 <= PolymarketClient._parse_retry_after(raw) <= 60


async def test_continuous_rate_limits_keep_maximum_backoff_after_long_cooldown():
    clock = Clock()
    limiter = scheduler(clock, interval=0)
    semaphore = asyncio.Semaphore(1)
    delays = []
    for _ in range(7):
        limiter.rate_limited("data", None)
        before = clock()
        async with limiter.slot("data", "/positions", semaphore):
            delays.append(clock() - before)
    assert delays == [5, 10, 20, 40, 60, 60, 60]
