from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from backend.broker import EventBroker
from backend.config import Settings
from backend.db import Database
from backend.models import CurrentPosition, PositionEvent, WalletTrade, WatchedWallet
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
    PositionRead,
    PositionsResponse,
    PositionSummary,
    PurchaseLotRead,
    WalletCreate,
    WalletRead,
    WalletUpdate,
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
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
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
                    wallet.status = "disabled"
            wallet.updated_at = now
            await session.commit()
            await session.refresh(wallet)
        if payload.enabled:
            monitor.wake()
        return wallet

    @application.delete(
        "/api/wallets/{wallet_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def disable_wallet(wallet_id: int, request: Request) -> Response:
        database: Database = request.app.state.database
        async with database.sessions() as session:
            wallet = await session.get(WatchedWallet, wallet_id)
            if wallet is None:
                raise HTTPException(status_code=404, detail="钱包不存在")
            wallet.enabled = False
            wallet.status = "disabled"
            wallet.updated_at = utcnow()
            await session.commit()
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
        position_items: list[PositionRead] = []
        purchase_dates = set()
        for position in positions:
            purchase_lots = [
                PurchaseLotRead.model_validate(lot)
                for lot in purchase_lots_by_asset.get(position.asset_id, [])
            ]
            purchase_dates.update(lot.purchase_date for lot in purchase_lots)
            position_items.append(
                PositionRead.model_validate(position).model_copy(
                    update={"purchase_lots": purchase_lots}
                )
            )
        initial_value = sum((position.initial_value for position in positions), start=Decimal("0"))
        current_value = sum((position.current_value for position in positions), start=Decimal("0"))
        cash_pnl = sum((position.cash_pnl for position in positions), start=Decimal("0"))
        stale_after = timedelta(seconds=max(30.0, settings_for_request.poll_interval_seconds * 2))
        stale = (
            wallet.last_success_at is None
            or wallet.status == "error"
            or utcnow() - wallet.last_success_at > stale_after
        )
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

    @application.get("/api/position-events", response_model=EventsResponse)
    async def get_position_events(
        request: Request,
        wallet_id: int = Query(gt=0),
        cursor: int | None = Query(default=None, gt=0),
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
                .order_by(PositionEvent.id.desc())
                .limit(limit + 1)
            )
            if cursor is not None:
                query = query.where(PositionEvent.id < cursor)
            events = list((await session.scalars(query)).all())
        has_more = len(events) > limit
        page = events[:limit]
        return EventsResponse(
            items=[EventRead.model_validate(event) for event in page],
            next_cursor=page[-1].id if has_more and page else None,
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
