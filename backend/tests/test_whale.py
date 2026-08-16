from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from backend.models import WhaleMarket
from backend.polymarket import LargeTradeSnapshot
from backend.whale import (
    aggregate_whale_trades,
    estimated_market_fee,
    fingerprint_large_trades,
    market_is_eligible,
    settlement_profit_ratio,
)

BASE_TIME = datetime(2026, 8, 16, 0, 0)
WALLET_A = "0x" + "a" * 40
WALLET_B = "0x" + "b" * 40


def large_trade(
    *,
    asset_id: str,
    size: str,
    price: str,
    side: str = "BUY",
    wallet: str = WALLET_A,
    condition_id: str = "0x" + "1" * 64,
    outcome: str = "Yes",
    outcome_index: int = 0,
    minute: int = 0,
    transaction_hash: str | None = None,
) -> LargeTradeSnapshot:
    parsed_size = Decimal(size)
    parsed_price = Decimal(price)
    return LargeTradeSnapshot(
        proxy_wallet=wallet,
        asset_id=asset_id,
        condition_id=condition_id,
        side=side,
        size=parsed_size,
        price=parsed_price,
        amount=parsed_size * parsed_price,
        timestamp=BASE_TIME + timedelta(minutes=minute),
        title="Test market",
        outcome=outcome,
        outcome_index=outcome_index,
        market_slug="test-market",
        event_slug="test-event",
        icon_url=None,
        display_name="Test whale",
        transaction_hash=transaction_hash or f"0x{asset_id}-{side}-{minute}",
    )


def aggregate(
    trades: list[LargeTradeSnapshot],
    *,
    single: str = "10000",
    cumulative: str = "10000",
):
    return aggregate_whale_trades(
        trades,
        single_trade_threshold_usdc=Decimal(single),
        cumulative_threshold_usdc=Decimal(cumulative),
    )


def test_aggregate_accepts_single_or_cumulative_threshold_and_rejects_below_both():
    single_condition = "0x" + "1" * 64
    cumulative_condition = "0x" + "2" * 64
    below_condition = "0x" + "3" * 64
    trades = [
        large_trade(
            asset_id="single",
            size="20000",
            price="0.5",
            wallet=WALLET_A,
            condition_id=single_condition,
        ),
        large_trade(
            asset_id="cumulative",
            size="12000",
            price="0.5",
            wallet=WALLET_B,
            condition_id=cumulative_condition,
        ),
        large_trade(
            asset_id="cumulative",
            size="12000",
            price="0.5",
            wallet=WALLET_B,
            condition_id=cumulative_condition,
            minute=1,
        ),
        large_trade(
            asset_id="below",
            size="8000",
            price="0.5",
            wallet="0x" + "c" * 40,
            condition_id=below_condition,
        ),
        large_trade(
            asset_id="below",
            size="8000",
            price="0.5",
            wallet="0x" + "c" * 40,
            condition_id=below_condition,
            minute=1,
        ),
    ]

    by_asset = {item.asset_id: item for item in aggregate(trades)}

    assert set(by_asset) == {"single", "cumulative"}
    assert by_asset["single"].max_single_usdc == Decimal("10000.0")
    assert by_asset["single"].trade_count == 1
    assert by_asset["cumulative"].max_single_usdc == Decimal("6000.0")
    assert by_asset["cumulative"].gross_buy_usdc == Decimal("12000.0")
    assert by_asset["cumulative"].trade_count == 2


def test_aggregate_uses_share_weighted_average_price():
    trades = [
        large_trade(asset_id="weighted", size="100", price="0.2"),
        large_trade(asset_id="weighted", size="900", price="0.8", minute=1),
    ]

    result = aggregate(trades, single="1000", cumulative="700")

    assert len(result) == 1
    assert result[0].gross_buy_size == Decimal("1000")
    assert result[0].gross_buy_usdc == Decimal("740.0")
    assert result[0].avg_buy_price == Decimal("0.74")
    assert result[0].first_buy_at == BASE_TIME
    assert result[0].last_buy_at == BASE_TIME + timedelta(minutes=1)


def test_sell_activity_calculates_net_ratio_and_all_three_statuses():
    trades: list[LargeTradeSnapshot] = []
    cases = [
        ("holding", "10", Decimal("90"), "holding"),
        ("reduced", "50", Decimal("50"), "reduced"),
        ("exited", "80", Decimal("20"), "exited"),
    ]
    for index, (asset_id, sold_size, _ratio, _status) in enumerate(cases, start=1):
        condition_id = f"0x{index:064x}"
        trades.extend(
            [
                large_trade(
                    asset_id=asset_id,
                    size="100",
                    price="0.5",
                    condition_id=condition_id,
                ),
                large_trade(
                    asset_id=asset_id,
                    size=sold_size,
                    price="0.6",
                    side="SELL",
                    condition_id=condition_id,
                    minute=1,
                ),
            ]
        )

    by_asset = {item.asset_id: item for item in aggregate(trades, single="10", cumulative="10")}

    for asset_id, sold_size, ratio, status in cases:
        assert by_asset[asset_id].sold_size == Decimal(sold_size)
        assert by_asset[asset_id].net_ratio_percent == ratio
        assert by_asset[asset_id].status == status


