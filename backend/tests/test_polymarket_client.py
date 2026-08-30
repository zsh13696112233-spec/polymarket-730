from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic
from types import SimpleNamespace

import httpx
import polymarket
import pytest

from backend.polymarket import InvalidWalletInput, PolymarketAPIError, PolymarketClient
from backend.tests.conftest import TEST_ADDRESS


@pytest.mark.asyncio
async def test_resolve_localized_sports_url_uses_event_slug_and_maps_outcomes(monkeypatch):
    market = SimpleNamespace(
        condition_id="0x" + "9" * 64,
        question="测试市场会通过吗？",
        group_item_title=None,
        slug="test-market",
        icon=None,
        image=None,
        tags=(),
        events=(SimpleNamespace(slug="test-event", title="测试事件"),),
        outcomes=SimpleNamespace(
            yes=SimpleNamespace(label="Yes", token_id="asset-yes", price=Decimal("0.51")),
            no=SimpleNamespace(label="No", token_id="asset-no", price=Decimal("0.49")),
        ),
        state=SimpleNamespace(
            closed=False,
            active=True,
            accepting_orders=True,
            neg_risk=False,
            end_date=None,
        ),
        trading=SimpleNamespace(
            minimum_order_size=Decimal("5"),
            minimum_tick_size=Decimal("0.01"),
            fee_schedule=SimpleNamespace(rate=Decimal("0.05"), exponent=1),
        ),
        metrics=SimpleNamespace(liquidity=Decimal("1000"), volume_24hr=Decimal("2000")),
        prices=SimpleNamespace(best_bid=Decimal("0.49"), best_ask=Decimal("0.51")),
    )

    class FakePublicClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get_event(self, *, slug: str):
            assert slug == "epl-che-bri-2026-08-30"
            return SimpleNamespace(title="测试事件", slug="test-event", markets=(market,))

    monkeypatch.setattr(polymarket, "AsyncPublicClient", FakePublicClient)
    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    )
    try:
        resolved = await client.resolve_market_url(
            "https://polymarket.com/zh/sports/epl/epl-che-bri-2026-08-30"
        )
    finally:
        await client.close()

    assert resolved.event_title == "测试事件"
    assert len(resolved.markets) == 1
    assert resolved.markets[0].outcomes == ("Yes", "No")
    assert resolved.markets[0].clob_token_ids == ("asset-yes", "asset-no")
    assert resolved.markets[0].fee_rate == Decimal("0.05")


@pytest.mark.asyncio
async def test_resolve_market_url_rejects_non_polymarket_hosts():
    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    )
    try:
        with pytest.raises(InvalidWalletInput, match="仅支持"):
            await client.resolve_market_url("https://example.com/event/test-event")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_large_trades_fetches_one_cash_filtered_page_and_parses_amount():
    start = datetime(2026, 8, 16, 1, 2, 3)
    end = datetime(2026, 8, 16, 2, 3, 4)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "data.test"
        assert request.url.path == "/trades"
        assert request.url.params["filterType"] == "CASH"
        assert request.url.params["filterAmount"] == "1000.00"
        assert "start" not in request.url.params
        assert request.url.params["end"] == str(int(end.replace(tzinfo=UTC).timestamp()))
        assert request.url.params["limit"] == "123"
        assert request.url.params["offset"] == "456"
        assert request.url.params["takerOnly"] == "false"
        assert request.url.params["side"] == "BUY"
        return httpx.Response(
            200,
            json=[
                {
                    "proxyWallet": TEST_ADDRESS.upper(),
                    "side": "buy",
                    "asset": "asset-1",
                    "conditionId": "0x" + "1" * 64,
                    "size": "20000",
                    "price": "0.56",
                    "timestamp": "1786886550",
                    "title": "LoL match",
                    "slug": "lol-market",
                    "eventSlug": "lol-event",
                    "icon": "https://example.test/icon.png",
                    "outcome": "Home",
                    "outcomeIndex": "0",
                    "name": "SineNooneEI",
                    "pseudonym": "Any-Keystone",
                    "transactionHash": "0xtrade",
                },
                {
                    "proxyWallet": TEST_ADDRESS,
                    "side": "MINT",
                    "asset": "ignored",
                    "conditionId": "0x" + "2" * 64,
                    "size": 1,
                    "price": 0.5,
                    "timestamp": 1786886550,
                },
            ],
        )

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        trades = await client.fetch_large_trades(
            filter_amount_usdc=Decimal("1000.00"),
            start=start,
            end=end,
            limit=123,
            offset=456,
        )
    finally:
        await client.close()

    assert len(trades) == 1
    trade = trades[0]
    assert trade.proxy_wallet == TEST_ADDRESS
    assert trade.side == "BUY"
    assert trade.size == Decimal("20000")
    assert trade.price == Decimal("0.56")
    assert trade.amount == Decimal("11200.00")
    assert trade.timestamp == datetime.fromtimestamp(1786886550, tz=UTC).replace(tzinfo=None)
    assert trade.display_name == "SineNooneEI"
    assert trade.outcome_index == 0


