"""Automatic-follow cohort reports. Reporting never executes or reconciles trades."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.config import Settings
from backend.db import Database
from backend.models import (
    ExecutionAccount,
    WhaleAutoFollowDecision,
    WhaleEmailDelivery,
    WhaleEntry,
    WhaleFollowLedger,
    WhaleFollowPosition,
    WhaleOrder,
)
from backend.polymarket import PolymarketClient
from backend.time_utils import utcnow

BEIJING = ZoneInfo("Asia/Shanghai")
ZERO = Decimal("0")
TOLERANCE = Decimal("0.0000001")
KIND = "weekly_auto_follow_report"
LABELS = {"new_account": "新号大额", "large_amount": "全量超大额", "unknown": "无法归属"}
EXITS = {"sell", "redeem", "resolved_loss", "dust_writeoff"}


def report_period(now: datetime) -> tuple[datetime, datetime]:
    """Latest due Monday 06:00 report, including catch-up before the next deadline."""
    local = (
        now.replace(tzinfo=UTC).astimezone(BEIJING)
        if now.tzinfo is None
        else now.astimezone(BEIJING)
    )
    monday = datetime.combine(local.date() - timedelta(days=local.weekday()), time.min, BEIJING)
    if local < monday + timedelta(hours=6):
        monday -= timedelta(days=7)
    end = monday.astimezone(UTC).replace(tzinfo=None)
    return end - timedelta(days=7), end


@dataclass
class OrderPerformance:
    fee: Decimal = ZERO
    size: Decimal = ZERO
    cost: Decimal = ZERO
    realized: Decimal = ZERO


def replay_position(
    rows: list[WhaleFollowLedger], position: WhaleFollowPosition
) -> tuple[dict[int | tuple[str, int], OrderPerformance], bool]:
    """Allocate exit proceeds by remaining shares, releasing each lot's own cost."""
    lots: dict[int | tuple[str, int], OrderPerformance] = {}
    valid = True
    for row in sorted(rows, key=lambda item: (item.timestamp, item.id)):
        if row.type == "buy" and row.size > ZERO:
            key = row.order_id if row.order_id is not None else ("ledger", row.id)
            lot = lots.setdefault(key, OrderPerformance())
            lot.fee += row.fee_usdc
            lot.cost += row.amount_usdc
            lot.size += row.size
            valid &= row.amount_usdc >= row.fee_usdc >= ZERO
        elif row.type in EXITS:
            active = [lot for lot in lots.values() if lot.size > ZERO]
            total = sum((lot.size for lot in active), ZERO)
            if total <= ZERO or row.size <= ZERO or row.size > total + TOLERANCE:
                valid = False
                continue
            ratio = min(row.size / total, Decimal("1"))
            released = sum((lot.cost * ratio for lot in active), ZERO)
            valid &= abs(row.amount_usdc - released - row.realized_pnl) <= TOLERANCE
            proceeds_left, fee_left = row.amount_usdc, row.fee_usdc
            for index, lot in enumerate(active):
                share = lot.size / total
                proceeds = row.amount_usdc * share if index < len(active) - 1 else proceeds_left
                fee = row.fee_usdc * share if index < len(active) - 1 else fee_left
                cost = lot.cost * ratio
                lot.realized += proceeds - cost
                lot.fee += fee
                lot.cost -= cost
                lot.size *= 1 - ratio
                proceeds_left -= proceeds
                fee_left -= fee
        elif row.size != ZERO or row.amount_usdc != ZERO or row.realized_pnl != ZERO:
            valid = False
    valid &= abs(sum((lot.size for lot in lots.values()), ZERO) - position.size) <= TOLERANCE
    valid &= abs(sum((lot.cost for lot in lots.values()), ZERO) - position.cost_usdc) <= TOLERANCE
    valid &= (
        abs(sum((lot.realized for lot in lots.values()), ZERO) - position.realized_pnl) <= TOLERANCE
    )
    return lots, valid


def money(value: Decimal | None) -> str:
    return f"{value:,.2f} USDC" if value is not None else "待核对 / 数据不完整"


def local_time(value: datetime | None) -> str:
    return (
        value.replace(tzinfo=UTC).astimezone(BEIJING).strftime("%Y-%m-%d %H:%M:%S")
        if value
        else "未知"
    )


def settlement_result(entry: WhaleEntry | None, now: datetime) -> str:
    if (
        entry is None
        or entry.settled_at is None
        or entry.settled_at > now
        or entry.settlement_price is None
    ):
        return "待结算"
    if entry.settlement_price == 1:
        return "命中"
    if entry.settlement_price == 0:
        return "未命中"
    return "特殊结算"


