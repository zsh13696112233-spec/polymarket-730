from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from backend.models import WalletTrade
from backend.purchase_history import active_position_cycle, opened_date


def trade(
    trade_id: int,
    side: str,
    size: str,
    timestamp: datetime,
) -> WalletTrade:
    return WalletTrade(
        id=trade_id,
        wallet_id=1,
        fingerprint=f"trade-{trade_id}",
        asset_id="asset",
        condition_id="condition",
        side=side,
        size=Decimal(size),
        price=Decimal("0.5"),
        amount=Decimal(size) * Decimal("0.5"),
        timestamp=timestamp,
        transaction_hash=f"0x{trade_id}",
        imported_at=timestamp,
    )


def current_position(size: str, first_seen_at: datetime) -> SimpleNamespace:
    return SimpleNamespace(size=Decimal(size), first_seen_at=first_seen_at)


def test_active_cycle_keeps_opening_across_add_and_partial_reduction():
    opened = datetime(2026, 8, 1, 1, 0)
    added = datetime(2026, 8, 2, 1, 0)
    reduced = datetime(2026, 8, 2, 2, 0)
    cycle = active_position_cycle(
        current_position("120", opened),
        [
            trade(1, "BUY", "100", opened),
            trade(2, "BUY", "50", added),
            trade(3, "SELL", "30", reduced),
        ],
    )

    assert cycle.opened_at == opened
    assert cycle.opened_at_source == "trade"
    assert cycle.complete is True
    assert [item.id for item in cycle.trades] == [1, 2, 3]


def test_active_cycle_starts_again_after_full_close():
    first_opened = datetime(2026, 8, 1, 1, 0)
    closed = datetime(2026, 8, 2, 1, 0)
    reopened = datetime(2026, 8, 3, 1, 0)
    cycle = active_position_cycle(
        current_position("20", reopened),
        [
            trade(1, "BUY", "100", first_opened),
            trade(2, "SELL", "100", closed),
            trade(3, "BUY", "20", reopened),
        ],
    )

    assert cycle.opened_at == reopened
    assert cycle.complete is True
    assert [item.id for item in cycle.trades] == [3]


def test_active_cycle_falls_back_to_first_seen_when_history_is_incomplete():
    first_seen = datetime(2026, 8, 1, 1, 0)
    later_buy = datetime(2026, 8, 2, 1, 0)
    cycle = active_position_cycle(
        current_position("10", first_seen),
        [trade(1, "BUY", "5", later_buy)],
    )

    assert cycle.opened_at == first_seen
    assert cycle.opened_at_source == "first_seen"
    assert cycle.complete is False
    assert [item.id for item in cycle.trades] == [1]


def test_opened_date_uses_beijing_timezone():
    assert opened_date(datetime(2026, 7, 31, 16, 0, tzinfo=UTC)).isoformat() == "2026-08-01"
