from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from backend.polymarket import PolymarketAPIError, PolymarketClient


def client_for(handler, base="https://data.test"):
    return PolymarketClient(
        data_api_url=base,
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )


def page(rows, cursor=None):
    return httpx.Response(
        200,
        json={
            "data": rows,
            "pagination": {
                "has_more": cursor is not None,
                "next_cursor": cursor,
            },
        },
    )


def trade(timestamp, token="1"):
    return {
        "token_id": token,
        "condition_id": "condition",
        "proxy_wallet": "wallet",
        "side": "BUY",
        "price": "0.5",
        "size": "10",
        "timestamp": timestamp,
    }


@pytest.mark.parametrize(
    "base", ["https://data.test", "https://data.test/v1/", "https://data.test/v2/"]
)
async def test_cursor_walk_fills_short_and_empty_pages_and_applies_local_window(base):
    calls = []

    def handler(request):
        assert request.url.path == "/v2/trades"
        assert "offset" not in request.url.params
        assert request.url.params["condition"] == "condition"
        cursor = request.url.params.get("cursor")
        calls.append(cursor)
        if cursor is None:
            return page([trade(300)], "empty")  # Newer than requested end.
        if cursor == "empty":
            return page([], "short")
        if cursor == "short":
            return page([trade(200)], "last")
        return page([trade(150, "2"), trade(99)], "older")

    client = client_for(handler, base)
    try:
        result = await client.fetch_large_trades(
            filter_amount_usdc=Decimal(0),
            start=datetime.fromtimestamp(100, UTC).replace(tzinfo=None),
            end=datetime.fromtimestamp(200, UTC).replace(tzinfo=None),
            offset=1,
            limit=2,
            condition_ids=["condition"],
        )
    finally:
        await client.close()
    assert [item.asset_id for item in result] == ["2"]
    assert calls == [None, "empty", "short", "last"]


@pytest.mark.parametrize("failure", ["repeat", "missing", "contradiction", "invalid", "http"])
async def test_incomplete_pages_fail_closed(failure):
    def handler(request):
        if "cursor" not in request.url.params:
            return page([], "next")
        if failure == "repeat":
            return page([], "next")
        if failure == "missing":
            return httpx.Response(200, json={"data": []})
        if failure == "contradiction":
            return httpx.Response(
                200, json={"data": [], "pagination": {"has_more": True, "next_cursor": None}}
            )
        if failure == "invalid":
            return page([None])
        return httpx.Response(503)

    client = client_for(handler)
    try:
        with pytest.raises(PolymarketAPIError):
            await client.fetch_redemptions("wallet")
    finally:
        await client.close()


async def test_missing_position_economics_is_not_silently_zero():
    client = client_for(
        lambda request: page(
            [
                {
                    "token_id": "token",
                    "condition_id": "condition",
                    "current_size": None,
                    "avg_price": None,
                    "mergeable": False,
                }
            ]
        )
    )
    try:
        with pytest.raises(PolymarketAPIError, match="数值缺失"):
            await client.fetch_active_positions("wallet")
    finally:
        await client.close()


async def test_position_condition_batches_keep_all_filters_and_rows():
    calls = []

    def handler(request):
        query = request.url.params
        assert query["status"] == "REDEEMABLE"
        assert query["user"] == "wallet"
        assert query["include_archived"] == "true"
        conditions = query["condition"].split(",")
        assert len(conditions) <= 20
        calls.extend(conditions)
        return page(
            [
                {
                    "token_id": condition,
                    "condition_id": condition,
                    "current_size": 10,
                    "avg_price": 0.5,
                    "current_price": 1,
                    "current_value": 10,
                    "total_size": 10,
                    "realized_pnl": 0,
                    "mergeable": False,
                }
                for condition in conditions
            ]
        )

    client = client_for(handler)
    try:
        results = await client.fetch_redeemable_positions(
            "wallet", condition_ids=map(str, range(21))
        )
    finally:
        await client.close()
    assert {item.asset_id for item in results} == set(map(str, range(21)))
    assert set(calls) == set(map(str, range(21)))
