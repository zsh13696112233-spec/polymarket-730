from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.polymarket import (
    ClosedPositionSnapshot,
    LargeTradeSnapshot,
    MarketResolution,
    OfficialTag,
    PolymarketAPIError,
    PositionSnapshot,
    PublicProfile,
    RedemptionSnapshot,
    SettlementEvidence,
    TradeSnapshot,
    WhaleMarketSnapshot,
    WhalePublicProfile,
    parse_wallet_input,
)

TEST_ADDRESS = "0x6ff2cb14da8be7eb57541d250a0196c5f295f140"


def position(
    *,
    asset_id: str = "asset-1",
    condition_id: str = "0x" + "1" * 64,
    size: str = "10",
    avg_price: str = "0.40",
    current_price: str = "0.50",
    current_value: str | None = None,
    title: str = "测试市场",
    outcome: str = "Yes",
) -> PositionSnapshot:
    size_decimal = Decimal(size)
    average_decimal = Decimal(avg_price)
    price_decimal = Decimal(current_price)
    return PositionSnapshot(
        asset_id=asset_id,
        condition_id=condition_id,
        title=title,
        outcome=outcome,
        outcome_index=0,
        icon_url="https://example.test/icon.png",
        event_slug=f"event-{asset_id}",
        market_slug=f"market-{asset_id}",
        size=size_decimal,
        avg_price=average_decimal,
        current_price=price_decimal,
        initial_value=size_decimal * average_decimal,
        current_value=(
            Decimal(current_value) if current_value is not None else size_decimal * price_decimal
        ),
        cash_pnl=size_decimal * (price_decimal - average_decimal),
        percent_pnl=(
            (price_decimal - average_decimal) / average_decimal * 100
            if average_decimal
            else Decimal("0")
        ),
        total_bought=size_decimal,
        realized_pnl=Decimal("0"),
        end_date=None,
    )