async def build_report(
    database: Database,
    client: PolymarketClient,
    *,
    period_start: datetime,
    period_end: datetime,
    now: datetime,
    refresh_balance: Callable[[], Awaitable[Decimal]] | None = None,
) -> tuple[str, str]:
    # Import here to keep the scanner/email module dependency one-way at import time.
    from backend.whale import _position_marks

    balance_failed = False
    if refresh_balance is not None:
        try:
            await refresh_balance()
        except Exception:
            balance_failed = True
    async with database.sessions() as session:
        account = await session.get(ExecutionAccount, 1)
        positions = list(await session.scalars(select(WhaleFollowPosition)))
        orders = list(
            await session.scalars(
                select(WhaleOrder).where(
                    WhaleOrder.source == "auto_follow", WhaleOrder.side == "BUY"
                )
            )
        )
        decisions = list(
            await session.scalars(
                select(WhaleAutoFollowDecision).where(
                    WhaleAutoFollowDecision.buy_order_id.is_not(None)
                )
            )
        )
        entry_ids = {order.entry_id for order in orders if order.entry_id is not None}
        entries = {
            entry.id: entry
            for entry in await session.scalars(
                select(WhaleEntry).where(WhaleEntry.id.in_(entry_ids))
            )
        }
        ledger = list(
            await session.scalars(
                select(WhaleFollowLedger)
                .where(WhaleFollowLedger.timestamp <= now)
                .order_by(WhaleFollowLedger.timestamp, WhaleFollowLedger.id)
            )
        )
    first_fills: dict[int, datetime] = {}
    by_position: dict[int, list[WhaleFollowLedger]] = defaultdict(list)
    for row in ledger:
        by_position[row.position_id].append(row)
        if row.type == "buy" and row.size > ZERO and row.order_id is not None:
            first_fills.setdefault(row.order_id, row.timestamp)
    selected = [
        order
        for order in orders
        if order.id in first_fills and period_start <= first_fills[order.id] < period_end
    ]
    selected.sort(key=lambda order: (first_fills[order.id], order.id))
    rules: dict[int, set[str | None]] = defaultdict(set)
    for decision in decisions:
        rules[decision.buy_order_id].add(decision.selected_rule)
    positions_by_id = {position.id: position for position in positions}
    replayed = {
        pid: replay_position(by_position[pid], positions_by_id[pid])
        for pid in {order.position_id for order in selected}
        if pid in positions_by_id
    }
    marks = await _position_marks(client, positions)
    groups: dict[str, list[dict]] = defaultdict(list)
    for order in selected:
        selected_rules = rules[order.id]
        rule = next(iter(selected_rules)) if len(selected_rules) == 1 else "unknown"
        if rule not in LABELS:
            rule = "unknown"
        lots, valid = replayed.get(order.position_id, ({}, False))
        lot = lots.get(order.id)
        valid = valid and lot is not None
        # Buy totals remain usable even when later reconciliation is incomplete.
        buys = [
            row
            for row in by_position[order.position_id]
            if row.type == "buy" and row.order_id == order.id
        ]
        invested = sum((row.amount_usdc for row in buys), ZERO)
        buy_fee = sum((row.fee_usdc for row in buys), ZERO)
        price, _ = marks.get(order.position_id, (None, "unavailable"))
        value = (
            (lot.size * price if price is not None else (ZERO if lot.size == ZERO else None))
            if valid
            else None
        )
        unrealized = value - lot.cost if value is not None else None
        result = settlement_result(entries.get(order.entry_id), now)
        trading = "待核对"
        if valid:
            trading = (
                "仍有持仓"
                if lot.size > TOLERANCE
                else (
                    "盈利"
                    if lot.realized > TOLERANCE
                    else "亏损"
                    if lot.realized < -TOLERANCE
                    else "持平"
                )
            )
        groups[rule].append(
            dict(
                order=order,
                lot=lot,
                valid=valid,
                invested=invested,
                buy_fee=buy_fee,
                value=value,
                unrealized=unrealized,
                result=result,
                trading=trading,
            )
        )

    period_label = f"{local_time(period_start)[:10]} 至 "
    period_label += local_time(period_end - timedelta(days=1))[:10]
    title = f"自动跟单周报｜{period_label}"
    lines = [
        f"PolyCopy {title}",
        f"截至：{local_time(now)}（北京时间）",
        "",
    ]

    all_items = [item for items in groups.values() for item in items]
    for label, items in [
        (LABELS[rule], groups[rule]) for rule in LABELS if rule != "unknown" or groups[rule]
    ] + [("合计", all_items)]:
        counts = {
            name: sum(item["result"] == name for item in items)
            for name in ("命中", "未命中", "待结算", "特殊结算")
        }
        effective = counts["命中"] + counts["未命中"]
        rate = f"{Decimal(counts['命中']) / effective * 100:.2f}%" if effective else "暂无有效样本"
        valid = all(item["valid"] for item in items)
        valued = valid and all(item["unrealized"] is not None for item in items)
        realized = sum((item["lot"].realized for item in items), ZERO) if valid else None
        unrealized = sum((item["unrealized"] for item in items), ZERO) if valued else None
        fees = sum((item["lot"].fee for item in items), ZERO) if valid else None
        total_pnl = realized + unrealized if valued else None
        holding = sum(item["trading"] == "仍有持仓" for item in items)
        pending = sum(item["trading"] == "待核对" for item in items)
        lines += [
            f"【{label}】",
            f"成交：{len(items)} 单；"
            f"含费投入：{money(sum((item['invested'] for item in items), ZERO))}",
            f"结算命中率：{rate}（{counts['命中']} 中 / {counts['未命中']} 未中）；"
            f"待结算：{counts['待结算']}；仍有持仓：{holding}",
            f"已实现盈亏：{money(realized)}；"
            f"浮动盈亏：{money(unrealized)}；"
            f"合计盈亏：{money(total_pnl)}",
            f"累计交易手续费：{money(fees)}",
        ]
        if counts["特殊结算"]:
            lines.append(f"特殊结算：{counts['特殊结算']}")
        if pending:
            lines.append(f"待核对：{pending}")
        lines.append("")

    open_positions = [position for position in positions if position.size > ZERO]
    complete = all(marks[p.id][0] is not None for p in open_positions)
    value = sum((p.size * marks[p.id][0] for p in open_positions), ZERO) if complete else None
    cash = account.collateral_balance if account else None
    balance_at = account.last_balance_at if account else None
    stale = balance_at is None or now - balance_at > timedelta(minutes=5)
    assets = cash + value if cash is not None and value is not None else None
    lines += [
        "【账户概况】",
        f"现金余额：{money(cash)}"
        + ("（刷新失败，使用缓存）" if balance_failed else "")
        + ("（数据过期或缺失）" if stale else ""),
        f"已纳管持仓市值：{money(value)}；持仓数量：{len(open_positions)}",
        f"现金＋已纳管持仓：{money(assets)}" + ("（余额过期，仅供参考）" if stale else ""),
    ]
    if not selected:
        lines.append("上周无自动买入成交。")
    return f"[PolyCopy] {title}", "\n".join(lines)


