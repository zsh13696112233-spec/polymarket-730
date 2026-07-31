from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, date
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