class FakePolymarketClient:
    def __init__(self, snapshots: list[list[PositionSnapshot] | Exception]) -> None:
        self.snapshots = list(snapshots)
        self.last_snapshot: list[PositionSnapshot] = []
        self.trades: list[TradeSnapshot] = []
        self.trade_error: Exception | None = None
        self.market_end_dates: dict[str, datetime | None] = {}
        self.redemptions: list[RedemptionSnapshot] = []
        self.redemption_error: Exception | None = None
        self.closed_positions: list[ClosedPositionSnapshot] | None = None
        self.evidence = SettlementEvidence(frozenset(), frozenset(), frozenset())
        self.settlement_calls: list[list[str]] = []
        self.market_resolutions: dict[str, MarketResolution] = {}
        self.market_resolution_error: Exception | None = None
        self.market_resolution_calls: list[list[str]] = []
        self.redeemable_positions: list[PositionSnapshot] = []
        self.redeemable_position_calls: list[tuple[str, list[str]]] = []
        self.large_trades: list[LargeTradeSnapshot] = []
        self.large_trade_error: Exception | None = None
        self.large_trade_calls: list[dict[str, Any]] = []
        self.whale_markets: list[WhaleMarketSnapshot] = []
        self.whale_market_error: Exception | None = None
        self.whale_market_calls: list[list[str]] = []
        self.public_profiles: dict[str, WhalePublicProfile | None] = {}
        self.public_profile_error: Exception | None = None
        self.public_profile_calls: list[str] = []
        self.official_tags: list[OfficialTag] = []
        self.tag_error: Exception | None = None
        self.tag_calls: list[int] = []

    async def resolve_profile(self, raw_input: str, requested_label: str | None) -> PublicProfile:
        address = parse_wallet_input(raw_input)
        return PublicProfile(
            submitted_address=address,
            proxy_wallet=address,
            label=(requested_label or "jjavi").strip() or "jjavi",
        )

    async def fetch_active_positions(self, user: str) -> list[PositionSnapshot]:
        if self.snapshots:
            value = self.snapshots.pop(0)
            if isinstance(value, Exception):
                raise value
            self.last_snapshot = value
        return list(self.last_snapshot)

    async def fetch_large_trades(
        self,
        *,
        filter_amount_usdc: Decimal,
        start: datetime,
        limit: int = 500,
        offset: int = 0,
    ) -> list[LargeTradeSnapshot]:
        self.large_trade_calls.append(
            {
                "filter_amount_usdc": filter_amount_usdc,
                "start": start,
                "limit": limit,
                "offset": offset,
            }
        )
        if self.large_trade_error is not None:
            raise self.large_trade_error
        eligible = [
            trade
            for trade in self.large_trades
            if trade.amount >= filter_amount_usdc and trade.timestamp >= start
        ]
        eligible.sort(key=lambda trade: trade.timestamp, reverse=True)
        return eligible[offset : offset + limit]

    async def fetch_markets_with_tags(
        self,
        condition_ids: Iterable[str],
    ) -> list[WhaleMarketSnapshot]:
        conditions = list(condition_ids)
        self.whale_market_calls.append(conditions)
        if self.whale_market_error is not None:
            raise self.whale_market_error
        wanted = set(conditions)
        return [market for market in self.whale_markets if market.condition_id in wanted]

    async def fetch_public_profile(self, address: str) -> WhalePublicProfile | None:
        normalized_address = address.strip().lower()
        self.public_profile_calls.append(normalized_address)
        if self.public_profile_error is not None:
            raise self.public_profile_error
        return self.public_profiles.get(normalized_address)

    async def fetch_tags(self, *, limit: int = 200) -> list[OfficialTag]:
        self.tag_calls.append(limit)
        if self.tag_error is not None:
            raise self.tag_error
        return list(self.official_tags[:limit])

    async def fetch_redeemable_positions(
        self,
        user: str,
        *,
        condition_ids: Any = None,
    ) -> list[PositionSnapshot]:
        conditions = list(condition_ids or [])
        self.redeemable_position_calls.append((user, conditions))
        return [
            position
            for position in self.redeemable_positions
            if not conditions or position.condition_id in conditions
        ]

    async def fetch_settlement_evidence(
        self,
        user: str,
        *,
        condition_ids: Any,
        last_seen_by_condition: dict[str, datetime],
    ) -> SettlementEvidence:
        conditions = list(condition_ids)
        self.settlement_calls.append(conditions)
        return self.evidence

    async def fetch_trades(
        self,
        user: str,
        *,
        condition_ids: Any = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[TradeSnapshot]:
        if self.trade_error is not None:
            raise self.trade_error
        conditions = set(condition_ids or [])
        return [
            trade
            for trade in self.trades
            if (not conditions or trade.condition_id in conditions)
            and (start is None or trade.timestamp >= start)
            and (end is None or trade.timestamp <= end)
        ]

    async def fetch_market_end_date(
        self,
        *,
        market_slug: str | None,
        condition_id: str,
    ) -> datetime | None:
        return self.market_end_dates.get(market_slug or condition_id)

    async def fetch_market_resolutions(
        self,
        condition_ids: Any,
    ) -> dict[str, MarketResolution]:
        conditions = list(condition_ids)
        self.market_resolution_calls.append(conditions)
        if self.market_resolution_error is not None:
            raise self.market_resolution_error
        return {
            condition_id: self.market_resolutions[condition_id]
            for condition_id in conditions
            if condition_id in self.market_resolutions
        }

    async def fetch_redemptions(
        self,
        user: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[RedemptionSnapshot]:
        if self.redemption_error is not None:
            raise self.redemption_error
        return [
            redemption
            for redemption in self.redemptions
            if (start is None or redemption.timestamp >= start)
            and (end is None or redemption.timestamp <= end)
        ]

    async def fetch_closed_positions(self, user: str) -> list[ClosedPositionSnapshot]:
        if self.closed_positions is None:
            raise PolymarketAPIError("官方已结仓数据未配置")
        return list(self.closed_positions)


@pytest.fixture
def settings_factory(tmp_path: Path):
    def factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "database_url": f"sqlite+aiosqlite:///{tmp_path / 'monitor.db'}",
            "start_monitor": False,
            "quiet_window_seconds": 0.0,
            "hard_window_seconds": 180.0,
            "poll_interval_seconds": 15.0,
        }
        values.update(overrides)
        return Settings(**values)

    return factory


@pytest.fixture
def app_client_factory(settings_factory):
    clients: list[TestClient] = []

    def factory(
        snapshots: list[list[PositionSnapshot] | Exception],
        **settings_overrides: Any,
    ) -> tuple[TestClient, FakePolymarketClient]:
        fake = FakePolymarketClient(snapshots)
        app = create_app(
            settings=settings_factory(**settings_overrides),
            client=fake,  # type: ignore[arg-type]
        )
        test_client = TestClient(app)
        test_client.__enter__()
        clients.append(test_client)
        return test_client, fake

    yield factory
    for client in reversed(clients):
        client.__exit__(None, None, None)