def test_same_wallet_on_two_market_outcomes_is_hedged_only_for_that_wallet():
    condition_id = "0x" + "9" * 64
    trades = [
        large_trade(
            asset_id="yes-token",
            size="100",
            price="0.5",
            condition_id=condition_id,
            outcome="Yes",
            outcome_index=0,
        ),
        large_trade(
            asset_id="no-token",
            size="100",
            price="0.5",
            condition_id=condition_id,
            outcome="No",
            outcome_index=1,
            minute=1,
        ),
        large_trade(
            asset_id="yes-token",
            size="100",
            price="0.5",
            wallet=WALLET_B,
            condition_id=condition_id,
            outcome="Yes",
            outcome_index=0,
            minute=2,
        ),
    ]

    result = aggregate(trades, single="10", cumulative="10")
    by_wallet_asset = {(item.proxy_wallet, item.asset_id): item for item in result}

    assert by_wallet_asset[(WALLET_A, "yes-token")].hedged is True
    assert by_wallet_asset[(WALLET_A, "no-token")].hedged is True
    assert by_wallet_asset[(WALLET_B, "yes-token")].hedged is False


def test_large_trade_fingerprints_are_stable_and_preserve_identical_double_fill():
    first = large_trade(
        asset_id="same-token",
        size="100",
        price="0.5",
        transaction_hash="0xsame",
    )
    second = large_trade(
        asset_id="same-token",
        size="100",
        price="0.5",
        transaction_hash="0xsame",
    )

    first_round = fingerprint_large_trades([first, second])
    overlapping_round = fingerprint_large_trades([second, first])

    first_hashes = [fingerprint for fingerprint, _trade in first_round]
    assert first_hashes == [fingerprint for fingerprint, _trade in overlapping_round]
    assert len(first_hashes) == 2
    assert len(set(first_hashes)) == 2


def whale_market(
    *,
    now: datetime,
    closed: bool = False,
    active: bool = True,
    accepting_orders: bool = True,
    liquidity: str = "5000",
    end_date: datetime | None = None,
    end_date_is_date_only: bool = False,
) -> WhaleMarket:
    return WhaleMarket(
        condition_id="0x" + "1" * 64,
        title="Eligible market",
        closed=closed,
        active=active,
        accepting_orders=accepting_orders,
        liquidity=Decimal(liquidity),
        end_date=end_date,
        end_date_is_date_only=end_date_is_date_only,
        refreshed_at=now,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"closed": True},
        {"active": False},
        {"accepting_orders": False},
        {"liquidity": "4999.99"},
    ],
)
def test_market_eligibility_rejects_closed_inactive_non_trading_or_illiquid(overrides):
    now = BASE_TIME
    market = whale_market(now=now, **overrides)

    assert not market_is_eligible(
        market,
        now=now,
        min_liquidity_usdc=Decimal("5000"),
        min_remaining_minutes=30,
    )


def test_market_eligibility_handles_remaining_time_and_date_only_end_date():
    now = BASE_TIME
    eligible = whale_market(now=now, end_date=now + timedelta(minutes=31))
    ending_at_cutoff = whale_market(now=now, end_date=now + timedelta(minutes=30))
    date_only = whale_market(
        now=now,
        end_date=now - timedelta(days=1),
        end_date_is_date_only=True,
    )

    def is_eligible(market: WhaleMarket) -> bool:
        return market_is_eligible(
            market,
            now=now,
            min_liquidity_usdc=Decimal("5000"),
            min_remaining_minutes=30,
        )

    assert is_eligible(eligible)
    assert not is_eligible(ending_at_cutoff)
    assert is_eligible(date_only)


def test_estimated_market_fee_and_settlement_profit_ratio_match_formula():
    fee = estimated_market_fee(
        Decimal("100"),
        Decimal("0.5"),
        Decimal("0.05"),
        Decimal("1"),
    )
    ratio = settlement_profit_ratio(
        Decimal("100"),
        Decimal("0.5"),
        Decimal("0.05"),
        Decimal("1"),
    )

    assert fee == Decimal("1.2500")
    assert ratio.quantize(Decimal("0.000001")) == Decimal("95.121951")
    assert settlement_profit_ratio(
        Decimal("100"), Decimal("0.5"), Decimal("0"), Decimal("1")
    ) == Decimal("100")
    assert estimated_market_fee(
        Decimal("100"), Decimal("0"), Decimal("0.05"), Decimal("1")
    ) == Decimal("0")
