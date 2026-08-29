from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from backend.db import Database
from backend.models import (
    ExecutionAccount,
    WhaleAutoFollowDecision,
    WhaleEntry,
    WhaleEntryRuleState,
    WhaleExclusion,
    WhaleFollowLedger,
    WhaleFollowPosition,
    WhaleMarket,
    WhaleOrder,
    WhaleSettings,
)
from backend.polymarket import PolymarketClient
from backend.time_utils import utcnow
from backend.whale import _auto_follow_reason_display, _json_list, _position_marks

BEIJING = ZoneInfo("Asia/Shanghai")
ZERO = Decimal("0")
HUNDRED = Decimal("100")
OPEN_POSITION_STATUSES = {"opening", "open", "closing", "redeeming"}
FINISHED_POSITION_STATUSES = {"closed", "redeemed", "resolved_loss"}
EXIT_LEDGER_TYPES = {"sell", "redeem", "resolved_loss", "dust_writeoff"}
RULES = ("new_account", "large_amount")
HOME_BALANCE_STALE_AFTER = timedelta(minutes=5)


def _beijing_day_start_utc(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=BEIJING).astimezone(UTC).replace(tzinfo=None)


def _beijing_date(value: datetime) -> date:
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.astimezone(BEIJING).date()


def _exit_cost(row: WhaleFollowLedger) -> Decimal:
    if row.type not in EXIT_LEDGER_TYPES and row.realized_pnl == ZERO:
        return ZERO
    # For every realized ledger entry: pnl = net proceeds - released cost basis.
    # This also yields the right basis for resolved losses and reconciliation write-offs.
    return max(ZERO, row.amount_usdc - row.realized_pnl)


def _empty_day(day: date) -> dict[str, Any]:
    return {
        "date": day,
        "buy_amount_usdc": ZERO,
        "buy_count": 0,
        "realized_pnl_usdc": ZERO,
        "realized_cost_usdc": ZERO,
        "realized_roi_percent": None,
        "win_count": 0,
        "loss_count": 0,
        "flat_count": 0,
    }


def _finish_counts(positions: list[WhaleFollowPosition]) -> dict[date, tuple[int, int, int]]:
    result: dict[date, list[int]] = defaultdict(lambda: [0, 0, 0])
    for position in positions:
        if position.status not in FINISHED_POSITION_STATUSES or position.closed_at is None:
            continue
        counts = result[_beijing_date(position.closed_at)]
        if position.realized_pnl > ZERO:
            counts[0] += 1
        elif position.realized_pnl < ZERO:
            counts[1] += 1
        else:
            counts[2] += 1
    return {day: (values[0], values[1], values[2]) for day, values in result.items()}


