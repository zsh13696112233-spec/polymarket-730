from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from backend.polymarket import InvalidWalletInput, PolymarketAPIError, PolymarketClient
from backend.tests.conftest import TEST_ADDRESS


@pytest.mark.asyncio
async def test_market_end_date_uses_precise_gamma_timestamp_and_cache():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.host == "gamma.test"
        assert request.url.params["slug"] == "temperature-market"
        return httpx.Response(200, json=[{"endDate": "2026-08-02T12:00:00Z"}])

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        first = await client.fetch_market_end_date(
            market_slug="temperature-market",
            condition_id="0x" + "1" * 64,
        )
        second = await client.fetch_market_end_date(
            market_slug="temperature-market",
            condition_id="0x" + "1" * 64,
        )
    finally:
        await client.close()

    assert first == datetime(2026, 8, 2, 12)
    assert second == first
    assert calls == 1


@pytest.mark.asyncio
async def test_market_end_date_rejects_date_only_midnight_assumption():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"endDate": "2026-08-02"}])

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        end_date = await client.fetch_market_end_date(
            market_slug="temperature-market",
            condition_id="0x" + "1" * 64,
        )
    finally:
        await client.close()

    assert end_date is None


@pytest.mark.asyncio
async def test_market_resolutions_batch_repeated_conditions_and_parse_asset_payouts():
    conditions = [f"0x{index:064x}" for index in range(101)]
    requested_batches: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        batch = request.url.params.get_list("condition_ids")
        requested_batches.append(batch)
        assert request.url.params["closed"] == "true"
        assert int(request.url.params["limit"]) == len(batch)
        if len(requested_batches) == 1:
            return httpx.Response(
                200,
                json=[
                    {
                        "conditionId": conditions[0],
                        "closed": True,
                        "umaResolutionStatus": "resolved",
                        "clobTokenIds": '["asset-win", "asset-lose"]',
                        "outcomePrices": '["1", "0"]',
                        "closedTime": "2026-08-03 22:44:42+00",
                    },
                    {
                        "conditionId": conditions[1],
                        "closed": True,
                        "umaResolutionStatus": "proposed",
                        "clobTokenIds": '["asset-a", "asset-b"]',
                        "outcomePrices": '["1", "0"]',
                    },
                    {
                        "conditionId": conditions[2],
                        "closed": True,
                        "umaResolutionStatus": "resolved",
                        "clobTokenIds": '["asset-a", "asset-b"]',
                        "outcomePrices": '["1"]',
                    },
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "conditionId": conditions[100],
                    "closed": True,
                    "umaResolutionStatus": "finalized",
                    "clobTokenIds": ["asset-half-a", "asset-half-b"],
                    "outcomePrices": ["0.5", "0.5"],
                    "umaEndDate": "2026-08-04T01:02:03Z",
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
        resolutions = await client.fetch_market_resolutions(conditions)
    finally:
        await client.close()

    assert [len(batch) for batch in requested_batches] == [100, 1]
    assert [condition for batch in requested_batches for condition in batch] == conditions
    assert set(resolutions) == {conditions[0], conditions[100]}
    assert resolutions[conditions[0]].payout_by_asset_id == {
        "asset-win": Decimal("1"),
        "asset-lose": Decimal("0"),
    }
    assert resolutions[conditions[0]].resolved_at == datetime(2026, 8, 3, 22, 44, 42)
    assert resolutions[conditions[100]].payout_by_asset_id["asset-half-a"] == Decimal("0.5")
    assert resolutions[conditions[100]].resolved_at == datetime(2026, 8, 4, 1, 2, 3)


@pytest.mark.asyncio
async def test_settlement_evidence_uses_repeated_gamma_condition_parameters():
    conditions = ["0x" + "1" * 64, "0x" + "2" * 64]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "gamma.test":
            assert request.url.params.get_list("condition_ids") == conditions
            return httpx.Response(
                200,
                json=[
                    {
                        "conditionId": condition_id,
                        "closed": True,
                        "umaResolutionStatus": "resolved",
                    }
                    for condition_id in conditions
                ],
            )
        return httpx.Response(200, json=[])

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        evidence = await client.fetch_settlement_evidence(
            TEST_ADDRESS,
            condition_ids=conditions,
            last_seen_by_condition={},
        )
    finally:
        await client.close()

    assert evidence.resolved_condition_ids == frozenset(conditions)


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


def raw_closed_position(index: int) -> dict[str, object]:
    return {
        "asset": f"closed-asset-{index}",
        "conditionId": f"0x{index:064x}",
        "avgPrice": 0.4,
        "totalBought": 10 + index,
        "realizedPnl": index - 25,
        "timestamp": 1_700_000_000 + index,
        "title": f"closed market {index}",
        "slug": f"closed-market-{index}",
        "eventSlug": f"closed-event-{index}",
        "outcome": "Yes",
    }


@pytest.mark.asyncio
async def test_closed_positions_are_parsed_paginated_and_cached():
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        offset = int(params["offset"])
        if offset == 0:
            return httpx.Response(
                200,
                json=[raw_closed_position(index) for index in range(50)],
            )
        return httpx.Response(200, json=[raw_closed_position(50)])

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        first = await client.fetch_closed_positions(TEST_ADDRESS)
        second = await client.fetch_closed_positions(TEST_ADDRESS)
    finally:
        await client.close()

    assert len(first) == 51
    assert second == first
    assert first[0].asset_id == "closed-asset-0"
    assert first[0].realized_pnl == Decimal("-25")
    assert first[-1].market_slug == "closed-market-50"
    assert [call["offset"] for call in calls] == ["0", "50"]
    assert all(call["sortBy"] == "TIMESTAMP" for call in calls)


def raw_redemption(index: int) -> dict[str, object]:
    return {
        "type": "REDEEM",
        "conditionId": f"0x{index:064x}",
        "asset": "",
        "size": 10 + index,
        "usdcSize": 10 + index,
        "timestamp": 1_700_000_000 + index,
        "transactionHash": f"0xredeem{index}",
        "title": f"redeemed market {index}",
        "slug": f"redeemed-market-{index}",
        "eventSlug": f"redeemed-event-{index}",
        "outcome": "Yes",
        "outcomeIndex": 0,
    }


@pytest.mark.asyncio
async def test_redemption_history_is_parsed_and_paginated():
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        offset = int(params["offset"])
        if offset == 0:
            return httpx.Response(200, json=[raw_redemption(index) for index in range(500)])
        return httpx.Response(200, json=[raw_redemption(500)])

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        redemptions = await client.fetch_redemptions(
            TEST_ADDRESS,
            start=datetime.fromtimestamp(1_699_999_999, tz=UTC).replace(tzinfo=None),
            end=datetime.fromtimestamp(1_700_001_000, tz=UTC).replace(tzinfo=None),
        )
    finally:
        await client.close()

    assert len(redemptions) == 501
    assert redemptions[-1].title == "redeemed market 500"
    assert redemptions[-1].size == 510
    assert redemptions[-1].usdc_size == 510
    assert redemptions[-1].market_slug == "redeemed-market-500"
    assert [call["offset"] for call in calls] == ["0", "500"]
    assert all(call["type"] == "REDEEM" for call in calls)
    assert all("start" in call and "end" in call for call in calls)


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
async def test_redeemable_snapshot_targets_conditions_and_deduplicates_assets():
    conditions = ["0x" + "1" * 64, "0x" + "2" * 64]
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        return httpx.Response(200, json=[raw_position("redeemable-asset")])

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        positions = await client.fetch_redeemable_positions(
            TEST_ADDRESS,
            condition_ids=conditions,
        )
    finally:
        await client.close()

    assert [item.asset_id for item in positions] == ["redeemable-asset"]
    assert Counter(call["mergeable"] for call in calls) == {"false": 1, "true": 1}
    for call in calls:
        assert call["redeemable"] == "true"
        assert call["market"] == ",".join(conditions)


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
