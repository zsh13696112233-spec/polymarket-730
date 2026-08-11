from __future__ import annotations

import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import selectinload

from backend.broker import EventBroker
from backend.config import Settings
from backend.copy_cli import DEFAULT_SERVICE
from backend.copy_trading import CopyTradingEngine, latest_event_id
from backend.db import Database
from backend.keychain import KeychainError, KeychainReference, MacOSKeychain
from backend.models import (
    CopyFill,
    CopyLedger,
    CopyOrder,
    CopyPosition,
    CopySubscription,
    CurrentPosition,
    ExecutionAccount,
    GlobalSettings,
    PositionEvent,
    PositionOverlapAlert,
    PositionOverlapPeriod,
    WalletTrade,
    WatchedWallet,
)
from backend.monitor import WalletMonitor, fills_reconcile, utcnow
from backend.polymarket import (
    InvalidWalletInput,
    PolymarketAPIError,
    PolymarketClient,
)
from backend.purchase_history import (
    ActivePositionCycle,
    active_position_cycle,
    build_purchase_lots,
    opened_date,
)
from backend.schemas import (
    CopyDailyRealizedPnlRead,
    CopyDashboardRead,
    CopyOrderRead,
    CopyOrdersResponse,
    CopyOverviewRead,
    CopyOverviewTotalsRead,
    CopyPortfolioSummaryRead,
    CopyPositionRead,
    CopyPositionsResponse,
    CopyRecommendation,
    CopyStrategyOverviewRead,
    CopySubscriptionAction,
    CopySubscriptionCreate,
    CopySubscriptionEnabledUpdate,
    CopySubscriptionRead,
    CopySubscriptionUpdate,
    CopyWalletSummaryRead,
    CopyWorkspaceOrderRead,
    CopyWorkspacePositionRead,
    EventRead,
    EventsResponse,
    ExecutionAccountRead,
    ExecutionAccountUpdate,
    GlobalSettingsRead,
    GlobalSettingsUpdate,
    HealthRead,
    PositionCycleTradeRead,
    PositionEventCycleRead,
    PositionEventGroupRead,
    PositionEventGroupsResponse,
    PositionOverlapAlertRead,
    PositionOverlapAlertsResponse,
    PositionOverlapDetail,
    PositionOverlapRead,
    PositionOverlapsResponse,
    PositionRead,
    PositionsResponse,
    PositionSummary,
    PurchaseLotRead,
    RehearsalExecuteRequest,
    RehearsalPreviewRead,
    RehearsalPreviewRequest,
    WalletCreate,
    WalletRead,
    WalletRecordedPnlRead,
    WalletUpdate,
)
from backend.trading import (
    V2_EXCHANGE_ADDRESS,
    V2_NEG_RISK_EXCHANGE_ADDRESS,
    MarketTradeRequest,
    OfficialClobTrader,
    TradingUnavailable,
)


def wallet_is_stale(wallet: WatchedWallet, settings: Settings) -> bool:
    stale_after = timedelta(seconds=max(30.0, settings.poll_interval_seconds * 2))
    return (
        wallet.last_success_at is None
        or wallet.status == "error"
        or utcnow() - wallet.last_success_at > stale_after
    )


def position_ratio(
    my_size: Decimal,
    tracked_size: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    percent = my_size / tracked_size * Decimal("100")
    if my_size <= tracked_size:
        return percent, Decimal("1"), tracked_size / my_size
    return percent, my_size / tracked_size, Decimal("1")


def encode_event_cursor(event: PositionEvent) -> str:
    return f"{event.settled_at.isoformat(timespec='microseconds')}|{event.id}"


def decode_event_cursor(raw_cursor: str) -> tuple[datetime, int]:
    try:
        raw_timestamp, raw_id = raw_cursor.rsplit("|", 1)
        timestamp = datetime.fromisoformat(raw_timestamp)
        event_id = int(raw_id)
    except (TypeError, ValueError) as error:
        raise ValueError("事件游标无效") from error
    if timestamp.tzinfo is not None or event_id <= 0:
        raise ValueError("事件游标无效")
    return timestamp, event_id


def encode_position_group_cursor(group: PositionEventGroupRead) -> str:
    timestamp = group.latest_recorded_at.isoformat(timespec="microseconds")
    return f"{timestamp}|{group.latest_event_id}|{group.asset_id}"


def decode_position_group_cursor(raw_cursor: str) -> tuple[datetime, int, str]:
    try:
        raw_timestamp, raw_id, asset_id = raw_cursor.split("|", 2)
        timestamp = datetime.fromisoformat(raw_timestamp)
        event_id = int(raw_id)
    except (TypeError, ValueError) as error:
        raise ValueError("仓位组游标无效") from error
    if timestamp.tzinfo is not None or event_id <= 0 or not asset_id:
        raise ValueError("仓位组游标无效")
    return timestamp, event_id, asset_id


def encode_copy_order_cursor(order: CopyOrder) -> str:
    return f"{order.created_at.isoformat(timespec='microseconds')}|{order.id}"


def decode_copy_order_cursor(raw_cursor: str) -> tuple[datetime, int]:
    try:
        raw_timestamp, raw_id = raw_cursor.rsplit("|", 1)
        timestamp = datetime.fromisoformat(raw_timestamp)
        order_id = int(raw_id)
    except (TypeError, ValueError) as error:
        raise ValueError("记录游标无效") from error
    if timestamp.tzinfo is not None or order_id <= 0:
        raise ValueError("记录游标无效")
    return timestamp, order_id


DEFAULT_COPY_RATIO = Decimal("10")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def validate_copy_caps(payload: CopySubscriptionCreate | CopySubscriptionUpdate) -> None:
    if payload.position_cap_usdc > payload.total_exposure_cap_usdc:
        raise HTTPException(
            status_code=422,
            detail="单仓最大投入不能高于总敞口上限",
        )


async def copy_subscription_read(
    session: Any,
    subscription: CopySubscription,
) -> CopySubscriptionRead:
    positions = list(
        (
            await session.scalars(
                select(CopyPosition).where(CopyPosition.subscription_id == subscription.id)
            )
        ).all()
    )
    exposure = sum(
        (position.attributed_cost + position.reserved_buy_usdc for position in positions),
        start=Decimal("0"),
    )
    local_start = datetime.now(SHANGHAI).replace(hour=0, minute=0, second=0, microsecond=0)
    utc_start = local_start.astimezone(UTC).replace(tzinfo=None)
    ledger = list(
        (
            await session.scalars(
                select(CopyLedger).where(
                    CopyLedger.subscription_id == subscription.id,
                    CopyLedger.timestamp >= utc_start,
                )
            )
        ).all()
    )
    bought = sum(
        (entry.amount_usdc for entry in ledger if entry.type == "buy"),
        start=Decimal("0"),
    )
    realized = sum((entry.realized_pnl for entry in ledger), start=Decimal("0"))
    wallet = await session.get(WatchedWallet, subscription.tracked_wallet_id)
    return CopySubscriptionRead.model_validate(subscription).model_copy(
        update={
            "enabled": subscription.state == "active",
            "tracked_wallet_label": wallet.label if wallet else None,
            "open_exposure_usdc": exposure,
            "daily_bought_usdc": bought,
            "daily_realized_pnl": realized,
        }
    )


async def copy_daily_realized_pnl(
    session: Any,
    *,
    reference_time: datetime | None = None,
    tracked_wallet_id: int | None = None,
) -> list[CopyDailyRealizedPnlRead]:
    reference = reference_time or datetime.now(SHANGHAI)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    local_reference = reference.astimezone(SHANGHAI)
    today = local_reference.date()
    local_end = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=SHANGHAI)
    utc_end = local_end.astimezone(UTC).replace(tzinfo=None)
    ledger_query = select(CopyLedger).where(CopyLedger.timestamp < utc_end)
    if tracked_wallet_id is not None:
        ledger_query = ledger_query.join(
            CopySubscription,
            CopySubscription.id == CopyLedger.subscription_id,
        ).where(CopySubscription.tracked_wallet_id == tracked_wallet_id)
    ledger = list((await session.scalars(ledger_query.order_by(CopyLedger.timestamp.asc()))).all())
    default_first_day = today - timedelta(days=29)
    earliest_day = (
        ledger[0].timestamp.replace(tzinfo=UTC).astimezone(SHANGHAI).date()
        if ledger
        else default_first_day
    )
    first_day = min(default_first_day, earliest_day)
    days = (today - first_day).days + 1
    realized_totals: dict[date, Decimal] = {
        first_day + timedelta(days=offset): Decimal("0") for offset in range(days)
    }
    bought_totals: dict[date, Decimal] = {
        first_day + timedelta(days=offset): Decimal("0") for offset in range(days)
    }
    for entry in ledger:
        local_day = entry.timestamp.replace(tzinfo=UTC).astimezone(SHANGHAI).date()
        if local_day not in realized_totals:
            continue
        realized_totals[local_day] += entry.realized_pnl
        if entry.type == "buy":
            bought_totals[local_day] += entry.amount_usdc
    return [
        CopyDailyRealizedPnlRead(
            date=day,
            realized_pnl=realized_totals[day],
            bought_usdc=bought_totals[day],
        )
        for day in sorted(realized_totals)
    ]


async def copy_position_marks(
    request: Request,
    positions: list[CopyPosition],
) -> dict[int, Decimal | None]:
    """Return conservative marks, using final payout when a resolved book is gone."""
    open_positions = [position for position in positions if position.attributed_size > 0]
    quote_semaphore = asyncio.Semaphore(5)

    async def fetch_bid(position: CopyPosition) -> tuple[int, Decimal | None]:
        async with quote_semaphore:
            try:
                book = await request.app.state.polymarket_client.fetch_order_book(position.asset_id)
            except Exception:
                return position.id, None
            return position.id, book.best_bid

    marks = dict(await asyncio.gather(*(fetch_bid(position) for position in open_positions)))
    missing = [position for position in open_positions if marks.get(position.id) is None]
    if not missing:
        return marks
    try:
        resolutions = await request.app.state.polymarket_client.fetch_market_resolutions(
            [position.condition_id for position in missing]
        )
    except Exception:
        return marks
    for position in missing:
        resolution = resolutions.get(position.condition_id)
        if resolution is not None and position.asset_id in resolution.payout_by_asset_id:
            marks[position.id] = resolution.payout_by_asset_id[position.asset_id]
    return marks


async def workspace_position_reads(
    request: Request,
    session: Any,
    positions: list[CopyPosition],
    wallet_by_subscription: dict[int, WatchedWallet],
) -> tuple[list[CopyWorkspacePositionRead], CopyPortfolioSummaryRead]:
    position_ids = [position.id for position in positions]
    fill_totals: dict[int, dict[str, Decimal]] = {}
    if position_ids:
        aggregate_rows = (
            await session.execute(
                select(
                    CopyOrder.copy_position_id,
                    CopyOrder.side,
                    func.sum(CopyFill.size),
                    func.sum(CopyFill.amount),
                )
                .join(CopyFill, CopyFill.order_id == CopyOrder.id)
                .where(CopyOrder.copy_position_id.in_(position_ids))
                .group_by(CopyOrder.copy_position_id, CopyOrder.side)
            )
        ).all()
        for position_id, side, size, amount in aggregate_rows:
            totals = fill_totals.setdefault(
                int(position_id),
                {
                    "BUY_size": Decimal("0"),
                    "BUY_usdc": Decimal("0"),
                    "SELL_size": Decimal("0"),
                    "SELL_usdc": Decimal("0"),
                },
            )
            totals[f"{side}_size"] = size or Decimal("0")
            totals[f"{side}_usdc"] = amount or Decimal("0")

    bids = await copy_position_marks(request, positions)
    valued_at = utcnow()
    result: list[CopyWorkspacePositionRead] = []
    for position in positions:
        wallet = wallet_by_subscription.get(position.subscription_id)
        if wallet is None:
            continue
        totals = fill_totals.get(
            position.id,
            {
                "BUY_size": Decimal("0"),
                "BUY_usdc": Decimal("0"),
                "SELL_size": Decimal("0"),
                "SELL_usdc": Decimal("0"),
            },
        )
        lifetime_bought_size = totals["BUY_size"]
        lifetime_bought_usdc = totals["BUY_usdc"]
        if position.attributed_size <= 0 and lifetime_bought_size <= 0:
            continue
        average_entry_price = (
            position.attributed_cost / position.attributed_size
            if position.attributed_size > 0
            else None
        )
        lifetime_average_buy_price = (
            lifetime_bought_usdc / lifetime_bought_size if lifetime_bought_size > 0 else None
        )
        current_bid = bids.get(position.id) if position.attributed_size > 0 else None
        if position.attributed_size > 0 and current_bid is not None:
            current_value = position.attributed_size * current_bid
            unrealized_pnl = current_value - position.attributed_cost
            unrealized_pnl_percent = (
                unrealized_pnl / position.attributed_cost * Decimal("100")
                if position.attributed_cost > 0
                else None
            )
            total_pnl = unrealized_pnl + position.realized_pnl
            valuation_status = "ok"
            position_valued_at = valued_at
        elif position.attributed_size > 0:
            current_value = None
            unrealized_pnl = None
            unrealized_pnl_percent = None
            total_pnl = None
            valuation_status = "unavailable"
            position_valued_at = valued_at
        else:
            current_value = None
            unrealized_pnl = None
            unrealized_pnl_percent = None
            total_pnl = position.realized_pnl
            valuation_status = "not_applicable"
            position_valued_at = None
        position_payload = CopyPositionRead.model_validate(position).model_dump()
        result.append(
            CopyWorkspacePositionRead.model_validate(
                {
                    **position_payload,
                    "tracked_wallet_id": wallet.id,
                    "tracked_wallet_label": wallet.label,
                    "tracked_wallet_address": wallet.proxy_wallet or wallet.address,
                    "average_entry_price": average_entry_price,
                    "current_bid": current_bid,
                    "current_value": current_value,
                    "unrealized_pnl": unrealized_pnl,
                    "unrealized_pnl_percent": unrealized_pnl_percent,
                    "total_pnl": total_pnl,
                    "lifetime_bought_size": lifetime_bought_size,
                    "lifetime_bought_usdc": lifetime_bought_usdc,
                    "lifetime_sold_size": totals["SELL_size"],
                    "lifetime_sold_usdc": totals["SELL_usdc"],
                    "lifetime_average_buy_price": lifetime_average_buy_price,
                    "valuation_status": valuation_status,
                    "valued_at": position_valued_at,
                }
            )
        )

    return result, workspace_portfolio(result, valued_at)


