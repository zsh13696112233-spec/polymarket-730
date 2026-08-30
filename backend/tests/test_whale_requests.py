from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from functools import partial
from types import SimpleNamespace

import httpx
import polymarket
import pytest

from backend.polymarket import PolymarketAPIError, PolymarketClient
from backend.whale_requests import WhaleRequestMonitor, capture_whale_requests


@pytest.mark.asyncio
async def test_monitor_emits_pending_and_completion_with_same_id_and_caps_history():
    monitor = WhaleRequestMonitor(capacity=2)

    async with monitor.subscribe() as queue:
        first_id = await monitor.begin(
            scan_id="scan-a",
            method="GET",
            url="https://data.test/trades",
            query_params={"limit": "500"},
        )
        pending = await asyncio.wait_for(queue.get(), timeout=1)
        assert pending.id == first_id
        assert pending.status == "pending"

        await monitor.complete(first_id, status="success", http_status=200)
        completed = await asyncio.wait_for(queue.get(), timeout=1)
        assert completed.id == first_id
        assert completed.status == "success"
        assert completed.duration_ms is not None

    for scan_id in ("scan-b", "scan-c"):
        record_id = await monitor.begin(
            scan_id=scan_id,
            method="GET",
            url="https://gamma.test/markets",
            query_params={},
        )
        await monitor.complete(record_id, status="success", http_status=200)

    snapshot = await monitor.snapshot()
    assert [record.scan_id for record in snapshot] == ["scan-c", "scan-b"]


@pytest.mark.asyncio
async def test_polymarket_request_capture_is_scoped_and_preserves_repeated_query_params():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    monitor = WhaleRequestMonitor()
    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.fetch_markets_with_tags(["condition-a", "condition-b"])
        assert await monitor.snapshot() == []

        with capture_whale_requests(monitor, "scan-1"):
            await client.fetch_markets_with_tags(["condition-a", "condition-b"])
    finally:
        await client.close()

    record = (await monitor.snapshot())[0]
    assert record.scan_id == "scan-1"
    assert record.status == "success"
    assert record.source == "http"
    assert record.url == "https://gamma.test/markets"
    assert record.query_params["condition_ids"] == ["condition-a", "condition-b"]
    assert record.query_params["include_tag"] == "true"
    assert record.http_status == 200
    assert record.response_excerpt is None


@pytest.mark.asyncio
async def test_public_sdk_request_capture_records_sdk_source(monkeypatch):
    class FakePublicClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get_public_profile(self, address):
            assert address == "0x" + "1" * 40
            return SimpleNamespace(wallet=address, name="SDK Whale", pseudonym=None)

    monkeypatch.setattr(polymarket, "AsyncPublicClient", FakePublicClient)
    monitor = WhaleRequestMonitor()
    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    )
    try:
        with capture_whale_requests(monitor, "scan-sdk"):
            await client.resolve_profile("0x" + "1" * 40, None)
    finally:
        await client.close()

    record = (await monitor.snapshot())[0]
    assert record.scan_id == "scan-sdk"
    assert record.status == "success"
    assert record.source == "sdk"
    assert record.url == "https://gamma-api.polymarket.com/public-profile"
    assert record.query_params == {"address": "0x" + "1" * 40}
    assert record.http_status == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_status", "expected_error"),
    [
        (httpx.Response(429, text="slow down"), 429, "请求过于频繁"),
        (httpx.Response(503, text="upstream down"), 503, "返回 503"),
        (httpx.Response(200, text="not-json"), 200, "无效 JSON"),
    ],
)
async def test_polymarket_request_capture_records_http_and_json_failures(
    response: httpx.Response,
    expected_status: int,
    expected_error: str,
):
    monitor = WhaleRequestMonitor()
    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(lambda request: response),
    )
    try:
        with capture_whale_requests(monitor, "scan-failure"):
            with pytest.raises(PolymarketAPIError):
                await client.fetch_large_trades(
                    filter_amount_usdc=Decimal("1000"),
                    start=datetime(2026, 8, 23),
                    end=datetime(2026, 8, 24),
                )
    finally:
        await client.close()

    record = (await monitor.snapshot())[0]
    assert record.status == "failed"
    assert record.http_status == expected_status
    assert expected_error in (record.error_message or "")
    assert record.response_excerpt == response.text


@pytest.mark.asyncio
async def test_polymarket_request_capture_records_network_failure_and_expected_404():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(404, text="missing")

    monitor = WhaleRequestMonitor()
    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        with capture_whale_requests(monitor, "scan-network"):
            with pytest.raises(PolymarketAPIError):
                await client.fetch_public_profile("0x" + "1" * 40)
            assert await client.fetch_public_profile("0x" + "2" * 40) is None
    finally:
        await client.close()

    success, failure = await monitor.snapshot()
    assert success.status == "success"
    assert success.http_status == 404
    assert failure.status == "failed"
    assert failure.http_status is None
    assert failure.error_type == "ConnectError"
    assert "connection refused" in (failure.error_message or "")


def test_whale_request_log_snapshot_api(app_client_factory):
    client, _ = app_client_factory([[]])
    monitor: WhaleRequestMonitor = client.app.state.whale_request_monitor
    record_id = client.portal.call(
        partial(
            monitor.begin,
            scan_id="scan-api",
            method="GET",
            url="https://data.test/trades",
            query_params={"side": "BUY"},
        )
    )
    client.portal.call(
        partial(
            monitor.complete,
            record_id,
            status="failed",
            http_status=503,
            error_type="HTTPError",
            error_message="upstream down",
            response_excerpt="temporary failure",
        )
    )

    response = client.get("/api/whales/request-logs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0] == {
        "id": record_id,
        "scan_id": "scan-api",
        "status": "failed",
        "source": "http",
        "started_at": payload["items"][0]["started_at"],
        "finished_at": payload["items"][0]["finished_at"],
        "method": "GET",
        "url": "https://data.test/trades",
        "query_params": {"side": "BUY"},
        "http_status": 503,
        "duration_ms": payload["items"][0]["duration_ms"],
        "error_type": "HTTPError",
        "error_message": "upstream down",
        "response_excerpt": "temporary failure",
    }
