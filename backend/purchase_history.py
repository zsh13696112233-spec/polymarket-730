from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from backend.models import CurrentPosition, WalletTrade

ZERO = Decimal("0")
SIZE_TOLERANCE = Decimal("0.000000001")
PURCHASE_TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class PurchaseLot:
    purchase_date: date
    size: Decimal
    avg_price: Decimal
    initial_value: Decimal
    current_value: Decimal
    cash_pnl: Decimal
    percent_pnl: Decimal


@dataclass(slots=True)
class _OpenLot:
    purchase_date: date
    size: Decimal
    price: Decimal


def fifo_cost_basis(
    trades: list[WalletTrade],
    size: Decimal,
    *,
    through: datetime | None = None,
) -> Decimal | None:
    """Return the FIFO cost of the next ``size`` shares, or None if history is incomplete."""
    timestamp = through or datetime.max
    return redemption_cost_bases(trades, [(0, timestamp, size)])[0]


def redemption_cost_bases(
    trades: list[WalletTrade],
    redemptions: list[tuple[int, datetime, Decimal]],
) -> dict[int, Decimal | None]:
    """Calculate FIFO cost for chronologically interleaved redemption events."""
    actions: list[tuple[datetime, int, int, WalletTrade | Decimal]] = [
        (trade.timestamp, 0, trade.id, trade) for trade in trades
    ]
    actions.extend((timestamp, 1, event_id, size) for event_id, timestamp, size in redemptions)
    actions.sort(key=lambda item: (item[0], item[1], item[2]))

    open_lots: deque[_OpenLot] = deque()
    complete = True
    results: dict[int, Decimal | None] = {}
    for _, action_type, action_id, payload in actions:
        if action_type == 0:
            trade = payload
            assert isinstance(trade, WalletTrade)
            if trade.size <= ZERO or trade.side not in {"BUY", "SELL"}:
                continue
            if trade.side == "BUY":
                open_lots.append(
                    _OpenLot(
                        purchase_date=_purchase_date(trade),
                        size=trade.size,
                        price=trade.price,
                    )
                )
                continue
            remaining_size, _ = _consume_lots(open_lots, trade.size)
            if remaining_size > SIZE_TOLERANCE:
                complete = False
            continue

        size = payload
        assert isinstance(size, Decimal)
        if size <= ZERO:
            results[action_id] = ZERO
            continue
        remaining_size, cost_basis = _consume_lots(open_lots, size)
        results[action_id] = cost_basis if complete and remaining_size <= SIZE_TOLERANCE else None
        if remaining_size > SIZE_TOLERANCE:
            complete = False

    return results


def _consume_lots(
    open_lots: deque[_OpenLot],
    size: Decimal,
) -> tuple[Decimal, Decimal]:
    remaining_size = size
    cost_basis = ZERO
    while remaining_size > SIZE_TOLERANCE and open_lots:
        lot = open_lots[0]
        consumed = min(lot.size, remaining_size)
        cost_basis += consumed * lot.price
        lot.size -= consumed
        remaining_size -= consumed
        if lot.size <= SIZE_TOLERANCE:
            open_lots.popleft()
    return remaining_size, cost_basis


def _purchase_date(trade: WalletTrade) -> date:
    timestamp = trade.timestamp
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(PURCHASE_TIMEZONE).date()


def build_purchase_lots(
    positions: list[CurrentPosition],
    trades: list[WalletTrade],
) -> tuple[dict[str, list[PurchaseLot]], bool]:
    trades_by_asset: dict[str, list[WalletTrade]] = defaultdict(list)
    for trade in trades:
        if trade.size > ZERO and trade.side in {"BUY", "SELL"}:
            trades_by_asset[trade.asset_id].append(trade)

    results: dict[str, list[PurchaseLot]] = {}
    complete = True
    for position in positions:
        open_lots: deque[_OpenLot] = deque()
        for trade in sorted(
            trades_by_asset.get(position.asset_id, []),
            key=lambda item: (item.timestamp, item.id),
        ):
            if trade.side == "BUY":
                open_lots.append(
                    _OpenLot(
                        purchase_date=_purchase_date(trade),
                        size=trade.size,
                        price=trade.price,
                    )
                )
                continue

            remaining_sell = trade.size
            while remaining_sell > SIZE_TOLERANCE and open_lots:
                lot = open_lots[0]
                consumed = min(lot.size, remaining_sell)
                lot.size -= consumed
                remaining_sell -= consumed
                if lot.size <= SIZE_TOLERANCE:
                    open_lots.popleft()

        tracked_size = sum((lot.size for lot in open_lots), start=ZERO)
        if tracked_size > position.size + SIZE_TOLERANCE:
            excess = tracked_size - position.size
            while excess > SIZE_TOLERANCE and open_lots:
                lot = open_lots[0]
                consumed = min(lot.size, excess)
                lot.size -= consumed
                excess -= consumed
                if lot.size <= SIZE_TOLERANCE:
                    open_lots.popleft()
            tracked_size = sum((lot.size for lot in open_lots), start=ZERO)

        if abs(tracked_size - position.size) > SIZE_TOLERANCE:
            complete = False

        daily_size: dict[date, Decimal] = defaultdict(lambda: ZERO)
        daily_cost: dict[date, Decimal] = defaultdict(lambda: ZERO)
        for lot in open_lots:
            if lot.size <= SIZE_TOLERANCE:
                continue
            daily_size[lot.purchase_date] += lot.size
            daily_cost[lot.purchase_date] += lot.size * lot.price

        asset_lots: list[PurchaseLot] = []
        for purchase_day in sorted(daily_size, reverse=True):
            size = daily_size[purchase_day]
            initial_value = daily_cost[purchase_day]
            current_value = size * position.current_price
            cash_pnl = current_value - initial_value
            asset_lots.append(
                PurchaseLot(
                    purchase_date=purchase_day,
                    size=size,
                    avg_price=initial_value / size if size else ZERO,
                    initial_value=initial_value,
                    current_value=current_value,
                    cash_pnl=cash_pnl,
                    percent_pnl=(
                        cash_pnl / initial_value * Decimal("100") if initial_value else ZERO
                    ),
                )
            )
        results[position.asset_id] = asset_lots

    return results, complete