def workspace_portfolio(
    items: list[CopyWorkspacePositionRead],
    valued_at: datetime,
) -> CopyPortfolioSummaryRead:
    open_items = [item for item in items if item.attributed_size > 0]
    open_cost = sum((item.attributed_cost for item in open_items), start=Decimal("0"))
    realized_pnl = sum((item.realized_pnl for item in items), start=Decimal("0"))
    unpriced_positions = sum(1 for item in open_items if item.current_value is None)
    valuation_complete = unpriced_positions == 0
    if valuation_complete:
        market_value = sum(
            (item.current_value or Decimal("0") for item in open_items),
            start=Decimal("0"),
        )
        unrealized_pnl = market_value - open_cost
        total_pnl = unrealized_pnl + realized_pnl
    else:
        market_value = None
        unrealized_pnl = None
        total_pnl = None
    return CopyPortfolioSummaryRead(
        open_cost_usdc=open_cost,
        market_value_usdc=market_value,
        unrealized_pnl=unrealized_pnl,
        realized_pnl=realized_pnl,
        total_pnl=total_pnl,
        valuation_complete=valuation_complete,
        unpriced_positions=unpriced_positions,
        valued_at=valued_at if open_items else None,
    )


async def workspace_order_reads(
    session: Any,
    orders: list[CopyOrder],
) -> list[CopyWorkspaceOrderRead]:
    subscription_ids = {
        order.subscription_id for order in orders if order.subscription_id is not None
    }
    position_ids = {
        order.copy_position_id for order in orders if order.copy_position_id is not None
    }
    order_ids = [order.id for order in orders]
    subscriptions = {
        item.id: item
        for item in (
            list(
                (
                    await session.scalars(
                        select(CopySubscription).where(CopySubscription.id.in_(subscription_ids))
                    )
                ).all()
            )
            if subscription_ids
            else []
        )
    }
    wallet_ids = {item.tracked_wallet_id for item in subscriptions.values()}
    wallets = {
        item.id: item
        for item in (
            list(
                (
                    await session.scalars(
                        select(WatchedWallet).where(WatchedWallet.id.in_(wallet_ids))
                    )
                ).all()
            )
            if wallet_ids
            else []
        )
    }
    positions = {
        item.id: item
        for item in (
            list(
                (
                    await session.scalars(
                        select(CopyPosition).where(CopyPosition.id.in_(position_ids))
                    )
                ).all()
            )
            if position_ids
            else []
        )
    }
    fill_totals: dict[int, tuple[Decimal, Decimal]] = {}
    if order_ids:
        rows = (
            await session.execute(
                select(
                    CopyFill.order_id,
                    func.sum(CopyFill.size),
                    func.sum(CopyFill.amount),
                )
                .where(CopyFill.order_id.in_(order_ids))
                .group_by(CopyFill.order_id)
            )
        ).all()
        fill_totals = {
            int(order_id): (size or Decimal("0"), amount or Decimal("0"))
            for order_id, size, amount in rows
        }

    result: list[CopyWorkspaceOrderRead] = []
    for order in orders:
        subscription = subscriptions.get(order.subscription_id)
        wallet = wallets.get(subscription.tracked_wallet_id) if subscription is not None else None
        position = positions.get(order.copy_position_id)
        fill_size, fill_amount = fill_totals.get(order.id, (order.filled_size, order.filled_usdc))
        average_fill_price = fill_amount / fill_size if fill_size > 0 else None
        result.append(
            CopyWorkspaceOrderRead.model_validate(order).model_copy(
                update={
                    "tracked_wallet_id": wallet.id if wallet else None,
                    "tracked_wallet_label": wallet.label if wallet else None,
                    "tracked_wallet_address": (
                        wallet.proxy_wallet or wallet.address if wallet else None
                    ),
                    "title": position.title if position else None,
                    "outcome": position.outcome if position else None,
                    "event_slug": position.event_slug if position else None,
                    "average_fill_price": average_fill_price,
                }
            )
        )
    return result


def execution_account_read(account: ExecutionAccount | None) -> ExecutionAccountRead | None:
    if account is None:
        return None
    return ExecutionAccountRead.model_validate(account).model_copy(
        update={
            "credentials_configured": bool(account.keychain_service and account.keychain_account)
        }
    )


def execution_account_capacity(account: ExecutionAccount) -> Decimal:
    balance = account.collateral_balance or Decimal("0")
    spendable = max(
        Decimal("0"),
        min(account.budget_usdc, balance) - account.cash_reserve_usdc,
    )
    return min(account.max_total_exposure_usdc, spendable)


async def reserved_copy_capacity(
    session: Any,
    *,
    exclude_subscription_id: int | None = None,
) -> Decimal:
    subscriptions = list((await session.scalars(select(CopySubscription))).all())
    total = Decimal("0")
    for subscription in subscriptions:
        if subscription.id == exclude_subscription_id:
            continue
        if subscription.state == "active":
            total += subscription.total_exposure_cap_usdc
            continue
        exposure = await session.scalar(
            select(
                func.coalesce(
                    func.sum(CopyPosition.attributed_cost + CopyPosition.reserved_buy_usdc),
                    0,
                )
            ).where(
                CopyPosition.subscription_id == subscription.id,
                (CopyPosition.attributed_size > 0) | (CopyPosition.reserved_buy_usdc > 0),
            )
        )
        total += Decimal(exposure or 0)
    return total


async def copy_ratio_percent(session: Any) -> Decimal:
    settings = await session.get(GlobalSettings, 1)
    return settings.copy_ratio_percent if settings is not None else DEFAULT_COPY_RATIO


def copy_recommendation(
    event: PositionEvent,
    ratio_percent: Decimal,
) -> CopyRecommendation | None:
    if event.type == "redeemed":
        return None
    shares = abs(event.delta_size) * ratio_percent / Decimal("100")
    estimated_usdc = (
        shares * event.average_fill_price
        if event.average_fill_price is not None and event.average_fill_price > 0
        else None
    )
    return CopyRecommendation(
        action="buy" if event.delta_size > 0 else "sell",
        ratio_percent=ratio_percent,
        shares=shares,
        estimated_usdc=estimated_usdc,
    )


def event_read(event: PositionEvent, ratio_percent: Decimal) -> EventRead:
    item = EventRead.model_validate(event).model_copy(
        update={"copy_recommendation": copy_recommendation(event, ratio_percent)}
    )
    reconciliation_status = event.reconciliation_status
    if reconciliation_status == "partial" and event.fills:
        net_fill_size = sum(
            (fill.size if fill.side == "BUY" else -fill.size for fill in event.fills),
            start=Decimal("0"),
        )
        if fills_reconcile(net_fill_size, event.delta_size):
            reconciliation_status = "matched"
            item = item.model_copy(update={"reconciliation_status": "matched"})
    if (
        event.type == "closed"
        and reconciliation_status == "matched"
        and event.before_size > 0
        and event.after_size == 0
        and event.fills
    ):
        bought_during_close = sum(
            (fill.amount for fill in event.fills if fill.side == "BUY"),
            start=Decimal("0"),
        )
        proceeds = sum(
            (fill.amount for fill in event.fills if fill.side == "SELL"),
            start=Decimal("0"),
        )
        cost_basis = event.before_size * event.before_avg_price + bought_during_close
        profit = proceeds - cost_basis
        profit_percent = profit / cost_basis * Decimal("100") if cost_basis > 0 else None
        return item.model_copy(
            update={
                "close_cost_basis": cost_basis,
                "close_proceeds": proceeds,
                "close_profit": profit,
                "close_profit_percent": profit_percent,
                "close_profit_complete": True,
            }
        )
    if event.type != "redeemed":
        return item

    size = event.before_size
    payout = event.payout_amount
    cost_basis = event.redemption_cost_basis
    redemption_price = payout / size if payout is not None and size > 0 else None
    entry_price = cost_basis / size if cost_basis is not None and size > 0 else None
    profit = payout - cost_basis if payout is not None and cost_basis is not None else None
    profit_percent = (
        profit / cost_basis * Decimal("100")
        if profit is not None and cost_basis is not None and cost_basis > 0
        else None
    )
    return item.model_copy(
        update={
            "redemption_entry_price": entry_price,
            "redemption_price": redemption_price,
            "redemption_profit": profit,
            "redemption_profit_percent": profit_percent,
            "redemption_cost_complete": cost_basis is not None,
        }
    )


def recorded_event_profit(event: PositionEvent, item: EventRead) -> tuple[Decimal, int]:
    if event.type == "redeemed":
        if item.redemption_cost_complete and item.redemption_profit is not None:
            return item.redemption_profit, 0
        return Decimal("0"), 1
    if event.type not in {"decreased", "closed"}:
        return Decimal("0"), 0
    if item.reconciliation_status != "matched" or not event.fills:
        return Decimal("0"), 1
    bought = sum(
        (fill.amount for fill in event.fills if fill.side == "BUY"),
        start=Decimal("0"),
    )
    sold = sum(
        (fill.amount for fill in event.fills if fill.side == "SELL"),
        start=Decimal("0"),
    )
    before_cost = event.before_size * event.before_avg_price
    after_cost = event.after_size * event.after_avg_price
    return sold - bought + after_cost - before_cost, 0


def position_event_cycle_read(
    entries: list[tuple[PositionEvent, EventRead]],
    *,
    cycle_number: int,
    is_current: bool,
    forced_history_gap: bool = False,
) -> PositionEventCycleRead:
    first_event = entries[0][0]
    last_event = entries[-1][0]
    if forced_history_gap:
        cycle_status = "history_gap"
    elif last_event.type == "closed":
        cycle_status = "closed"
    elif last_event.type == "redeemed":
        cycle_status = "redeemed"
    elif is_current:
        cycle_status = "open"
    else:
        cycle_status = "history_gap"
    confirmed_realized_pnl = Decimal("0")
    incomplete_profit_events = 0
    for event, item in entries:
        profit, incomplete = recorded_event_profit(event, item)
        confirmed_realized_pnl += profit
        incomplete_profit_events += incomplete
    return PositionEventCycleRead(
        cycle_number=cycle_number,
        status=cycle_status,
        start_source="opened" if first_event.type == "opened" else "first_recorded",
        history_complete=(
            first_event.type == "opened"
            and cycle_status in {"open", "closed", "redeemed"}
            and not forced_history_gap
        ),
        started_at=first_event.settled_at,
        ended_at=None if cycle_status == "open" else last_event.settled_at,
        confirmed_realized_pnl=confirmed_realized_pnl,
        incomplete_profit_events=incomplete_profit_events,
        events=[item for _, item in entries],
    )