async def home_overview(
    database: Database,
    client: PolymarketClient,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = now or utcnow()
    today_date = _beijing_date(generated_at)
    first_date = today_date - timedelta(days=29)
    range_start = _beijing_day_start_utc(first_date)
    range_end = _beijing_day_start_utc(today_date + timedelta(days=1))

    async with database.sessions() as session:
        settings = await session.get(WhaleSettings, 1)
        account = await session.get(ExecutionAccount, 1)
        ledger = list(
            (
                await session.scalars(
                    select(WhaleFollowLedger)
                    .where(
                        WhaleFollowLedger.timestamp >= range_start,
                        WhaleFollowLedger.timestamp < range_end,
                    )
                    .order_by(WhaleFollowLedger.timestamp, WhaleFollowLedger.id)
                )
            ).all()
        )
        positions = list((await session.scalars(select(WhaleFollowPosition))).all())
        decisions = list(
            (
                await session.scalars(
                    select(WhaleAutoFollowDecision)
                    .order_by(
                        WhaleAutoFollowDecision.created_at.desc(),
                        WhaleAutoFollowDecision.id.desc(),
                    )
                    .limit(8)
                )
            ).all()
        )
        condition_ids = list(dict.fromkeys(item.condition_id for item in decisions))
        markets = (
            {
                item.condition_id: item
                for item in (
                    await session.scalars(
                        select(WhaleMarket).where(WhaleMarket.condition_id.in_(condition_ids))
                    )
                ).all()
            }
            if condition_ids
            else {}
        )
        order_ids = list(
            dict.fromkeys(
                order_id
                for item in decisions
                for order_id in (item.buy_order_id, item.latest_sell_order_id)
                if order_id is not None
            )
        )
        orders = (
            {
                item.id: item
                for item in (
                    await session.scalars(select(WhaleOrder).where(WhaleOrder.id.in_(order_ids)))
                ).all()
            }
            if order_ids
            else {}
        )
        rule_wallet_counts = {
            rule: int(
                await session.scalar(
                    select(func.count(func.distinct(WhaleEntry.proxy_wallet)))
                    .join(WhaleEntryRuleState, WhaleEntryRuleState.entry_id == WhaleEntry.id)
                    .outerjoin(
                        WhaleExclusion,
                        func.lower(WhaleExclusion.proxy_wallet)
                        == func.lower(WhaleEntry.proxy_wallet),
                    )
                    .where(
                        WhaleEntryRuleState.rule_type == rule,
                        WhaleEntryRuleState.active.is_(True),
                        WhaleExclusion.proxy_wallet.is_(None),
                    )
                )
                or 0
            )
            for rule in RULES
        }

    daily_by_date = {
        first_date + timedelta(days=index): _empty_day(first_date + timedelta(days=index))
        for index in range(30)
    }
    for row in ledger:
        day = _beijing_date(row.timestamp)
        bucket = daily_by_date.get(day)
        if bucket is None:
            continue
        if row.type == "buy":
            bucket["buy_amount_usdc"] += row.amount_usdc
            bucket["buy_count"] += 1
        if row.realized_pnl != ZERO or row.type in EXIT_LEDGER_TYPES:
            bucket["realized_pnl_usdc"] += row.realized_pnl
            bucket["realized_cost_usdc"] += _exit_cost(row)

    finished_counts = _finish_counts(positions)
    for day, bucket in daily_by_date.items():
        wins, losses, flats = finished_counts.get(day, (0, 0, 0))
        bucket.update(win_count=wins, loss_count=losses, flat_count=flats)
        cost = bucket["realized_cost_usdc"]
        bucket["realized_roi_percent"] = (
            bucket["realized_pnl_usdc"] / cost * HUNDRED if cost > ZERO else None
        )

    marks = await _position_marks(client, positions)
    open_positions = [
        item for item in positions if item.size > ZERO and item.status in OPEN_POSITION_STATUSES
    ]
    open_cost = sum((item.cost_usdc for item in open_positions), ZERO)
    unpriced_count = sum(1 for item in open_positions if marks[item.id][1] != "ok")
    valuation_complete = unpriced_count == 0
    market_value = (
        sum((item.size * marks[item.id][0] for item in open_positions), ZERO)
        if valuation_complete
        else None
    )
    unrealized = market_value - open_cost if market_value is not None else None

    cash_balance = account.collateral_balance if account is not None else None
    cash_reserve = account.cash_reserve_usdc if account is not None else ZERO
    balance_stale = (
        account is None
        or account.last_balance_at is None
        or generated_at - account.last_balance_at > HOME_BALANCE_STALE_AFTER
    )
    wallet_available = bool(
        account is not None
        and account.signer_address
        and account.funder_address
        and account.status not in {"unconfigured", "error"}
    )
    total_assets = (
        cash_balance + market_value
        if cash_balance is not None and market_value is not None and valuation_complete
        else None
    )

    today = daily_by_date[today_date]
    win_denominator = today["win_count"] + today["loss_count"]
    today_payload = dict(today)
    today_payload["win_rate_percent"] = (
        Decimal(today["win_count"]) / Decimal(win_denominator) * HUNDRED
        if win_denominator
        else None
    )
    today_payload["unrealized_pnl_usdc"] = unrealized

    recent_decisions = []
    for decision in decisions:
        market = markets.get(decision.condition_id)
        buy_order = orders.get(decision.buy_order_id)
        sell_order = orders.get(decision.latest_sell_order_id)
        recent_decisions.append(
            {
                "id": decision.id,
                "created_at": decision.created_at,
                "selected_rule": decision.selected_rule,
                "matched_rules": [str(value) for value in _json_list(decision.matched_rules_json)],
                "title": market.title if market is not None else "未命名市场",
                "outcome": decision.outcome,
                "market_slug": market.market_slug if market is not None else None,
                "event_slug": market.event_slug if market is not None else None,
                "configured_amount_usdc": decision.configured_amount_usdc,
                "filled_usdc": buy_order.filled_usdc + buy_order.fee_usdc if buy_order else ZERO,
                "status": decision.status,
                "reason": _auto_follow_reason_display(decision.reason),
                "is_risk_exit": bool(
                    sell_order is not None and sell_order.source == "conflict_exit"
                )
                or decision.status.startswith("exit_"),
            }
        )

    scan_failed = settings is None or bool(
        settings.last_scan_error or settings.consecutive_failures
    )
    system_status = (
        "error" if scan_failed else "disabled" if settings and not settings.enabled else "healthy"
    )
    return {
        "as_of": generated_at,
        "timezone": "Asia/Shanghai",
        "range_start": first_date,
        "range_end": today_date,
        "system": {
            "status": system_status,
            "enabled": settings.enabled if settings is not None else False,
            "last_scan_at": settings.last_scan_at if settings is not None else None,
            "last_scan_error": (
                settings.last_scan_error if settings is not None else "巨鲸模块尚未初始化"
            ),
            "consecutive_failures": settings.consecutive_failures if settings is not None else 0,
            "scan_interval_seconds": settings.scan_interval_seconds if settings is not None else 0,
            "rules": [
                {
                    "rule": rule,
                    "enabled": bool(settings and settings.enabled),
                    "auto_follow_enabled": bool(
                        settings and getattr(settings, f"{rule}_auto_follow_enabled")
                    ),
                    "active_wallet_count": rule_wallet_counts[rule],
                }
                for rule in RULES
            ],
        },
        "today": today_payload,
        "wallet": {
            "status": account.status if account is not None else "unconfigured",
            "available": wallet_available,
            "cash_balance_usdc": cash_balance,
            "open_cost_usdc": open_cost,
            "market_value_usdc": market_value,
            "total_assets_usdc": total_assets,
            "unrealized_pnl_usdc": unrealized,
            "cash_reserve_usdc": cash_reserve,
            "available_cash_usdc": (
                max(ZERO, cash_balance - cash_reserve) if cash_balance is not None else None
            ),
            "open_position_count": len(open_positions),
            "last_balance_at": account.last_balance_at if account is not None else None,
            "balance_stale": balance_stale,
            "valuation_complete": valuation_complete,
            "unpriced_position_count": unpriced_count,
            "last_error": account.last_error if account is not None else None,
        },
        "daily": list(daily_by_date.values()),
        "recent_auto_decisions": recent_decisions,
    }
