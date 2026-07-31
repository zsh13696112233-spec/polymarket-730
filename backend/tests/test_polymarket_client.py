from __future__ import annotations

from collections import Counter

import httpx
import pytest

from backend.polymarket import InvalidWalletInput, PolymarketAPIError, PolymarketClient
from backend.tests.conftest import TEST_ADDRESS


def raw_position(asset: str) -> dict[str, object]:
    return {
        "asset": asset,
        "conditionId": "0x" + "1" * 64,
        "size": 1,
        "avgPrice": 0.4,
        "initialValue": 0.4,
        "currentValue": 0.5,
        "cashPnl": 0.1,
        "percentPnl": 25,
        "totalBought": 1,
        "realizedPnl": 0,
        "curPrice": 0.5,
        "title": "market",
        "slug": "market",
        "eventSlug": "event",
        "outcome": "Yes",
        "outcomeIndex": 0,
    }


@pytest.mark.asyncio
async def test_trade_history_chunks_market_filters_at_data_api_limit():
    conditions = [f"0x{index:064x}" for index in range(201)]
    requested_batches: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        batch = request.url.params["market"].split(",")
        requested_batches.append(batch)
        return httpx.Response(
            200,
            json=[
                {
                    "asset": f"asset-{len(requested_batches)}",
                    "conditionId": batch[0],
                    "side": "BUY",
                    "size": 2,
                    "price": 0.4,
                    "timestamp": 1_700_000_000 + len(requested_batches),
                    "transactionHash": f"0x{len(requested_batches)}",
                }
            ],
        )

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        trades = await client.fetch_trades(
            TEST_ADDRESS,
            condition_ids=conditions,
        )
    finally:
        await client.close()

    assert [len(batch) for batch in requested_batches] == [100, 100, 1]
    assert [condition for batch in requested_batches for condition in batch] == conditions
    assert len(trades) == 3


@pytest.mark.asyncio
async def test_active_snapshot_requires_both_complete_mergeable_pages():
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        mergeable = params["mergeable"]
        offset = int(params["offset"])
        if mergeable == "false" and offset == 0:
            return httpx.Response(200, json=[raw_position(f"a-{index}") for index in range(500)])
        if mergeable == "false" and offset == 500:
            return httpx.Response(200, json=[raw_position("last")])
        return httpx.Response(200, json=[raw_position("mergeable")])

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        positions = await client.fetch_active_positions(TEST_ADDRESS)
    finally:
        await client.close()

    assert len(positions) == 502
    assert Counter(call["mergeable"] for call in calls) == {"false": 2, "true": 1}
    for call in calls:
        assert call["redeemable"] == "false"
        assert call["sizeThreshold"] == "0"
        assert call["limit"] == "500"
        assert call["sortBy"] == "CURRENT"
        assert call["sortDirection"] == "DESC"


@pytest.mark.asyncio
async def test_failure_on_any_mergeable_page_rejects_whole_snapshot():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["mergeable"] == "true":
            return httpx.Response(500, text="upstream")
        return httpx.Response(200, json=[raw_position("safe")])

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(PolymarketAPIError):
            await client.fetch_active_positions(TEST_ADDRESS)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_retry_after_is_preserved_on_rate_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"Retry-After": "37"},
            text="rate limited",
        )

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(PolymarketAPIError) as raised:
            await client.resolve_profile(TEST_ADDRESS, None)
    finally:
        await client.close()

    assert raised.value.retry_after == 37
    assert "请求过于频繁" in str(raised.value)


@pytest.mark.asyncio
async def test_http_timeout_is_reported_as_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("upstream timed out", request=request)

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(PolymarketAPIError, match="接口连接失败"):
            await client.resolve_profile(TEST_ADDRESS, None)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_invalid_json_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"this is not json",
            headers={"Content-Type": "application/json"},
        )

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(PolymarketAPIError, match="无效 JSON"):
            await client.resolve_profile(TEST_ADDRESS, None)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_unexpected_positions_response_shape_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(PolymarketAPIError, match="持仓接口返回格式无效"):
            await client.fetch_active_positions(TEST_ADDRESS)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_second_positions_page_failure_rejects_first_full_page():
    requested_offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        mergeable = request.url.params["mergeable"]
        offset = int(request.url.params["offset"])
        if mergeable == "true":
            return httpx.Response(200, json=[])
        requested_offsets.append(offset)
        if offset == 0:
            return httpx.Response(
                200,
                json=[raw_position(f"page-one-{index}") for index in range(500)],
            )
        return httpx.Response(503, text="second page failed")

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(PolymarketAPIError, match="返回 503"):
            await client.fetch_active_positions(TEST_ADDRESS)
    finally:
        await client.close()

    assert requested_offsets == [0, 500]


def test_invalid_non_polymarket_url_is_rejected():
    from backend.polymarket import parse_wallet_input

    with pytest.raises(InvalidWalletInput):
        parse_wallet_input(f"https://evil.test/profile/{TEST_ADDRESS}")