@pytest.mark.asyncio
async def test_large_trade_requests_are_serialized_and_paced():
    started_at: list[float] = []
    active_requests = 0
    max_active_requests = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal active_requests, max_active_requests
        started_at.append(monotonic())
        active_requests += 1
        max_active_requests = max(max_active_requests, active_requests)
        await asyncio.sleep(0.01)
        active_requests -= 1
        return httpx.Response(200, json=[])

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    client.LARGE_TRADE_REQUEST_INTERVAL_SECONDS = 0.05
    now = datetime(2026, 8, 16, 2, 3, 4)
    try:
        await asyncio.gather(
            client.fetch_large_trades(
                filter_amount_usdc=Decimal("1000"),
                start=now,
                end=now,
            ),
            client.fetch_large_trades(
                filter_amount_usdc=Decimal("1000"),
                start=now,
                end=now,
            ),
        )
    finally:
        await client.close()

    assert max_active_requests == 1
    assert len(started_at) == 2
    assert started_at[1] - started_at[0] >= 0.04


@pytest.mark.asyncio
async def test_whale_markets_batch_include_tags_and_parse_json_fields_and_dates():
    conditions = [f"0x{index:064x}" for index in range(101)]
    requested_batches: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        batch = request.url.params.get_list("condition_ids")
        requested_batches.append(batch)
        assert request.url.path == "/markets"
        assert request.url.params["include_tag"] == "true"
        assert int(request.url.params["limit"]) == len(batch)
        if len(requested_batches) == 1:
            return httpx.Response(
                200,
                json=[
                    {
                        "conditionId": conditions[0],
                        "question": "Will Home win?",
                        "slug": "home-win",
                        "events": [{"slug": "match-event"}],
                        "image": "https://example.test/market.png",
                        "tags": '[{"id":64,"label":"Esports","slug":"esports"}]',
                        "closed": False,
                        "active": "true",
                        "acceptingOrders": True,
                        "negRisk": False,
                        "endDate": "2026-08-16",
                        "outcomes": '["Home","Away"]',
                        "outcomePrices": '["0.56","0.44"]',
                        "clobTokenIds": '["asset-home","asset-away"]',
                        "liquidity": "12345.67",
                        "volume24hr": "7654.32",
                        "bestBid": "0.55",
                        "bestAsk": "0.57",
                        "orderMinSize": "5",
                        "orderPriceMinTickSize": "0.01",
                        "feeSchedule": '{"rate":"0.05","exponent":1}',
                    }
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "conditionId": conditions[100],
                    "question": "Precise market",
                    "slug": "precise-market",
                    "eventSlug": "precise-event",
                    "tags": [],
                    "closed": False,
                    "active": True,
                    "acceptingOrders": True,
                    "endDate": "2026-08-16T12:34:56Z",
                    "outcomes": ["Yes", "No"],
                    "outcomePrices": [0.2, 0.8],
                    "clobTokenIds": ["yes", "no"],
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
        markets = await client.fetch_markets_with_tags(conditions)
    finally:
        await client.close()

    assert [len(batch) for batch in requested_batches] == [100, 1]
    assert [condition for batch in requested_batches for condition in batch] == conditions
    assert len(markets) == 2
    first = markets[0]
    assert first.event_slug == "match-event"
    assert first.tags[0].id == "64"
    assert first.tags[0].slug == "esports"
    assert first.outcomes == ("Home", "Away")
    assert first.outcome_prices == (Decimal("0.56"), Decimal("0.44"))
    assert first.clob_token_ids == ("asset-home", "asset-away")
    assert first.end_date is None
    assert first.end_date_is_date_only is True
    assert first.fee_rate == Decimal("0.05")
    assert first.fee_exponent == Decimal("1")
    assert markets[1].end_date == datetime(2026, 8, 16, 12, 34, 56)
    assert markets[1].end_date_is_date_only is False


@pytest.mark.asyncio
async def test_active_whale_markets_paginate_and_filter_non_tradable_rows():
    condition_ids = [f"0x{index:064x}" for index in range(3)]
    cursors: list[str | None] = []

    def market(condition_id: str, *, active: bool = True) -> dict[str, object]:
        return {
            "conditionId": condition_id,
            "question": condition_id,
            "closed": False,
            "active": active,
            "acceptingOrders": True,
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.6","0.4"]',
            "clobTokenIds": f'["{condition_id}-yes","{condition_id}-no"]',
            "liquidity": "5000",
        }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/markets/keyset"
        assert request.url.params["closed"] == "false"
        assert request.url.params["liquidity_num_min"] == "5000"
        assert request.url.params["volume_num_min"] == "10000"
        assert request.url.params["include_tag"] == "true"
        cursor = request.url.params.get("after_cursor")
        cursors.append(cursor)
        if cursor is None:
            return httpx.Response(
                200,
                json={
                    "markets": [
                        market(condition_ids[0]),
                        market(condition_ids[1], active=False),
                    ],
                    "next_cursor": "page-2",
                },
            )
        assert cursor == "page-2"
        return httpx.Response(200, json={"markets": [market(condition_ids[2])]})

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        markets = await client.fetch_active_whale_markets(
            min_liquidity_usdc=Decimal("5000"),
            min_volume_usdc=Decimal("10000"),
            page_size=2,
        )
    finally:
        await client.close()

    assert cursors == [None, "page-2"]
    assert [market.condition_id for market in markets] == [condition_ids[0], condition_ids[2]]


@pytest.mark.asyncio
async def test_official_holders_and_market_positions_are_parsed(monkeypatch):
    condition_id = "0x" + "a" * 64
    asset_id = "asset-yes"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/holders"
        assert request.url.params["market"] == condition_id
        assert request.url.params["limit"] == "20"
        assert request.url.params["minBalance"] == "10000"
        return httpx.Response(
            200,
            json=[
                {
                    "token": asset_id,
                    "holders": [
                        {
                            "proxyWallet": TEST_ADDRESS.upper(),
                            "asset": asset_id,
                            "amount": "25000.5",
                            "outcomeIndex": 0,
                            "name": "Position Whale",
                            "profileImage": "https://example.test/whale.png",
                            "verified": True,
                        }
                    ],
                }
            ],
        )

    position = SimpleNamespace(
        wallet=TEST_ADDRESS.upper(),
        token_id=asset_id,
        condition_id=condition_id,
        avg_price=Decimal("0.40"),
        size=Decimal("25000.5"),
        total_bought=Decimal("25000.5"),
        cur_price=Decimal("0.60"),
        current_value=Decimal("15000.3"),
        outcome="Yes",
        outcome_index=0,
        name="Position Whale",
        profile_image="https://example.test/whale.png",
        verified=True,
    )

    class FakePaginator:
        async def first_page(self):
            return SimpleNamespace(items=(SimpleNamespace(token=asset_id, positions=(position,)),))

    class FakePublicClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def list_market_positions(self, **kwargs):
            assert kwargs == {
                "market": condition_id,
                "status": "OPEN",
                "sort_by": "TOKENS",
                "sort_direction": "DESC",
                "page_size": 20,
            }
            return FakePaginator()

    monkeypatch.setattr(polymarket, "AsyncPublicClient", FakePublicClient)

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        holders = await client.fetch_top_holders([condition_id], min_balance=10000, limit=20)
        positions = await client.fetch_market_positions(condition_id)
    finally:
        await client.close()

    assert holders[0].proxy_wallet == TEST_ADDRESS
    assert holders[0].amount == Decimal("25000.5")
    assert holders[0].verified_badge is True
    assert positions[0].condition_id == condition_id
    assert positions[0].total_bought == Decimal("25000.5")
    assert positions[0].avg_price == Decimal("0.40")
    assert positions[0].current_value == Decimal("15000.3")


@pytest.mark.asyncio
async def test_resolve_profile_uses_public_sdk(monkeypatch):
    class FakePublicClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get_public_profile(self, address):
            assert address == TEST_ADDRESS
            return SimpleNamespace(
                wallet=TEST_ADDRESS.upper(),
                name="SDK Whale",
                pseudonym="sdk-whale",
            )

    monkeypatch.setattr(polymarket, "AsyncPublicClient", FakePublicClient)
    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    )
    try:
        profile = await client.resolve_profile(TEST_ADDRESS, None)
    finally:
        await client.close()

    assert profile.submitted_address == TEST_ADDRESS
    assert profile.proxy_wallet == TEST_ADDRESS
    assert profile.label == "SDK Whale"


@pytest.mark.asyncio
async def test_whale_public_profile_parses_fields_and_returns_none_for_404():
    missing_address = "0x" + "2" * 40

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/public-profile"
        address = request.url.params["address"]
        if address == missing_address:
            return httpx.Response(404, text="not found")
        assert address == TEST_ADDRESS
        return httpx.Response(
            200,
            json={
                "createdAt": "2026-03-27T00:18:03.788884Z",
                "proxyWallet": TEST_ADDRESS.upper(),
                "pseudonym": "Whirlwind-Catalogue",
                "name": "Named Whale",
                "profileImage": "https://example.test/whale-avatar.jpg",
                "verifiedBadge": True,
                "takerTier": "3",
                "takerTierName": "Tier 3",
                "weightedVolume": "123456.78",
            },
        )

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        profile = await client.fetch_public_profile(TEST_ADDRESS.upper())
        missing = await client.fetch_public_profile(missing_address)
    finally:
        await client.close()

    assert profile is not None
    assert profile.proxy_wallet == TEST_ADDRESS
    assert profile.display_name == "Named Whale"
    assert profile.profile_image_url == "https://example.test/whale-avatar.jpg"
    assert profile.created_at == datetime(2026, 3, 27, 0, 18, 3, 788884)
    assert profile.verified_badge is True
    assert profile.taker_tier == 3
    assert profile.weighted_volume == Decimal("123456.78")
    assert missing is None


@pytest.mark.asyncio
async def test_fetch_official_tags_uses_stable_order_and_skips_invalid_rows(monkeypatch):
    class FakePaginator:
        async def iter_items(self):
            for item in (
                SimpleNamespace(id=1, slug="sports", label="Sports"),
                SimpleNamespace(id=64, slug="esports", label="Esports"),
                SimpleNamespace(id=65, slug=None, label="missing slug"),
            ):
                yield item

    class FakePublicClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def list_tags(self, **kwargs):
            assert kwargs == {"order": "id", "ascending": True, "page_size": 100}
            return FakePaginator()

    monkeypatch.setattr(polymarket, "AsyncPublicClient", FakePublicClient)

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    )
    try:
        tags = await client.fetch_tags()
    finally:
        await client.close()

    assert [(tag.id, tag.slug, tag.label) for tag in tags] == [
        ("1", "sports", "Sports"),
        ("64", "esports", "Esports"),
    ]


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
        positions = await client.fetch_active_positions(
            TEST_ADDRESS,
            condition_ids=["0x" + "9" * 64],
        )
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
        assert call["market"] == "0x" + "9" * 64


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
            await client.fetch_public_profile(TEST_ADDRESS)
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
        with pytest.raises(PolymarketAPIError, match="接口连接失败") as raised:
            await client.fetch_public_profile(TEST_ADDRESS)
    finally:
        await client.close()

    assert "ReadTimeout" in str(raised.value)
    assert "gamma.test" in str(raised.value)


@pytest.mark.asyncio
async def test_http_client_pool_is_replaced_after_network_error():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("stale proxy tunnel", request=request)
        return httpx.Response(200, json={"name": "Recovered"})

    client = PolymarketClient(
        data_api_url="https://data.test",
        gamma_api_url="https://gamma.test",
        timeout=1,
        transport=httpx.MockTransport(handler),
    )
    original = client._http
    try:
        with pytest.raises(PolymarketAPIError):
            await client.fetch_public_profile(TEST_ADDRESS)
        assert client._http is not original
        profile = await client.fetch_public_profile(TEST_ADDRESS)
    finally:
        await client.close()

    assert profile is not None
    assert profile.display_name == "Recovered"


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
            await client.fetch_public_profile(TEST_ADDRESS)
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