def position_event_group_read(
    events: list[PositionEvent],
    *,
    ratio_percent: Decimal,
    is_current: bool,
) -> PositionEventGroupRead:
    ordered = sorted(events, key=lambda event: (event.settled_at, event.id))
    entries = [(event, event_read(event, ratio_percent)) for event in ordered]
    cycle_entries: list[list[tuple[PositionEvent, EventRead]]] = []
    forced_gaps: list[bool] = []
    current: list[tuple[PositionEvent, EventRead]] = []
    for entry in entries:
        event = entry[0]
        if current and (event.type == "opened" or current[-1][0].type in {"closed", "redeemed"}):
            forced_gaps.append(current[-1][0].type not in {"closed", "redeemed"})
            cycle_entries.append(current)
            current = []
        current.append(entry)
    if current:
        forced_gaps.append(False)
        cycle_entries.append(current)

    cycles = [
        position_event_cycle_read(
            cycle,
            cycle_number=index + 1,
            is_current=is_current and index == len(cycle_entries) - 1,
            forced_history_gap=forced_gaps[index],
        )
        for index, cycle in enumerate(cycle_entries)
    ]
    latest = ordered[-1]
    event_counts = {
        event_type: sum(event.type == event_type for event in ordered)
        for event_type in ("opened", "increased", "decreased", "closed", "redeemed")
    }
    incomplete_profit_events = sum(cycle.incomplete_profit_events for cycle in cycles)
    has_realized_event = any(
        event_counts[event_type] > 0 for event_type in ("decreased", "closed", "redeemed")
    )
    return PositionEventGroupRead(
        wallet_id=latest.wallet_id,
        asset_id=latest.asset_id,
        condition_id=latest.condition_id,
        title=latest.title,
        outcome=latest.outcome,
        event_slug=latest.event_slug,
        status=cycles[-1].status,
        event_count=len(ordered),
        event_counts=event_counts,
        cycle_count=len(cycles),
        first_recorded_at=ordered[0].settled_at,
        latest_recorded_at=latest.settled_at,
        latest_event_id=latest.id,
        confirmed_realized_pnl=sum(
            (cycle.confirmed_realized_pnl for cycle in cycles),
            start=Decimal("0"),
        ),
        incomplete_profit_events=incomplete_profit_events,
        realized_pnl_status=(
            "unavailable"
            if incomplete_profit_events > 0
            else "confirmed"
            if has_realized_event
            else "unrealized"
            if cycles[-1].status == "open"
            else "unavailable"
        ),
        cycles=cycles,
    )


def cycle_trade_reads(cycle: ActivePositionCycle) -> list[PositionCycleTradeRead]:
    return [
        PositionCycleTradeRead(
            id=trade.id,
            type=(
                "opened"
                if cycle.complete and index == 0 and trade.side == "BUY"
                else "increased"
                if trade.side == "BUY"
                else "decreased"
            ),
            size=trade.size,
            price=trade.price,
            amount=trade.amount,
            timestamp=trade.timestamp,
            transaction_hash=trade.transaction_hash,
        )
        for index, trade in enumerate(cycle.trades)
    ]


async def position_read_with_lots(
    session: Any,
    position: CurrentPosition,
) -> PositionRead:
    trades = list(
        (
            await session.scalars(
                select(WalletTrade)
                .where(
                    WalletTrade.wallet_id == position.wallet_id,
                    WalletTrade.asset_id == position.asset_id,
                )
                .order_by(WalletTrade.timestamp.asc(), WalletTrade.id.asc())
            )
        ).all()
    )
    lots_by_asset, _ = build_purchase_lots([position], trades)
    lots = [PurchaseLotRead.model_validate(lot) for lot in lots_by_asset.get(position.asset_id, [])]
    cycle = active_position_cycle(position, trades)
    return PositionRead.model_validate(position).model_copy(
        update={
            "first_opened_at": cycle.opened_at,
            "first_opened_at_source": cycle.opened_at_source,
            "opened_date": opened_date(cycle.opened_at),
            "cycle_trades": cycle_trade_reads(cycle),
            "cycle_history_complete": cycle.complete,
            "purchase_lots": lots,
        }
    )


def first_opened_metadata(
    position: CurrentPosition,
    trades: list[WalletTrade],
) -> tuple[datetime, str]:
    cycle = active_position_cycle(position, trades)
    return cycle.opened_at, cycle.opened_at_source


def overlap_alert_read(
    alert: PositionOverlapAlert,
    ratio_percent: Decimal,
) -> PositionOverlapAlertRead:
    return PositionOverlapAlertRead(
        id=alert.id,
        my_wallet_id=alert.my_wallet_id,
        tracked_wallet_id=alert.tracked_wallet_id,
        asset_id=alert.period.asset_id,
        condition_id=alert.period.condition_id,
        title=alert.period.title,
        outcome=alert.period.outcome,
        event_slug=alert.period.event_slug,
        market_slug=alert.period.market_slug,
        type=alert.event.type,
        before_size=alert.event.before_size,
        after_size=alert.event.after_size,
        delta_size=alert.event.delta_size,
        detected_at=alert.event.first_detected_at,
        created_at=alert.created_at,
        read_at=alert.read_at,
        copy_recommendation=copy_recommendation(alert.event, ratio_percent),
    )


