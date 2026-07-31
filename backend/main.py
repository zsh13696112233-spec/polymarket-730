from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import selectinload

from backend.broker import EventBroker
from backend.config import Settings
from backend.db import Database
from backend.models import (
    CurrentPosition,
    PositionEvent,
    PositionOverlapAlert,
    PositionOverlapPeriod,
    WalletTrade,
    WatchedWallet,
)
from backend.monitor import WalletMonitor, utcnow
from backend.polymarket import (
    InvalidWalletInput,
    PolymarketAPIError,
    PolymarketClient,
)
from backend.purchase_history import build_purchase_lots
from backend.schemas import (
    EventRead,
    EventsResponse,
    HealthRead,
    PositionOverlapAlertRead,
    PositionOverlapAlertsResponse,
    PositionOverlapDetail,
    PositionOverlapRead,
    PositionOverlapsResponse,
    PositionRead,
    PositionsResponse,
    PositionSummary,
    PurchaseLotRead,
    WalletCreate,
    WalletRead,
    WalletUpdate,
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


def event_read(event: PositionEvent) -> EventRead:
    item = EventRead.model_validate(event)
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
    first_opened_at, first_opened_at_source = first_opened_metadata(position, trades)
    return PositionRead.model_validate(position).model_copy(
        update={
            "first_opened_at": first_opened_at,
            "first_opened_at_source": first_opened_at_source,
            "purchase_lots": lots,
        }
    )


def first_opened_metadata(
    position: CurrentPosition,
    trades: list[WalletTrade],
) -> tuple[datetime, str]:
    first_buy = min(
        (trade.timestamp for trade in trades if trade.side == "BUY"),
        default=None,
    )
    if first_buy is not None:
        return first_buy, "trade"
    return position.first_seen_at, "first_seen"


def overlap_alert_read(alert: PositionOverlapAlert) -> PositionOverlapAlertRead:
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
            timeout=resolved_settings.request_timeout_seconds,
        )
        broker = EventBroker()
        monitor = WalletMonitor(
            database=database,
            client=polymarket_client,
            broker=broker,
            settings=resolved_settings,
        )
        application.state.settings = resolved_settings
        application.state.database = database
        application.state.polymarket_client = polymarket_client
        application.state.broker = broker
        application.state.monitor = monitor
        if resolved_settings.start_monitor:
            monitor.start()
        try:
            yield
        finally:
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
        purchase_dates = set()
        for position in positions:
            purchase_lots = [
                PurchaseLotRead.model_validate(lot)
                for lot in purchase_lots_by_asset.get(position.asset_id, [])
            ]
            purchase_dates.update(lot.purchase_date for lot in purchase_lots)
            first_opened_at, first_opened_at_source = first_opened_metadata(
                position,
                trades_by_asset.get(position.asset_id, []),
            )
            position_items.append(
                PositionRead.model_validate(position).model_copy(
                    update={
                        "first_opened_at": first_opened_at,
                        "first_opened_at_source": first_opened_at_source,
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

        return PositionOverlapAlertsResponse(
            items=[overlap_alert_read(alert) for alert in alerts],
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
            return overlap_alert_read(alert)

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
        has_more = len(events) > limit
        page = events[:limit]
        return EventsResponse(
            items=[event_read(event) for event in page],
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