async def enqueue_weekly_report(
    database: Database,
    settings: Settings,
    client: PolymarketClient,
    *,
    now: datetime | None = None,
    refresh_balance: Callable[[], Awaitable[Decimal]] | None = None,
) -> int:
    if not settings.weekly_report_to:
        return 0
    current = now or utcnow()
    if current.tzinfo is not None:
        current = current.astimezone(UTC).replace(tzinfo=None)
    start, end = report_period(current)
    key = f"weekly-auto-follow:{local_time(start)[:10]}"
    async with database.sessions() as session:
        existing = list(
            await session.scalars(
                select(WhaleEmailDelivery).where(WhaleEmailDelivery.dedupe_key == key)
            )
        )
    missing = sorted(set(settings.weekly_report_to) - {row.recipient_email for row in existing})
    if not missing:
        return 0
    if existing:
        subject, body = existing[0].subject, existing[0].body_text
    else:
        subject, body = await build_report(
            database,
            client,
            period_start=start,
            period_end=end,
            now=current,
            refresh_balance=refresh_balance,
        )
    count = 0
    for recipient in missing:
        async with database.sessions() as session:
            session.add(
                WhaleEmailDelivery(
                    notification_kind=KIND,
                    condition_id="weekly-auto-follow",
                    dedupe_key=key,
                    rule_key=KIND,
                    rules_json="[]",
                    recipient_email=recipient,
                    market_title=subject.removeprefix("[PolyCopy] "),
                    wallet_label="上周自动买入 · 结算命中 · 交易盈亏 · 账户概况",
                    subject=subject,
                    body_text=body,
                    status="pending",
                    attempt_count=0,
                    next_attempt_at=current,
                    created_at=current,
                )
            )
            try:
                await session.commit()
                count += 1
            except IntegrityError:
                await session.rollback()
                # Only tolerate the expected concurrent enqueue, not unrelated corruption.
                duplicate = await session.scalar(
                    select(WhaleEmailDelivery.id).where(
                        WhaleEmailDelivery.dedupe_key == key,
                        WhaleEmailDelivery.recipient_email == recipient,
                    )
                )
                if duplicate is None:
                    raise
    return count