def create_app(
    *,
    settings: Settings | None = None,
    client: PolymarketClient | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        database = Database(resolved_settings)
        await database.initialize()
        owns_client = client is None
        polymarket_client = client or PolymarketClient(
            data_api_url=resolved_settings.data_api_url,
            gamma_api_url=resolved_settings.gamma_api_url,
            clob_api_url=resolved_settings.clob_api_url,
            timeout=resolved_settings.request_timeout_seconds,
        )
        broker = EventBroker()
        monitor = WalletMonitor(
            database=database,
            client=polymarket_client,
            broker=broker,
            settings=resolved_settings,
        )
        keychain = MacOSKeychain()
        copy_engine = CopyTradingEngine(
            database=database,
            client=polymarket_client,
            settings=resolved_settings,
            keychain=keychain,
        )
        application.state.settings = resolved_settings
        application.state.database = database
        application.state.polymarket_client = polymarket_client
        application.state.broker = broker
        application.state.monitor = monitor
        application.state.keychain = keychain
        application.state.copy_engine = copy_engine
        application.state.copy_toggle_lock = asyncio.Lock()
        application.state.rehearsal_previews = {}
        if resolved_settings.start_monitor:
            monitor.start()
            copy_engine.start()
        try:
            yield
        finally:
            await copy_engine.stop()
            await monitor.stop()
            if owns_client:
                await polymarket_client.close()
            await database.close()

    application = FastAPI(
        title="Polymarket 钱包持仓监控器",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    @application.get("/healthz", response_model=HealthRead)
    async def health(request: Request) -> HealthRead:
        database: Database = request.app.state.database
        async with database.sessions() as session:
            await session.execute(text("SELECT 1"))
        return HealthRead(status="ok", database="ok")

    @application.get("/api/settings", response_model=GlobalSettingsRead)
    async def get_global_settings(request: Request) -> GlobalSettingsRead:
        database: Database = request.app.state.database
        async with database.sessions() as session:
            ratio = await copy_ratio_percent(session)
        return GlobalSettingsRead(copy_ratio_percent=ratio)

    @application.put("/api/settings", response_model=GlobalSettingsRead)
    async def update_global_settings(
        payload: GlobalSettingsUpdate,
        request: Request,
    ) -> GlobalSettingsRead:
        database: Database = request.app.state.database
        async with database.sessions() as session:
            settings = await session.get(GlobalSettings, 1)
            if settings is None:
                settings = GlobalSettings(
                    id=1,
                    copy_ratio_percent=payload.copy_ratio_percent,
                )
                session.add(settings)
            else:
                settings.copy_ratio_percent = payload.copy_ratio_percent
            await session.commit()
            await session.refresh(settings)
            return GlobalSettingsRead.model_validate(settings)

    @application.get("/api/copy-trading/account", response_model=ExecutionAccountRead | None)
    async def get_execution_account(request: Request) -> ExecutionAccountRead | None:
        database: Database = request.app.state.database
        async with database.sessions() as session:
            return execution_account_read(await session.get(ExecutionAccount, 1))

    @application.put("/api/copy-trading/account", response_model=ExecutionAccountRead)
    async def configure_execution_account(
        payload: ExecutionAccountUpdate,
        request: Request,
    ) -> ExecutionAccountRead:
        if payload.cash_reserve_usdc > payload.budget_usdc:
            raise HTTPException(status_code=422, detail="现金保留额不能高于钱包预算")
        if payload.max_total_exposure_usdc > payload.budget_usdc - payload.cash_reserve_usdc:
            raise HTTPException(status_code=422, detail="总敞口不能高于预算扣除现金保留额")
        database: Database = request.app.state.database
        keychain: MacOSKeychain = request.app.state.keychain
        async with database.sessions() as session:
            wallet = await session.get(WatchedWallet, payload.wallet_id)
            if wallet is None or wallet.wallet_role != "self":
                raise HTTPException(status_code=422, detail="执行账户必须绑定唯一的“我的钱包”")
            account = await session.get(ExecutionAccount, 1)
            if account is not None and account.wallet_id != wallet.id:
                has_position = await session.scalar(
                    select(CopyPosition.id).where(CopyPosition.attributed_size > 0).limit(1)
                )
                has_order = await session.scalar(
                    select(CopyOrder.id)
                    .where(CopyOrder.status.in_(["open", "partially_filled", "submitted"]))
                    .limit(1)
                )
                if has_position is not None or has_order is not None:
                    raise HTTPException(
                        status_code=409,
                        detail="存在自动策略持仓或挂单，不能更换执行账户",
                    )
            signer = (payload.signer_address or wallet.address).lower()
            funder = (payload.funder_address or wallet.proxy_wallet).lower()
            if len(signer) != 42 or not signer.startswith("0x"):
                raise HTTPException(status_code=422, detail="签名钱包地址无效")
            if len(funder) != 42 or not funder.startswith("0x"):
                raise HTTPException(status_code=422, detail="资金钱包地址无效")
            reference = KeychainReference(service=DEFAULT_SERVICE, account=signer)
            try:
                await asyncio.to_thread(keychain.get_secret, reference)
                account_status = "configured"
            except KeychainError:
                account_status = "missing_key"
            now = utcnow()
            if account is None:
                account = ExecutionAccount(
                    id=1,
                    wallet_id=wallet.id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(account)
            account.wallet_id = wallet.id
            account.signer_address = signer
            account.funder_address = funder
            account.signature_type = payload.signature_type
            account.keychain_service = reference.service
            account.keychain_account = reference.account
            account.status = account_status
            account.budget_usdc = payload.budget_usdc
            account.cash_reserve_usdc = payload.cash_reserve_usdc
            account.max_total_exposure_usdc = payload.max_total_exposure_usdc
            account.daily_buy_limit_usdc = payload.daily_buy_limit_usdc
            account.daily_loss_limit_usdc = payload.daily_loss_limit_usdc
            account.auto_redeem = payload.auto_redeem
            account.collateral_balance = None
            account.last_balance_at = None
            account.last_error = None
            account.updated_at = now
            await session.commit()
            return execution_account_read(account)  # type: ignore[return-value]

    async def verify_execution_account_record(request: Request) -> ExecutionAccount:
        database: Database = request.app.state.database
        settings_for_request: Settings = request.app.state.settings
        keychain: MacOSKeychain = request.app.state.keychain
        async with database.sessions() as session:
            account = await session.get(ExecutionAccount, 1)
            if account is None or not account.keychain_service or not account.keychain_account:
                raise HTTPException(status_code=409, detail="请先配置执行账户和钥匙串密钥")
            trader = OfficialClobTrader(
                host=settings_for_request.clob_api_url,
                keychain=keychain,
                key_reference=KeychainReference(
                    service=account.keychain_service,
                    account=account.keychain_account,
                ),
                signature_type=account.signature_type,
                funder_address=account.funder_address,
                relayer_url=settings_for_request.relayer_api_url,
                rpc_url=settings_for_request.polygon_rpc_url,
            )
            execution_wallet = await session.get(WatchedWallet, account.wallet_id)
            try:
                signer_address, balance, allowances = await asyncio.gather(
                    trader.signer_address(),
                    trader.collateral_balance(),
                    trader.collateral_allowances(),
                )
                if signer_address != (account.signer_address or "").lower():
                    raise TradingUnavailable("钥匙串私钥与配置的签名地址不一致")
                if (
                    execution_wallet is None
                    or (account.funder_address or "").lower()
                    != execution_wallet.proxy_wallet.lower()
                ):
                    raise TradingUnavailable("Proxy 资金地址与“我的钱包”不一致")
                required_exchanges = {
                    V2_EXCHANGE_ADDRESS.lower(),
                    V2_NEG_RISK_EXCHANGE_ADDRESS.lower(),
                }
                if any(
                    allowances.get(address, Decimal("0")) <= 0 for address in required_exchanges
                ):
                    raise TradingUnavailable("pUSD 尚未授权给 V2 Exchange 合约")
            except (KeychainError, TradingUnavailable) as error:
                account.status = "error"
                account.last_error = str(error)[:1000]
                account.updated_at = utcnow()
                await session.commit()
                raise HTTPException(status_code=502, detail=str(error)) from error
            account.collateral_balance = balance
            account.last_balance_at = utcnow()
            account.status = (
                "ready" if balance > account.cash_reserve_usdc else "insufficient_balance"
            )
            account.last_error = None
            account.updated_at = utcnow()
            await session.commit()
            return account

    @application.post(
        "/api/copy-trading/account/verify",
        response_model=ExecutionAccountRead,
    )
    async def verify_execution_account(request: Request) -> ExecutionAccountRead:
        account = await verify_execution_account_record(request)
        result = execution_account_read(account)
        assert result is not None
        return result

    @application.post(
        "/api/copy-trading/account/balance/refresh",
        response_model=ExecutionAccountRead,
    )
    async def refresh_execution_account_balance(request: Request) -> ExecutionAccountRead:
        await request.app.state.copy_engine.refresh_execution_balance(force=True)
        database: Database = request.app.state.database
        async with database.sessions() as session:
            account = await session.get(ExecutionAccount, 1)
            if account is None:
                raise HTTPException(status_code=409, detail="请先配置执行账户")
            if account.last_error:
                raise HTTPException(status_code=502, detail=account.last_error)
            result = execution_account_read(account)
            assert result is not None
            return result

    @application.post("/api/copy-trading/reconcile")
    async def reconcile_copy_trading(request: Request) -> dict[str, str]:
        database: Database = request.app.state.database
        async with database.sessions() as session:
            subscription_ids = list(
                (
                    await session.scalars(
                        select(CopySubscription.id).where(
                            CopySubscription.state.in_(["active", "paused", "exit_only", "closing"])
                        )
                    )
                ).all()
            )
        for subscription_id in subscription_ids:
            await request.app.state.copy_engine.reconcile_terminal_positions(subscription_id)
        return {"status": "ok"}

    @application.post(
        "/api/copy-trading/rehearsal/preview",
        response_model=RehearsalPreviewRead,
    )
    async def preview_rehearsal(
        payload: RehearsalPreviewRequest,
        request: Request,
    ) -> RehearsalPreviewRead:
        if not request.app.state.settings.live_copy_enabled:
            raise HTTPException(status_code=409, detail="自动实盘已被系统紧急停用")
        database: Database = request.app.state.database
        async with database.sessions() as session:
            account = await session.get(ExecutionAccount, 1)
            if account is None or account.status != "ready" or account.signature_type not in {1, 3}:
                raise HTTPException(status_code=409, detail="请先完成 V2 执行钱包验证")
        try:
            market = await request.app.state.polymarket_client.resolve_market_outcome(
                payload.market_url, payload.outcome
            )
            book = await request.app.state.polymarket_client.fetch_order_book(market.asset_id)
        except (InvalidWalletInput, PolymarketAPIError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if book.best_ask is None:
            raise HTTPException(status_code=409, detail="市场当前没有可成交卖盘")
        fee_multiplier = Decimal("1") + Decimal(market.fee_rate_bps) / Decimal("10000")
        buy_amount = payload.max_total_usdc / fee_multiplier
        confirmation_id = secrets.token_urlsafe(32)
        expires_at = utcnow() + timedelta(minutes=5)
        preview = RehearsalPreviewRead(
            confirmation_id=confirmation_id,
            market_url=payload.market_url,
            asset_id=market.asset_id,
            condition_id=market.condition_id,
            title=market.title,
            outcome=market.outcome,
            best_ask=book.best_ask,
            fee_rate_bps=market.fee_rate_bps,
            max_total_usdc=payload.max_total_usdc,
            expires_at=expires_at,
        )
        request.app.state.rehearsal_previews[confirmation_id] = {
            "preview": preview,
            "buy_amount": buy_amount,
            "neg_risk": market.neg_risk,
        }
        return preview

    @application.post(
        "/api/copy-trading/rehearsal/execute",
        response_model=CopyOrderRead,
    )
    async def execute_rehearsal(
        payload: RehearsalExecuteRequest,
        request: Request,
    ) -> CopyOrderRead:
        if not request.app.state.settings.live_copy_enabled:
            raise HTTPException(status_code=409, detail="自动实盘已被系统紧急停用")
        stored = request.app.state.rehearsal_previews.pop(payload.confirmation_id, None)
        if stored is None:
            raise HTTPException(status_code=409, detail="演练确认已失效，请重新预览")
        preview: RehearsalPreviewRead = stored["preview"]
        if preview.expires_at < utcnow():
            raise HTTPException(status_code=409, detail="演练确认已过期，请重新预览")
        database: Database = request.app.state.database
        keychain: MacOSKeychain = request.app.state.keychain
        settings_for_request: Settings = request.app.state.settings
        async with database.sessions() as session:
            account = await session.get(ExecutionAccount, 1)
            if (
                account is None
                or account.status != "ready"
                or account.signature_type not in {1, 3}
                or not account.keychain_service
                or not account.keychain_account
            ):
                raise HTTPException(status_code=409, detail="V2 执行钱包当前不可用")
            trader = OfficialClobTrader(
                host=settings_for_request.clob_api_url,
                keychain=keychain,
                key_reference=KeychainReference(
                    service=account.keychain_service,
                    account=account.keychain_account,
                ),
                signature_type=account.signature_type,
                funder_address=account.funder_address,
                relayer_url=settings_for_request.relayer_api_url,
                rpc_url=settings_for_request.polygon_rpc_url,
            )
        book = await request.app.state.polymarket_client.fetch_order_book(preview.asset_id)
        if book.best_ask is None:
            raise HTTPException(status_code=409, detail="市场已不再开放交易")
        worst_price = min(
            Decimal("0.99"),
            book.best_ask + Decimal("0.05"),
        )
        buy_amount: Decimal = stored["buy_amount"]
        trade_request = MarketTradeRequest(
            asset_id=preview.asset_id,
            side="BUY",
            amount=buy_amount,
            worst_price=worst_price,
            neg_risk=stored["neg_risk"],
        )
        now = utcnow()
        order = CopyOrder(
            subscription_id=None,
            copy_position_id=None,
            leader_event_id=None,
            idempotency_key=f"rehearsal:{payload.confirmation_id}",
            source="rehearsal",
            signed_order_hash=None,
            asset_id=preview.asset_id,
            condition_id=preview.condition_id,
            side="BUY",
            requested_size=buy_amount / worst_price,
            requested_usdc=buy_amount,
            limit_price=worst_price,
            reference_price=book.best_ask,
            filled_size=Decimal("0"),
            filled_usdc=Decimal("0"),
            fee_usdc=Decimal("0"),
            status="planned",
            reason=None,
            external_order_id=None,
            external_trade_id=None,
            created_at=now,
            updated_at=now,
        )
        async with database.sessions() as session:
            session.add(order)
            await session.commit()
            order_id = order.id
        before_pusd, before_token = await asyncio.gather(
            trader.collateral_balance(), trader.outcome_balance(preview.asset_id)
        )
        try:
            prepared = await trader.prepare_market(trade_request)
            async with database.sessions() as session:
                planned = await session.get(CopyOrder, order_id)
                assert planned is not None
                planned.signed_order_hash = prepared.signed_order_hash
                planned.status = "signed"
                planned.updated_at = utcnow()
                await session.commit()
            result = await trader.submit_prepared_market(prepared)
        except TradingUnavailable as error:
            async with database.sessions() as session:
                planned = await session.get(CopyOrder, order_id)
                assert planned is not None
                planned.status = "reconciliation_pending"
                planned.reason = str(error)[:1000]
                planned.updated_at = utcnow()
                await session.commit()
            raise HTTPException(
                status_code=502,
                detail="演练提交结果待对账；系统不会自动重试",
            ) from error
        if result.external_trade_id and result.fee_usdc == 0:
            for attempt in range(3):
                fee = await trader.trade_fee(result.external_trade_id)
                if fee > 0:
                    result = replace(result, fee_usdc=fee)
                    break
                if attempt < 2:
                    await asyncio.sleep(1)
        await request.app.state.copy_engine._apply_result(order_id, result)
        total_spent = result.filled_usdc + result.fee_usdc
        if total_spent > preview.max_total_usdc:
            raise HTTPException(
                status_code=500,
                detail=(f"演练订单超过 {preview.max_total_usdc} USDC 硬上限，已标记审计"),
            )
        money_ok = False
        token_ok = False
        for attempt in range(10):
            after_pusd, after_token = await asyncio.gather(
                trader.collateral_balance(), trader.outcome_balance(preview.asset_id)
            )
            money_ok = abs((before_pusd - after_pusd) - total_spent) <= Decimal("0.02")
            token_ok = abs((after_token - before_token) - result.filled_size) <= Decimal("0.0001")
            if money_ok and token_ok:
                break
            if attempt < 9:
                await asyncio.sleep(1)
        async with database.sessions() as session:
            completed = await session.get(CopyOrder, order_id)
            assert completed is not None
            if result.filled_size <= 0 or not money_ok or not token_ok:
                completed.status = "reconciliation_pending"
                completed.reason = "订单成交与 pUSD/outcome token 余额尚未一致"
                completed.updated_at = utcnow()
                await session.commit()
                raise HTTPException(status_code=502, detail=completed.reason)
            return CopyOrderRead.model_validate(completed)

    @application.get(
        "/api/copy-trading/subscriptions",
        response_model=list[CopySubscriptionRead],
    )
    async def list_copy_subscriptions(request: Request) -> list[CopySubscriptionRead]:
        database: Database = request.app.state.database
        async with database.sessions() as session:
            subscriptions = list(
                (
                    await session.scalars(
                        select(CopySubscription).order_by(CopySubscription.id.asc())
                    )
                ).all()
            )
            return [
                await copy_subscription_read(session, subscription)
                for subscription in subscriptions
            ]

    @application.post(
        "/api/copy-trading/subscriptions",
        response_model=CopySubscriptionRead,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_copy_subscription(
        payload: CopySubscriptionCreate,
        request: Request,
    ) -> CopySubscriptionRead:
        validate_copy_caps(payload)
        database: Database = request.app.state.database
        async with database.sessions() as session:
            wallet = await session.get(WatchedWallet, payload.tracked_wallet_id)
            if wallet is None or wallet.wallet_role != "tracked":
                raise HTTPException(status_code=422, detail="请选择一个观察钱包作为目标")
            existing = await session.scalar(
                select(CopySubscription).where(
                    CopySubscription.tracked_wallet_id == payload.tracked_wallet_id
                )
            )
            if existing is not None:
                raise HTTPException(status_code=409, detail="该观察钱包已经有策略配置")
            baseline = await latest_event_id(session, payload.tracked_wallet_id)
            now = utcnow()
            values = payload.model_dump(exclude={"tracked_wallet_id"})
            subscription = CopySubscription(
                tracked_wallet_id=payload.tracked_wallet_id,
                state="disabled",
                baseline_event_id=baseline,
                last_processed_event_id=baseline,
                created_at=now,
                updated_at=now,
                **values,
            )
            session.add(subscription)
            await session.commit()
            return await copy_subscription_read(session, subscription)

    @application.put(
        "/api/copy-trading/subscriptions/{subscription_id}",
        response_model=CopySubscriptionRead,
    )
    async def update_copy_subscription(
        subscription_id: int,
        payload: CopySubscriptionUpdate,
        request: Request,
    ) -> CopySubscriptionRead:
        validate_copy_caps(payload)
        database: Database = request.app.state.database
        toggle_lock: asyncio.Lock = request.app.state.copy_toggle_lock
        async with toggle_lock:
            async with database.sessions() as session:
                subscription = await session.get(CopySubscription, subscription_id)
                if subscription is None:
                    raise HTTPException(status_code=404, detail="策略不存在")
                if subscription.state == "active":
                    account = await session.get(ExecutionAccount, 1)
                    if account is None or account.status != "ready":
                        raise HTTPException(status_code=409, detail="执行账户尚未通过余额验证")
                    reserved = await reserved_copy_capacity(
                        session,
                        exclude_subscription_id=subscription.id,
                    )
                    capacity = execution_account_capacity(account)
                    required = reserved + payload.total_exposure_cap_usdc
                    if required > capacity:
                        shortage = required - capacity
                        raise HTTPException(
                            status_code=409,
                            detail=f"执行钱包固定额度不足，还差 ${shortage:.2f}",
                        )
                for field, value in payload.model_dump().items():
                    setattr(subscription, field, value)
                subscription.updated_at = utcnow()
                await session.commit()
                return await copy_subscription_read(session, subscription)

    @application.put(
        "/api/copy-trading/subscriptions/{subscription_id}/enabled",
        response_model=CopySubscriptionRead,
    )
    async def set_copy_subscription_enabled(
        subscription_id: int,
        payload: CopySubscriptionEnabledUpdate,
        request: Request,
    ) -> CopySubscriptionRead:
        database: Database = request.app.state.database
        engine: CopyTradingEngine = request.app.state.copy_engine
        toggle_lock: asyncio.Lock = request.app.state.copy_toggle_lock
        async with toggle_lock:
            async with database.sessions() as session:
                subscription = await session.get(CopySubscription, subscription_id)
                if subscription is None:
                    raise HTTPException(status_code=404, detail="策略不存在")
                if payload.enabled and not payload.confirm_live:
                    raise HTTPException(status_code=422, detail="开启实盘需要明确确认")
                if payload.enabled and subscription.state == "active":
                    return await copy_subscription_read(session, subscription)
                if not payload.enabled and subscription.state != "active":
                    return await copy_subscription_read(session, subscription)

            if payload.enabled:
                if not request.app.state.settings.live_copy_enabled:
                    raise HTTPException(status_code=409, detail="自动实盘已被系统紧急停用")
                account = await verify_execution_account_record(request)
                if account.status != "ready":
                    balance = account.collateral_balance or Decimal("0")
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"当前余额 ${balance:.2f} 未高于现金保留额 "
                            f"${account.cash_reserve_usdc:.2f}"
                        ),
                    )
                async with database.sessions() as session:
                    subscription = await session.get(CopySubscription, subscription_id)
                    assert subscription is not None
                    if subscription.state == "closing":
                        raise HTTPException(status_code=409, detail="策略正在清仓，暂时不能开启")
                    reserved = await reserved_copy_capacity(
                        session,
                        exclude_subscription_id=subscription.id,
                    )
                    capacity = execution_account_capacity(account)
                    required = reserved + subscription.total_exposure_cap_usdc
                    if required > capacity:
                        shortage = required - capacity
                        raise HTTPException(
                            status_code=409,
                            detail=f"执行钱包固定额度不足，还差 ${shortage:.2f}",
                        )
                    subscription.state = "paused"
                    subscription.last_error = None
                    subscription.updated_at = utcnow()
                    await session.commit()
                await engine.cancel_open_orders(subscription_id)
                await engine.prime_subscription(subscription_id)
                async with database.sessions() as session:
                    subscription = await session.get(CopySubscription, subscription_id)
                    assert subscription is not None
                    subscription.state = "active"
                    subscription.enabled_at = utcnow()
                    subscription.updated_at = utcnow()
                    await session.commit()
                    result = await copy_subscription_read(session, subscription)
                engine.wake()
                return result

            async with database.sessions() as session:
                subscription = await session.get(CopySubscription, subscription_id)
                assert subscription is not None
                subscription.state = "exit_only"
                subscription.updated_at = utcnow()
                await session.commit()
            await engine.cancel_open_orders(subscription_id)
            engine.wake()
            async with database.sessions() as session:
                subscription = await session.get(CopySubscription, subscription_id)
                assert subscription is not None
                return await copy_subscription_read(session, subscription)

    @application.post(
        "/api/copy-trading/subscriptions/{subscription_id}/action",
        response_model=CopySubscriptionRead,
    )
    async def act_on_copy_subscription(
        subscription_id: int,
        payload: CopySubscriptionAction,
        request: Request,
    ) -> CopySubscriptionRead:
        database: Database = request.app.state.database
        engine: CopyTradingEngine = request.app.state.copy_engine
        async with database.sessions() as session:
            subscription = await session.get(CopySubscription, subscription_id)
            if subscription is None:
                raise HTTPException(status_code=404, detail="策略不存在")
            subscription.state = "closing"
            subscription.updated_at = utcnow()
            await session.commit()
        await engine.cancel_open_orders(subscription_id)
        engine.wake()
        async with database.sessions() as session:
            subscription = await session.get(CopySubscription, subscription_id)
            assert subscription is not None
            return await copy_subscription_read(session, subscription)

    @application.get(
        "/api/copy-trading/positions",
        response_model=CopyPositionsResponse,
    )
    async def get_copy_workspace_positions(
        request: Request,
        tracked_wallet_id: Annotated[int | None, Query(gt=0)] = None,
        scope: str = "open",
    ) -> CopyPositionsResponse:
        if scope not in {"open", "history"}:
            raise HTTPException(status_code=422, detail="持仓范围必须是 open 或 history")
        database: Database = request.app.state.database
        async with database.sessions() as session:
            subscription_query = select(CopySubscription)
            if tracked_wallet_id is not None:
                subscription_query = subscription_query.where(
                    CopySubscription.tracked_wallet_id == tracked_wallet_id
                )
            subscriptions = list((await session.scalars(subscription_query)).all())
            subscription_ids = [item.id for item in subscriptions]
            wallets = (
                {
                    wallet.id: wallet
                    for wallet in list(
                        (
                            await session.scalars(
                                select(WatchedWallet).where(
                                    WatchedWallet.id.in_(
                                        [item.tracked_wallet_id for item in subscriptions]
                                    )
                                )
                            )
                        ).all()
                    )
                }
                if subscriptions
                else {}
            )
            wallet_by_subscription = {
                item.id: wallets[item.tracked_wallet_id]
                for item in subscriptions
                if item.tracked_wallet_id in wallets
            }
            if not subscription_ids:
                return CopyPositionsResponse(
                    items=[],
                    portfolio=CopyPortfolioSummaryRead(),
                    as_of=utcnow(),
                )
            position_query = select(CopyPosition).where(
                CopyPosition.subscription_id.in_(subscription_ids)
            )
            if scope == "open":
                position_query = position_query.where(CopyPosition.attributed_size > 0)
            else:
                position_query = position_query.where(CopyPosition.attributed_size <= 0)
            positions = list(
                (
                    await session.scalars(position_query.order_by(CopyPosition.updated_at.desc()))
                ).all()
            )
            items, portfolio = await workspace_position_reads(
                request, session, positions, wallet_by_subscription
            )
            return CopyPositionsResponse(
                items=items,
                portfolio=portfolio,
                as_of=utcnow(),
            )

    @application.get(
        "/api/copy-trading/orders",
        response_model=CopyOrdersResponse,
    )
    async def get_copy_workspace_orders(
        request: Request,
        tracked_wallet_id: Annotated[int | None, Query(gt=0)] = None,
        side: str | None = None,
        status_group: str | None = None,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> CopyOrdersResponse:
        if side is not None and side not in {"BUY", "SELL"}:
            raise HTTPException(status_code=422, detail="买卖方向必须是 BUY 或 SELL")
        status_groups = {
            "filled": ["filled"],
            "partial": ["partially_filled"],
            "unfilled": ["unfilled"],
            "skipped": ["skipped", "blocked"],
            "processing": ["planned", "signed", "submitted"],
            "attention": ["reconciliation_pending", "interrupted_before_submit"],
        }
        if status_group is not None and status_group not in status_groups:
            raise HTTPException(status_code=422, detail="记录状态筛选无效")
        database: Database = request.app.state.database
        async with database.sessions() as session:
            query = select(CopyOrder).where(CopyOrder.source == "copy")
            if tracked_wallet_id is not None:
                subscription_ids = select(CopySubscription.id).where(
                    CopySubscription.tracked_wallet_id == tracked_wallet_id
                )
                query = query.where(CopyOrder.subscription_id.in_(subscription_ids))
            if side is not None:
                query = query.where(CopyOrder.side == side)
            if status_group is not None:
                query = query.where(CopyOrder.status.in_(status_groups[status_group]))

            def naive_utc(value: datetime) -> datetime:
                if value.tzinfo is None:
                    return value
                return value.astimezone(UTC).replace(tzinfo=None)

            if from_time is not None:
                query = query.where(CopyOrder.created_at >= naive_utc(from_time))
            if to_time is not None:
                query = query.where(CopyOrder.created_at <= naive_utc(to_time))
            if cursor:
                try:
                    cursor_time, cursor_id = decode_copy_order_cursor(cursor)
                except ValueError as error:
                    raise HTTPException(status_code=422, detail=str(error)) from error
                query = query.where(
                    or_(
                        CopyOrder.created_at < cursor_time,
                        and_(
                            CopyOrder.created_at == cursor_time,
                            CopyOrder.id < cursor_id,
                        ),
                    )
                )
            orders = list(
                (
                    await session.scalars(
                        query.order_by(CopyOrder.created_at.desc(), CopyOrder.id.desc()).limit(
                            limit + 1
                        )
                    )
                ).all()
            )
            has_more = len(orders) > limit
            page = orders[:limit]
            items = await workspace_order_reads(session, page)
            return CopyOrdersResponse(
                items=items,
                next_cursor=(encode_copy_order_cursor(page[-1]) if has_more and page else None),
            )

    @application.get(
        "/api/copy-trading/overview",
        response_model=CopyOverviewRead,
    )
    async def get_copy_workspace_overview(
        request: Request,
        tracked_wallet_id: Annotated[int | None, Query(gt=0)] = None,
    ) -> CopyOverviewRead:
        database: Database = request.app.state.database
        async with database.sessions() as session:
            subscriptions = list(
                (
                    await session.scalars(
                        select(CopySubscription).order_by(CopySubscription.id.asc())
                    )
                ).all()
            )
            wallets = (
                {
                    wallet.id: wallet
                    for wallet in list(
                        (
                            await session.scalars(
                                select(WatchedWallet).where(
                                    WatchedWallet.id.in_(
                                        [item.tracked_wallet_id for item in subscriptions]
                                    )
                                )
                            )
                        ).all()
                    )
                }
                if subscriptions
                else {}
            )
            wallet_by_subscription = {
                item.id: wallets[item.tracked_wallet_id]
                for item in subscriptions
                if item.tracked_wallet_id in wallets
            }
            subscription_ids = [item.id for item in subscriptions]
            positions = (
                list(
                    (
                        await session.scalars(
                            select(CopyPosition)
                            .where(CopyPosition.subscription_id.in_(subscription_ids))
                            .order_by(CopyPosition.updated_at.desc())
                        )
                    ).all()
                )
                if subscription_ids
                else []
            )
            valued_at = utcnow()
            position_items, global_portfolio = await workspace_position_reads(
                request, session, positions, wallet_by_subscription
            )
            items_by_subscription: dict[int, list[CopyWorkspacePositionRead]] = {}
            for item in position_items:
                items_by_subscription.setdefault(item.subscription_id, []).append(item)
            strategies: list[CopyStrategyOverviewRead] = []
            for subscription in subscriptions:
                wallet = wallet_by_subscription.get(subscription.id)
                if wallet is None:
                    continue
                strategy_positions = items_by_subscription.get(subscription.id, [])
                strategies.append(
                    CopyStrategyOverviewRead(
                        subscription=await copy_subscription_read(session, subscription),
                        wallet=CopyWalletSummaryRead.model_validate(wallet),
                        portfolio=workspace_portfolio(strategy_positions, valued_at),
                        lifetime_bought_usdc=sum(
                            (item.lifetime_bought_usdc for item in strategy_positions),
                            start=Decimal("0"),
                        ),
                        open_positions=sum(
                            1 for item in strategy_positions if item.attributed_size > 0
                        ),
                        stale=wallet_is_stale(wallet, request.app.state.settings),
                    )
                )
            recent_orders = list(
                (
                    await session.scalars(
                        select(CopyOrder)
                        .where(CopyOrder.source == "copy")
                        .order_by(CopyOrder.created_at.desc(), CopyOrder.id.desc())
                        .limit(8)
                    )
                ).all()
            )
            account = await session.get(ExecutionAccount, 1)
            collateral_balance = account.collateral_balance if account else None
            available_capacity = execution_account_capacity(account) if account else Decimal("0")
            open_exposure = sum(
                (item.subscription.open_exposure_usdc for item in strategies),
                start=Decimal("0"),
            )
            daily_bought = sum(
                (item.subscription.daily_bought_usdc for item in strategies),
                start=Decimal("0"),
            )
            return CopyOverviewRead(
                live_copy_enabled=request.app.state.settings.live_copy_enabled,
                account=execution_account_read(account),
                totals=CopyOverviewTotalsRead(
                    collateral_balance=collateral_balance,
                    available_capacity_usdc=available_capacity,
                    open_exposure_usdc=open_exposure,
                    daily_bought_usdc=daily_bought,
                    open_cost_usdc=global_portfolio.open_cost_usdc,
                    market_value_usdc=global_portfolio.market_value_usdc,
                    unrealized_pnl=global_portfolio.unrealized_pnl,
                    realized_pnl=global_portfolio.realized_pnl,
                    total_pnl=global_portfolio.total_pnl,
                    valuation_complete=global_portfolio.valuation_complete,
                    unpriced_positions=global_portfolio.unpriced_positions,
                ),
                daily_realized_pnl=await copy_daily_realized_pnl(
                    session,
                    reference_time=valued_at,
                    tracked_wallet_id=tracked_wallet_id,
                ),
                strategies=strategies,
                recent_orders=await workspace_order_reads(session, recent_orders),
                as_of=valued_at,
            )

    @application.get(
        "/api/copy-trading/dashboard",
        response_model=CopyDashboardRead,
    )
    async def get_copy_dashboard(
        request: Request,
        tracked_wallet_id: int = Query(gt=0),
    ) -> CopyDashboardRead:
        database: Database = request.app.state.database
        async with database.sessions() as session:
            subscription = await session.scalar(
                select(CopySubscription).where(
                    CopySubscription.tracked_wallet_id == tracked_wallet_id
                )
            )
            positions: list[CopyPosition] = []
            orders: list[CopyOrder] = []
            fill_totals: dict[int, dict[str, Decimal]] = {}
            subscription_response = None
            if subscription is not None:
                subscription_response = await copy_subscription_read(session, subscription)
                positions = list(
                    (
                        await session.scalars(
                            select(CopyPosition)
                            .where(CopyPosition.subscription_id == subscription.id)
                            .order_by(CopyPosition.updated_at.desc())
                        )
                    ).all()
                )
                orders = list(
                    (
                        await session.scalars(
                            select(CopyOrder)
                            .where(CopyOrder.subscription_id == subscription.id)
                            .order_by(CopyOrder.created_at.desc())
                            .limit(100)
                        )
                    ).all()
                )
                aggregate_rows = (
                    await session.execute(
                        select(
                            CopyOrder.copy_position_id,
                            CopyOrder.side,
                            func.sum(CopyFill.size),
                            func.sum(CopyFill.amount),
                        )
                        .join(CopyFill, CopyFill.order_id == CopyOrder.id)
                        .where(
                            CopyOrder.subscription_id == subscription.id,
                            CopyOrder.copy_position_id.is_not(None),
                        )
                        .group_by(CopyOrder.copy_position_id, CopyOrder.side)
                    )
                ).all()
                for position_id, side, size, amount in aggregate_rows:
                    totals = fill_totals.setdefault(
                        int(position_id),
                        {
                            "BUY_size": Decimal("0"),
                            "BUY_usdc": Decimal("0"),
                            "SELL_size": Decimal("0"),
                            "SELL_usdc": Decimal("0"),
                        },
                    )
                    totals[f"{side}_size"] = size or Decimal("0")
                    totals[f"{side}_usdc"] = amount or Decimal("0")
            open_positions = [item for item in positions if item.attributed_size > 0]
            bids = await copy_position_marks(request, positions)
            valued_at = utcnow()
            position_responses: list[CopyPositionRead] = []
            for position in positions:
                totals = fill_totals.get(
                    position.id,
                    {
                        "BUY_size": Decimal("0"),
                        "BUY_usdc": Decimal("0"),
                        "SELL_size": Decimal("0"),
                        "SELL_usdc": Decimal("0"),
                    },
                )
                lifetime_bought_size = totals["BUY_size"]
                lifetime_bought_usdc = totals["BUY_usdc"]
                if position.attributed_size <= 0 and lifetime_bought_size <= 0:
                    continue
                average_entry_price = (
                    position.attributed_cost / position.attributed_size
                    if position.attributed_size > 0
                    else None
                )
                lifetime_average_buy_price = (
                    lifetime_bought_usdc / lifetime_bought_size
                    if lifetime_bought_size > 0
                    else None
                )
                current_bid = bids.get(position.id) if position.attributed_size > 0 else None
                if position.attributed_size > 0 and current_bid is not None:
                    current_value = position.attributed_size * current_bid
                    unrealized_pnl = current_value - position.attributed_cost
                    unrealized_pnl_percent = (
                        unrealized_pnl / position.attributed_cost * Decimal("100")
                        if position.attributed_cost > 0
                        else None
                    )
                    total_pnl = unrealized_pnl + position.realized_pnl
                    valuation_status = "ok"
                    position_valued_at = valued_at
                elif position.attributed_size > 0:
                    current_value = None
                    unrealized_pnl = None
                    unrealized_pnl_percent = None
                    total_pnl = None
                    valuation_status = "unavailable"
                    position_valued_at = valued_at
                else:
                    current_value = None
                    unrealized_pnl = None
                    unrealized_pnl_percent = None
                    total_pnl = position.realized_pnl
                    valuation_status = "not_applicable"
                    position_valued_at = None
                position_responses.append(
                    CopyPositionRead.model_validate(position).model_copy(
                        update={
                            "average_entry_price": average_entry_price,
                            "current_bid": current_bid,
                            "current_value": current_value,
                            "unrealized_pnl": unrealized_pnl,
                            "unrealized_pnl_percent": unrealized_pnl_percent,
                            "total_pnl": total_pnl,
                            "lifetime_bought_size": lifetime_bought_size,
                            "lifetime_bought_usdc": lifetime_bought_usdc,
                            "lifetime_sold_size": totals["SELL_size"],
                            "lifetime_sold_usdc": totals["SELL_usdc"],
                            "lifetime_average_buy_price": lifetime_average_buy_price,
                            "valuation_status": valuation_status,
                            "valued_at": position_valued_at,
                        }
                    )
                )
            open_cost = sum(
                (position.attributed_cost for position in open_positions),
                start=Decimal("0"),
            )
            realized_pnl = sum(
                (position.realized_pnl for position in positions),
                start=Decimal("0"),
            )
            priced_values = [
                position.current_value
                for position in position_responses
                if position.attributed_size > 0 and position.current_value is not None
            ]
            unpriced_positions = sum(
                1
                for position in position_responses
                if position.attributed_size > 0 and position.current_value is None
            )
            valuation_complete = unpriced_positions == 0
            if valuation_complete:
                market_value = sum(priced_values, start=Decimal("0"))
                unrealized_pnl = market_value - open_cost
                total_pnl = unrealized_pnl + realized_pnl
            else:
                market_value = None
                unrealized_pnl = None
                total_pnl = None
            return CopyDashboardRead(
                live_copy_enabled=request.app.state.settings.live_copy_enabled,
                account=execution_account_read(await session.get(ExecutionAccount, 1)),
                subscription=subscription_response,
                positions=position_responses,
                orders=[CopyOrderRead.model_validate(item) for item in orders],
                portfolio=CopyPortfolioSummaryRead(
                    open_cost_usdc=open_cost,
                    market_value_usdc=market_value,
                    unrealized_pnl=unrealized_pnl,
                    realized_pnl=realized_pnl,
                    total_pnl=total_pnl,
                    valuation_complete=valuation_complete,
                    unpriced_positions=unpriced_positions,
                    valued_at=valued_at if open_positions else None,
                ),
            )

    @application.get("/api/wallets", response_model=list[WalletRead])
    async def list_wallets(request: Request) -> list[WatchedWallet]:
        database: Database = request.app.state.database
        async with database.sessions() as session:
            return list(
                (
                    await session.scalars(
                        select(WatchedWallet).order_by(WatchedWallet.created_at.asc())
                    )
                ).all()
            )

    @application.get("/api/my-wallet", response_model=WalletRead | None)
    async def get_my_wallet(request: Request) -> WatchedWallet | None:
        database: Database = request.app.state.database
        async with database.sessions() as session:
            return await session.scalar(
                select(WatchedWallet).where(WatchedWallet.wallet_role == "self")
            )

    @application.put("/api/my-wallet", response_model=WalletRead)
    async def set_my_wallet(
        payload: WalletCreate,
        request: Request,
    ) -> WatchedWallet:
        database: Database = request.app.state.database
        polymarket_client: PolymarketClient = request.app.state.polymarket_client
        monitor: WalletMonitor = request.app.state.monitor
        try:
            profile = await polymarket_client.resolve_profile(payload.address, payload.label)
        except InvalidWalletInput as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except PolymarketAPIError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

        now = utcnow()
        previous_self_id: int | None = None
        async with database.sessions() as session:
            current_self = await session.scalar(
                select(WatchedWallet).where(WatchedWallet.wallet_role == "self")
            )
            wallet = await session.scalar(
                select(WatchedWallet).where(WatchedWallet.proxy_wallet == profile.proxy_wallet)
            )

            if current_self is not None and current_self is not wallet:
                execution_account = await session.get(ExecutionAccount, 1)
                active_copy_position = await session.scalar(
                    select(CopyPosition.id)
                    .join(CopySubscription)
                    .where(CopyPosition.attributed_size > 0)
                    .limit(1)
                )
                active_copy_order = await session.scalar(
                    select(CopyOrder.id)
                    .where(CopyOrder.status.in_(["open", "partially_filled", "submitted"]))
                    .limit(1)
                )
                if (
                    execution_account is not None
                    and execution_account.wallet_id == current_self.id
                    and (active_copy_position is not None or active_copy_order is not None)
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="存在自动策略持仓或挂单，请先关闭策略并清仓后再更换执行钱包",
                    )
                previous_self_id = current_self.id
                open_periods = list(
                    (
                        await session.scalars(
                            select(PositionOverlapPeriod).where(
                                PositionOverlapPeriod.my_wallet_id == current_self.id,
                                PositionOverlapPeriod.ended_at.is_(None),
                            )
                        )
                    ).all()
                )
                for period in open_periods:
                    period.ended_at = now
                current_self.wallet_role = "tracked"
                current_self.enabled = False
                current_self.status = "disabled"
                current_self.updated_at = now
                await session.flush()

            if wallet is None:
                wallet = WatchedWallet(
                    address=profile.submitted_address,
                    proxy_wallet=profile.proxy_wallet,
                    label=profile.label,
                    wallet_role="self",
                    enabled=True,
                    baseline_established=False,
                    status="idle",
                    next_sync_at=now,
                    consecutive_failures=0,
                    created_at=now,
                    updated_at=now,
                )
                session.add(wallet)
                await session.flush()
            else:
                wallet.address = profile.submitted_address
                if payload.label is not None:
                    wallet.label = profile.label
                wallet.wallet_role = "self"
                wallet.enabled = True
                wallet.status = "idle"
                wallet.next_sync_at = now
                wallet.updated_at = now

            await session.commit()
            wallet_id = wallet.id

        if previous_self_id is not None:
            await monitor.close_overlap_periods(
                my_wallet_id=previous_self_id,
                ended_at=now,
            )
        await monitor.sync_wallet(wallet_id)
        async with database.sessions() as session:
            refreshed = await session.get(WatchedWallet, wallet_id)
            assert refreshed is not None
            return refreshed

    @application.post(
        "/api/wallets",
        response_model=WalletRead,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_wallet(payload: WalletCreate, request: Request) -> WatchedWallet:
        database: Database = request.app.state.database
        polymarket_client: PolymarketClient = request.app.state.polymarket_client
        monitor: WalletMonitor = request.app.state.monitor
        try:
            profile = await polymarket_client.resolve_profile(payload.address, payload.label)
        except InvalidWalletInput as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except PolymarketAPIError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

        now = utcnow()
        async with database.sessions() as session:
            wallet = await session.scalar(
                select(WatchedWallet).where(WatchedWallet.proxy_wallet == profile.proxy_wallet)
            )
            if wallet is None:
                wallet = WatchedWallet(
                    address=profile.submitted_address,
                    proxy_wallet=profile.proxy_wallet,
                    label=profile.label,
                    enabled=True,
                    baseline_established=False,
                    status="idle",
                    next_sync_at=now,
                    consecutive_failures=0,
                    created_at=now,
                    updated_at=now,
                )
                session.add(wallet)
                await session.flush()
            else:
                if wallet.wallet_role == "self":
                    raise HTTPException(
                        status_code=409,
                        detail="该地址已设置为我的钱包",
                    )
                wallet.address = profile.submitted_address
                if payload.label is not None:
                    wallet.label = profile.label
                wallet.enabled = True
                wallet.next_sync_at = now
                wallet.updated_at = now
            await session.commit()
            wallet_id = wallet.id

        # A newly added wallet should be useful as soon as POST returns. A remote API
        # failure is stored on the wallet and does not discard the user's watch entry.
        await monitor.sync_wallet(wallet_id)
        async with database.sessions() as session:
            refreshed = await session.get(WatchedWallet, wallet_id)
            assert refreshed is not None
            return refreshed

    @application.patch("/api/wallets/{wallet_id}", response_model=WalletRead)
    async def update_wallet(
        wallet_id: int,
        payload: WalletUpdate,
        request: Request,
    ) -> WatchedWallet:
        database: Database = request.app.state.database
        monitor: WalletMonitor = request.app.state.monitor
        now = utcnow()
        async with database.sessions() as session:
            wallet = await session.get(WatchedWallet, wallet_id)
            if wallet is None:
                raise HTTPException(status_code=404, detail="钱包不存在")
            if wallet.wallet_role == "self" and payload.enabled is False:
                raise HTTPException(
                    status_code=409,
                    detail="请先更换我的钱包，再停用该地址",
                )
            if payload.label is not None:
                cleaned = payload.label.strip()
                if not cleaned:
                    raise HTTPException(status_code=422, detail="钱包名称不能为空")
                wallet.label = cleaned
            if payload.enabled is not None:
                wallet.enabled = payload.enabled
                if payload.enabled:
                    wallet.next_sync_at = now
                    wallet.status = "idle"
                else:
                    open_periods = list(
                        (
                            await session.scalars(
                                select(PositionOverlapPeriod).where(
                                    PositionOverlapPeriod.tracked_wallet_id == wallet.id,
                                    PositionOverlapPeriod.ended_at.is_(None),
                                )
                            )
                        ).all()
                    )
                    for period in open_periods:
                        period.ended_at = now
                    wallet.status = "disabled"
            wallet.updated_at = now
            await session.commit()
            await session.refresh(wallet)
        if payload.enabled:
            monitor.wake()
        elif payload.enabled is False:
            await monitor.close_overlap_periods(
                tracked_wallet_id=wallet_id,
                ended_at=now,
            )
        return wallet

    @application.delete(
        "/api/wallets/{wallet_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def disable_wallet(wallet_id: int, request: Request) -> Response:
        database: Database = request.app.state.database
        monitor: WalletMonitor = request.app.state.monitor
        now = utcnow()
        async with database.sessions() as session:
            wallet = await session.get(WatchedWallet, wallet_id)
            if wallet is None:
                raise HTTPException(status_code=404, detail="钱包不存在")
            if wallet.wallet_role == "self":
                raise HTTPException(
                    status_code=409,
                    detail="请先更换我的钱包，再停用该地址",
                )
            open_periods = list(
                (
                    await session.scalars(
                        select(PositionOverlapPeriod).where(
                            PositionOverlapPeriod.tracked_wallet_id == wallet.id,
                            PositionOverlapPeriod.ended_at.is_(None),
                        )
                    )
                ).all()
            )
            for period in open_periods:
                period.ended_at = now
            wallet.enabled = False
            wallet.status = "disabled"
            wallet.updated_at = now
            await session.commit()
        await monitor.close_overlap_periods(
            tracked_wallet_id=wallet_id,
            ended_at=now,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @application.post("/api/wallets/{wallet_id}/sync", response_model=WalletRead)
    async def sync_wallet(wallet_id: int, request: Request) -> WatchedWallet:
        database: Database = request.app.state.database
        monitor: WalletMonitor = request.app.state.monitor
        async with database.sessions() as session:
            wallet = await session.get(WatchedWallet, wallet_id)
            if wallet is None:
                raise HTTPException(status_code=404, detail="钱包不存在")
            if not wallet.enabled:
                raise HTTPException(status_code=409, detail="钱包监控已停用")
        await monitor.sync_wallet(wallet_id)
        async with database.sessions() as session:
            refreshed = await session.get(WatchedWallet, wallet_id)
            assert refreshed is not None
            return refreshed

    @application.get("/api/positions", response_model=PositionsResponse)
    async def get_positions(
        request: Request,
        wallet_id: int = Query(gt=0),
    ) -> PositionsResponse:
        database: Database = request.app.state.database
        settings_for_request: Settings = request.app.state.settings
        async with database.sessions() as session:
            wallet = await session.get(WatchedWallet, wallet_id)
            if wallet is None:
                raise HTTPException(status_code=404, detail="钱包不存在")
            positions = list(
                (
                    await session.scalars(
                        select(CurrentPosition)
                        .where(
                            CurrentPosition.wallet_id == wallet_id,
                            CurrentPosition.size > 0,
                        )
                        .order_by(
                            CurrentPosition.current_value.desc(),
                            CurrentPosition.id.asc(),
                        )
                    )
                ).all()
            )
            asset_ids = [position.asset_id for position in positions]
            trades = (
                list(
                    (
                        await session.scalars(
                            select(WalletTrade)
                            .where(
                                WalletTrade.wallet_id == wallet_id,
                                WalletTrade.asset_id.in_(asset_ids),
                            )
                            .order_by(WalletTrade.timestamp.asc(), WalletTrade.id.asc())
                        )
                    ).all()
                )
                if asset_ids
                else []
            )
        purchase_lots_by_asset, lots_complete = build_purchase_lots(positions, trades)
        trades_by_asset: dict[str, list[WalletTrade]] = {}
        for trade in trades:
            trades_by_asset.setdefault(trade.asset_id, []).append(trade)
        position_items: list[PositionRead] = []
        opened_dates = set()
        purchase_dates = set()
        for position in positions:
            purchase_lots = [
                PurchaseLotRead.model_validate(lot)
                for lot in purchase_lots_by_asset.get(position.asset_id, [])
            ]
            purchase_dates.update(lot.purchase_date for lot in purchase_lots)
            cycle = active_position_cycle(position, trades_by_asset.get(position.asset_id, []))
            position_opened_date = opened_date(cycle.opened_at)
            opened_dates.add(position_opened_date)
            position_items.append(
                PositionRead.model_validate(position).model_copy(
                    update={
                        "first_opened_at": cycle.opened_at,
                        "first_opened_at_source": cycle.opened_at_source,
                        "opened_date": position_opened_date,
                        "cycle_trades": cycle_trade_reads(cycle),
                        "cycle_history_complete": cycle.complete,
                        "purchase_lots": purchase_lots,
                    }
                )
            )
        initial_value = sum((position.initial_value for position in positions), start=Decimal("0"))
        current_value = sum((position.current_value for position in positions), start=Decimal("0"))
        cash_pnl = sum((position.cash_pnl for position in positions), start=Decimal("0"))
        stale = wallet_is_stale(wallet, settings_for_request)
        return PositionsResponse(
            items=position_items,
            summary=PositionSummary(
                current_value=current_value,
                initial_value=initial_value,
                cash_pnl=cash_pnl,
                count=len(positions),
            ),
            opened_dates=sorted(opened_dates, reverse=True),
            purchase_dates=sorted(purchase_dates, reverse=True),
            purchase_history_complete=(
                wallet.trade_history_synced_at is not None
                and wallet.trade_history_error is None
                and lots_complete
            ),
            purchase_history_error=wallet.trade_history_error,
            as_of=wallet.last_success_at,
            stale=stale,
        )

    @application.get(
        "/api/position-overlaps",
        response_model=PositionOverlapsResponse,
    )
    async def get_position_overlaps(
        request: Request,
        tracked_wallet_id: int = Query(gt=0),
    ) -> PositionOverlapsResponse:
        database: Database = request.app.state.database
        settings_for_request: Settings = request.app.state.settings
        async with database.sessions() as session:
            tracked_wallet = await session.get(WatchedWallet, tracked_wallet_id)
            if tracked_wallet is None:
                raise HTTPException(status_code=404, detail="跟踪钱包不存在")
            if tracked_wallet.wallet_role != "tracked":
                raise HTTPException(status_code=422, detail="请选择一个跟踪钱包")

            my_wallet = await session.scalar(
                select(WatchedWallet).where(
                    WatchedWallet.wallet_role == "self",
                    WatchedWallet.enabled.is_(True),
                )
            )
            if my_wallet is None:
                return PositionOverlapsResponse(
                    my_wallet_id=None,
                    tracked_wallet_id=tracked_wallet.id,
                    items=[],
                    overlap_count=0,
                    my_as_of=None,
                    tracked_as_of=tracked_wallet.last_success_at,
                    my_stale=None,
                    tracked_stale=wallet_is_stale(
                        tracked_wallet,
                        settings_for_request,
                    ),
                )

            positions = list(
                (
                    await session.scalars(
                        select(CurrentPosition).where(
                            CurrentPosition.wallet_id.in_([my_wallet.id, tracked_wallet.id]),
                            CurrentPosition.size > 0,
                        )
                    )
                ).all()
            )

        mine = {
            position.asset_id: position
            for position in positions
            if position.wallet_id == my_wallet.id
        }
        tracked = {
            position.asset_id: position
            for position in positions
            if position.wallet_id == tracked_wallet.id
        }
        items: list[PositionOverlapRead] = []
        for asset_id in sorted(mine.keys() & tracked.keys()):
            my_position = mine[asset_id]
            tracked_position = tracked[asset_id]
            percent, my_ratio, tracked_ratio = position_ratio(
                my_position.size,
                tracked_position.size,
            )
            items.append(
                PositionOverlapRead(
                    asset_id=asset_id,
                    condition_id=tracked_position.condition_id,
                    my_size=my_position.size,
                    tracked_size=tracked_position.size,
                    my_to_tracked_percent=percent,
                    my_ratio=my_ratio,
                    tracked_ratio=tracked_ratio,
                )
            )

        return PositionOverlapsResponse(
            my_wallet_id=my_wallet.id,
            tracked_wallet_id=tracked_wallet.id,
            items=items,
            overlap_count=len(items),
            my_as_of=my_wallet.last_success_at,
            tracked_as_of=tracked_wallet.last_success_at,
            my_stale=wallet_is_stale(my_wallet, settings_for_request),
            tracked_stale=wallet_is_stale(tracked_wallet, settings_for_request),
        )

    @application.get(
        "/api/position-overlaps/{asset_id}",
        response_model=PositionOverlapDetail,
    )
    async def get_position_overlap_detail(
        asset_id: str,
        request: Request,
        tracked_wallet_id: int = Query(gt=0),
    ) -> PositionOverlapDetail:
        database: Database = request.app.state.database
        settings_for_request: Settings = request.app.state.settings
        async with database.sessions() as session:
            tracked_wallet = await session.get(WatchedWallet, tracked_wallet_id)
            if tracked_wallet is None:
                raise HTTPException(status_code=404, detail="跟踪钱包不存在")
            if tracked_wallet.wallet_role != "tracked":
                raise HTTPException(status_code=422, detail="请选择一个跟踪钱包")
            my_wallet = await session.scalar(
                select(WatchedWallet).where(
                    WatchedWallet.wallet_role == "self",
                    WatchedWallet.enabled.is_(True),
                )
            )
            if my_wallet is None:
                raise HTTPException(status_code=409, detail="请先设置我的钱包")

            matches = list(
                (
                    await session.scalars(
                        select(CurrentPosition).where(
                            CurrentPosition.wallet_id.in_([my_wallet.id, tracked_wallet.id]),
                            CurrentPosition.asset_id == asset_id,
                            CurrentPosition.size > 0,
                        )
                    )
                ).all()
            )
            mine = next(
                (position for position in matches if position.wallet_id == my_wallet.id),
                None,
            )
            tracked = next(
                (position for position in matches if position.wallet_id == tracked_wallet.id),
                None,
            )
            if mine is None or tracked is None:
                raise HTTPException(status_code=404, detail="共同持仓已不存在")

            mine_read = await position_read_with_lots(session, mine)
            tracked_read = await position_read_with_lots(session, tracked)

        percent, my_ratio, tracked_ratio = position_ratio(
            mine.size,
            tracked.size,
        )
        return PositionOverlapDetail(
            my_wallet=WalletRead.model_validate(my_wallet),
            tracked_wallet=WalletRead.model_validate(tracked_wallet),
            mine=mine_read,
            tracked=tracked_read,
            my_to_tracked_percent=percent,
            my_ratio=my_ratio,
            tracked_ratio=tracked_ratio,
            my_stale=wallet_is_stale(my_wallet, settings_for_request),
            tracked_stale=wallet_is_stale(tracked_wallet, settings_for_request),
        )

    @application.get(
        "/api/overlap-alerts",
        response_model=PositionOverlapAlertsResponse,
    )
    async def get_overlap_alerts(
        request: Request,
        tracked_wallet_id: int = Query(gt=0),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> PositionOverlapAlertsResponse:
        database: Database = request.app.state.database
        async with database.sessions() as session:
            tracked_wallet = await session.get(WatchedWallet, tracked_wallet_id)
            if tracked_wallet is None:
                raise HTTPException(status_code=404, detail="跟踪钱包不存在")
            if tracked_wallet.wallet_role != "tracked":
                raise HTTPException(status_code=422, detail="请选择一个跟踪钱包")
            my_wallet = await session.scalar(
                select(WatchedWallet).where(
                    WatchedWallet.wallet_role == "self",
                    WatchedWallet.enabled.is_(True),
                )
            )
            if my_wallet is None:
                return PositionOverlapAlertsResponse(items=[], unread_count=0)

            alerts = list(
                (
                    await session.scalars(
                        select(PositionOverlapAlert)
                        .options(
                            selectinload(PositionOverlapAlert.period),
                            selectinload(PositionOverlapAlert.event),
                        )
                        .where(
                            PositionOverlapAlert.my_wallet_id == my_wallet.id,
                            PositionOverlapAlert.tracked_wallet_id == tracked_wallet.id,
                        )
                        .order_by(PositionOverlapAlert.id.desc())
                        .limit(limit)
                    )
                ).all()
            )
            unread_count = (
                await session.scalar(
                    select(func.count(PositionOverlapAlert.id)).where(
                        PositionOverlapAlert.my_wallet_id == my_wallet.id,
                        PositionOverlapAlert.tracked_wallet_id == tracked_wallet.id,
                        PositionOverlapAlert.read_at.is_(None),
                    )
                )
            ) or 0
            ratio = await copy_ratio_percent(session)

        return PositionOverlapAlertsResponse(
            items=[overlap_alert_read(alert, ratio) for alert in alerts],
            unread_count=unread_count,
        )

    @application.post(
        "/api/overlap-alerts/{alert_id}/read",
        response_model=PositionOverlapAlertRead,
    )
    async def mark_overlap_alert_read(
        alert_id: int,
        request: Request,
    ) -> PositionOverlapAlertRead:
        database: Database = request.app.state.database
        async with database.sessions() as session:
            my_wallet = await session.scalar(
                select(WatchedWallet).where(
                    WatchedWallet.wallet_role == "self",
                    WatchedWallet.enabled.is_(True),
                )
            )
            alert = await session.scalar(
                select(PositionOverlapAlert)
                .options(
                    selectinload(PositionOverlapAlert.period),
                    selectinload(PositionOverlapAlert.event),
                )
                .where(PositionOverlapAlert.id == alert_id)
            )
            if my_wallet is None or alert is None or alert.my_wallet_id != my_wallet.id:
                raise HTTPException(status_code=404, detail="提醒不存在")
            if alert.read_at is None:
                alert.read_at = utcnow()
                await session.commit()
            ratio = await copy_ratio_percent(session)
            return overlap_alert_read(alert, ratio)

    @application.delete(
        "/api/overlap-alerts/{alert_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_read_overlap_alert(
        alert_id: int,
        request: Request,
    ) -> Response:
        database: Database = request.app.state.database
        async with database.sessions() as session:
            my_wallet = await session.scalar(
                select(WatchedWallet).where(
                    WatchedWallet.wallet_role == "self",
                    WatchedWallet.enabled.is_(True),
                )
            )
            alert = await session.scalar(
                select(PositionOverlapAlert).where(PositionOverlapAlert.id == alert_id)
            )
            if my_wallet is None or alert is None or alert.my_wallet_id != my_wallet.id:
                raise HTTPException(status_code=404, detail="提醒不存在")
            if alert.read_at is None:
                raise HTTPException(status_code=409, detail="请先将提醒标为已读")
            await session.delete(alert)
            await session.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @application.post(
        "/api/overlap-alerts/read-all",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def mark_all_overlap_alerts_read(
        request: Request,
        tracked_wallet_id: int = Query(gt=0),
    ) -> Response:
        database: Database = request.app.state.database
        async with database.sessions() as session:
            tracked_wallet = await session.get(WatchedWallet, tracked_wallet_id)
            if tracked_wallet is None:
                raise HTTPException(status_code=404, detail="跟踪钱包不存在")
            if tracked_wallet.wallet_role != "tracked":
                raise HTTPException(status_code=422, detail="请选择一个跟踪钱包")
            my_wallet = await session.scalar(
                select(WatchedWallet).where(
                    WatchedWallet.wallet_role == "self",
                    WatchedWallet.enabled.is_(True),
                )
            )
            if my_wallet is None:
                return Response(status_code=status.HTTP_204_NO_CONTENT)
            alerts = list(
                (
                    await session.scalars(
                        select(PositionOverlapAlert).where(
                            PositionOverlapAlert.my_wallet_id == my_wallet.id,
                            PositionOverlapAlert.tracked_wallet_id == tracked_wallet.id,
                            PositionOverlapAlert.read_at.is_(None),
                        )
                    )
                ).all()
            )
            read_at = utcnow()
            for alert in alerts:
                alert.read_at = read_at
            await session.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @application.get(
        "/api/position-event-groups",
        response_model=PositionEventGroupsResponse,
    )
    async def get_position_event_groups(
        request: Request,
        wallet_id: int = Query(gt=0),
        cursor: str | None = Query(default=None, min_length=1, max_length=240),
        limit: int = Query(default=20, ge=1, le=50),
    ) -> PositionEventGroupsResponse:
        database: Database = request.app.state.database
        async with database.sessions() as session:
            wallet = await session.get(WatchedWallet, wallet_id)
            if wallet is None:
                raise HTTPException(status_code=404, detail="钱包不存在")
            events = list(
                (
                    await session.scalars(
                        select(PositionEvent)
                        .options(selectinload(PositionEvent.fills))
                        .where(PositionEvent.wallet_id == wallet_id)
                        .order_by(PositionEvent.settled_at.asc(), PositionEvent.id.asc())
                    )
                ).all()
            )
            positions = list(
                (
                    await session.scalars(
                        select(CurrentPosition).where(CurrentPosition.wallet_id == wallet_id)
                    )
                ).all()
            )
            ratio = await copy_ratio_percent(session)

        official_closed_positions = []
        official_pnl_available = False
        try:
            official_closed_positions = (
                await request.app.state.polymarket_client.fetch_closed_positions(
                    wallet.proxy_wallet
                )
            )
            official_pnl_available = True
        except PolymarketAPIError:
            pass

        events_by_asset: dict[str, list[PositionEvent]] = {}
        for event in events:
            events_by_asset.setdefault(event.asset_id, []).append(event)
        open_assets = {position.asset_id for position in positions}
        groups = [
            position_event_group_read(
                asset_events,
                ratio_percent=ratio,
                is_current=asset_id in open_assets,
            )
            for asset_id, asset_events in events_by_asset.items()
        ]
        positions_by_asset = {position.asset_id: position for position in positions}
        official_closed_by_asset = {
            position.asset_id: position for position in official_closed_positions
        }
        if official_pnl_available:
            groups = [
                group.model_copy(
                    update={
                        "confirmed_realized_pnl": official_closed_by_asset[
                            group.asset_id
                        ].realized_pnl,
                        "incomplete_profit_events": 0,
                        "realized_pnl_source": "polymarket",
                        "realized_pnl_status": "confirmed",
                    }
                )
                if group.asset_id in official_closed_by_asset
                else group.model_copy(
                    update={
                        "confirmed_realized_pnl": positions_by_asset[group.asset_id].realized_pnl,
                        "incomplete_profit_events": 0,
                        "realized_pnl_source": "polymarket",
                        "realized_pnl_status": (
                            "confirmed"
                            if positions_by_asset[group.asset_id].realized_pnl != 0
                            or any(
                                group.event_counts[event_type] > 0
                                for event_type in ("decreased", "closed", "redeemed")
                            )
                            else "unrealized"
                        ),
                    }
                )
                if group.asset_id in positions_by_asset
                else group
                for group in groups
            ]
        groups.sort(
            key=lambda group: (
                group.latest_recorded_at,
                group.latest_event_id,
                group.asset_id,
            ),
            reverse=True,
        )
        if official_pnl_available:
            confirmed_realized_pnl = sum(
                (position.realized_pnl for position in official_closed_by_asset.values()),
                start=Decimal("0"),
            ) + sum(
                (position.realized_pnl for position in positions_by_asset.values()),
                start=Decimal("0"),
            )
            incomplete_realized_events = 0
        else:
            confirmed_realized_pnl = sum(
                (group.confirmed_realized_pnl for group in groups),
                start=Decimal("0"),
            )
            incomplete_realized_events = sum(group.incomplete_profit_events for group in groups)
        if cursor is not None:
            try:
                cursor_key = decode_position_group_cursor(cursor)
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            groups = [
                group
                for group in groups
                if (
                    group.latest_recorded_at,
                    group.latest_event_id,
                    group.asset_id,
                )
                < cursor_key
            ]

        has_more = len(groups) > limit
        page = groups[:limit]
        current_unrealized_pnl = sum(
            (position.cash_pnl for position in positions),
            start=Decimal("0"),
        )
        return PositionEventGroupsResponse(
            items=page,
            pnl=WalletRecordedPnlRead(
                recorded_since=wallet.created_at,
                confirmed_realized_pnl=confirmed_realized_pnl,
                current_unrealized_pnl=current_unrealized_pnl,
                confirmed_total_pnl=confirmed_realized_pnl + current_unrealized_pnl,
                incomplete_realized_events=incomplete_realized_events,
                complete=incomplete_realized_events == 0,
                source="polymarket" if official_pnl_available else "recorded",
            ),
            next_cursor=(encode_position_group_cursor(page[-1]) if has_more and page else None),
        )

    @application.get("/api/position-events", response_model=EventsResponse)
    async def get_position_events(
        request: Request,
        wallet_id: int = Query(gt=0),
        cursor: str | None = Query(default=None, min_length=1, max_length=80),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> EventsResponse:
        database: Database = request.app.state.database
        async with database.sessions() as session:
            if await session.get(WatchedWallet, wallet_id) is None:
                raise HTTPException(status_code=404, detail="钱包不存在")
            query = (
                select(PositionEvent)
                .options(selectinload(PositionEvent.fills))
                .where(PositionEvent.wallet_id == wallet_id)
                .order_by(
                    PositionEvent.settled_at.desc(),
                    PositionEvent.id.desc(),
                )
                .limit(limit + 1)
            )
            if cursor is not None:
                try:
                    cursor_timestamp, cursor_id = decode_event_cursor(cursor)
                except ValueError as error:
                    raise HTTPException(status_code=422, detail=str(error)) from error
                query = query.where(
                    or_(
                        PositionEvent.settled_at < cursor_timestamp,
                        and_(
                            PositionEvent.settled_at == cursor_timestamp,
                            PositionEvent.id < cursor_id,
                        ),
                    )
                )
            events = list((await session.scalars(query)).all())
            ratio = await copy_ratio_percent(session)
        has_more = len(events) > limit
        page = events[:limit]
        return EventsResponse(
            items=[event_read(event, ratio) for event in page],
            next_cursor=encode_event_cursor(page[-1]) if has_more and page else None,
        )

    @application.get("/api/stream")
    async def stream_events(request: Request) -> StreamingResponse:
        broker: EventBroker = request.app.state.broker

        async def event_stream():
            yield ": connected\n\n"
            async with broker.subscribe() as queue:
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        message: dict[str, Any] = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield f"data: {json.dumps(message, separators=(',', ':'))}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    return application


app = create_app()
