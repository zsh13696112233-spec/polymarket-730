from __future__ import annotations

import asyncio
import json
import secrets
import smtplib
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from backend.config import Settings
from backend.db import Database
from backend.home import home_overview
from backend.keychain import KeychainError, KeychainReference, MacOSKeychain
from backend.models import (
    EmailRecipient,
    EmailSettings,
    ExecutionAccount,
    WhaleEmailDelivery,
    WhaleEntry,
    WhaleEntryRuleState,
    WhaleExclusion,
    WhaleOrder,
    WhaleSettings,
    WhaleTag,
)
from backend.polymarket import (
    InvalidWalletInput,
    PolymarketAPIError,
    PolymarketClient,
    parse_wallet_input,
)
from backend.schemas import (
    ChainTestBuyPreviewRequest,
    ChainTestMarketRead,
    ChainTestResolveRead,
    ChainTestResolveRequest,
    EmailDeliveryListRead,
    EmailSettingsRead,
    EmailSettingsUpdate,
    EmailTestRead,
    EmailTestRequest,
    ExecutionAccountRead,
    ExecutionAccountUpdate,
    HealthRead,
    HomeOverviewRead,
    WalletCancelExecuteRequest,
    WalletCancelPreviewRead,
    WalletOrderRead,
    WalletPositionsRead,
    WalletSellPreviewRead,
    WalletSellPreviewRequest,
    WhaleAutoDecisionListRead,
    WhaleExclusionCreate,
    WhaleExclusionListRead,
    WhaleExclusionRead,
    WhaleFollowExecuteRequest,
    WhaleFollowPreviewRead,
    WhaleFollowPreviewRequest,
    WhaleHistoryListRead,
    WhaleMarketDetailRead,
    WhaleMarketListRead,
    WhaleOrderRead,
    WhalePositionDetailRead,
    WhalePositionListRead,
    WhaleRecordListRead,
    WhaleRequestLogListRead,
    WhaleRequestLogRead,
    WhaleScanRead,
    WhaleScanRunListRead,
    WhaleScanRunRead,
    WhaleSellExecuteRequest,
    WhaleSellPreviewRead,
    WhaleSellPreviewRequest,
    WhaleSettingsRead,
    WhaleSettingsUpdate,
    WhaleStatisticsRead,
    WhaleStatisticsSignalListRead,
    WhaleTagRead,
)
from backend.time_utils import utcnow
from backend.trading import (
    V2_EXCHANGE_ADDRESS,
    V2_NEG_RISK_EXCHANGE_ADDRESS,
    TradingUnavailable,
    UnifiedPolymarketTrader,
)
from backend.trading_cli import DEFAULT_SERVICE
from backend.whale import (
    WALLET_PENDING_STATUSES,
    WhaleDiscoveryScanner,
    WhaleFollowExecutor,
    list_whale_auto_decisions,
    list_whale_exclusions,
    list_whale_history,
    list_whale_markets,
    list_whale_positions,
    list_whale_records,
    list_whale_scan_runs,
    list_whale_statistics_signals,
    whale_order_payload,
    whale_position_detail,
    whale_settings_read,
    whale_statistics,
)
from backend.whale_email import (
    SMTP_KEYCHAIN_SERVICE,
    WhaleEmailNotifier,
    list_whale_email_deliveries,
    next_weekly_summary_run,
)
from backend.whale_requests import WhaleRequestMonitor


def add_smtp_configuration_state(values: dict[str, Any], settings: Settings) -> None:
    if not values.get("smtp_host") and settings.smtp_host:
        values.update(
            {
                "smtp_host": settings.smtp_host,
                "smtp_port": settings.smtp_port,
                "smtp_security": settings.smtp_security,
                "smtp_username": settings.smtp_username,
                "smtp_from_email": settings.smtp_from_email,
                "smtp_from_name": settings.smtp_from_name,
            }
        )
    keychain_configured = bool(
        values.get("smtp_keychain_service") and values.get("smtp_keychain_account")
    )
    authorization_configured = keychain_configured or bool(settings.smtp_password)
    values["smtp_authorization_code_configured"] = authorization_configured
    values["smtp_configured"] = bool(
        (values.get("smtp_host") or settings.smtp_host)
        and (values.get("smtp_username") or settings.smtp_username)
        and (values.get("smtp_from_email") or settings.smtp_from_email)
        and authorization_configured
    )


async def email_settings_read(database: Database, settings: Settings) -> dict[str, Any]:
    async with database.sessions() as session:
        row = await session.get(EmailSettings, 1)
        if row is None:
            raise ValueError("邮件设置尚未初始化")
        values = {
            column.name: getattr(row, column.name) for column in EmailSettings.__table__.columns
        }
        values["notification_recipients"] = list(
            await session.scalars(
                select(EmailRecipient.email)
                .where(EmailRecipient.enabled.is_(True))
                .order_by(EmailRecipient.email)
            )
        )
        values["weekly_summary_last_sent_at"] = await session.scalar(
            select(func.max(WhaleEmailDelivery.sent_at)).where(
                WhaleEmailDelivery.notification_kind == "weekly_summary",
                WhaleEmailDelivery.status == "sent",
            )
        )
        values["weekly_summary_next_run_at"] = (
            next_weekly_summary_run(utcnow()) if row.weekly_summary_enabled else None
        )
    add_smtp_configuration_state(values, settings)
    return values


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
        async with database.sessions() as session:
            if await session.get(EmailSettings, 1) is None:
                now = utcnow()
                session.add(
                    EmailSettings(
                        id=1,
                        smtp_host=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
                await session.commit()
        owns_client = client is None
        polymarket_client = client or PolymarketClient(
            data_api_url=resolved_settings.data_api_url,
            gamma_api_url=resolved_settings.gamma_api_url,
            clob_api_url=resolved_settings.clob_api_url,
            timeout=resolved_settings.request_timeout_seconds,
            proxy_url=resolved_settings.proxy_url,
            data_api_concurrency=resolved_settings.data_api_concurrency,
            gamma_api_concurrency=resolved_settings.gamma_api_concurrency,
            clob_api_concurrency=resolved_settings.clob_api_concurrency,
        )
        whale_request_monitor = WhaleRequestMonitor(
            capacity=100,
            failure_log_path=resolved_settings.whale_failure_log_path,
        )
        keychain = MacOSKeychain()
        whale_executor = WhaleFollowExecutor(
            database=database,
            client=polymarket_client,
            settings=resolved_settings,
            keychain=keychain,
        )
        whale_email_notifier = WhaleEmailNotifier(
            database=database,
            settings=resolved_settings,
            keychain=keychain,
        )
        whale_scanner = WhaleDiscoveryScanner(
            database=database,
            client=polymarket_client,
            settings=resolved_settings,
            executor=whale_executor,
            request_monitor=whale_request_monitor,
            email_notifier=whale_email_notifier,
        )
        application.state.settings = resolved_settings
        application.state.database = database
        application.state.polymarket_client = polymarket_client
        application.state.keychain = keychain
        application.state.whale_executor = whale_executor
        application.state.whale_scanner = whale_scanner
        application.state.whale_email_notifier = whale_email_notifier
        application.state.whale_request_monitor = whale_request_monitor
        application.state.whale_follow_previews = {}
        application.state.whale_sell_previews = {}
        application.state.wallet_previews = {}
        application.state.chain_test_resolutions = {}
        application.state.chain_test_buy_previews = {}
        application.state.chain_test_sell_previews = {}
        if resolved_settings.start_monitor:
            whale_email_notifier.start()
            if resolved_settings.whale_enabled:
                whale_scanner.start()

        async def reconcile_wallet_orders() -> None:
            while True:
                await asyncio.sleep(10)
                try:
                    # Only resume orders already authorized by the user, even with scanning off.
                    async with database.sessions() as session:
                        pending = await session.scalar(
                            select(WhaleOrder.id)
                            .where(
                                WhaleOrder.source == "wallet_manual",
                                WhaleOrder.status.in_(
                                    [
                                        "submitted",
                                        "live",
                                        "matched",
                                        "partially_filled_live",
                                        "reconciliation_pending",
                                        "delayed",
                                    ]
                                ),
                                WhaleOrder.external_order_id.is_not(None),
                            )
                            .limit(1)
                        )
                    if pending is not None:
                        await whale_executor.reconcile_pending_orders()
                except Exception:
                    # Keep reservations intact; the next page refresh reports upstream errors.
                    continue

        reconciliation_task = asyncio.create_task(reconcile_wallet_orders())
        try:
            yield
        finally:
            reconciliation_task.cancel()
            with suppress(asyncio.CancelledError):
                await reconciliation_task
            await whale_scanner.stop()
            await whale_email_notifier.stop()
            await whale_executor.close()
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

    retired_prefixes = (
        "/api/copy-trading",
        "/api/wallets",
        "/api/my-wallet",
        "/api/positions",
        "/api/position-events",
        "/api/position-event-groups",
        "/api/position-overlaps",
        "/api/overlap-alerts",
        "/api/settings",
        "/api/stream",
    )

    @application.middleware("http")
    async def reject_retired_fixed_wallet_api(request: Request, call_next: Any) -> Response:
        if any(request.url.path.startswith(prefix) for prefix in retired_prefixes):
            return JSONResponse(status_code=404, content={"detail": "固定钱包监控与跟单功能已移除"})
        return await call_next(request)

    original_openapi = application.openapi

    def active_openapi() -> dict[str, Any]:
        schema = original_openapi()
        schema["paths"] = {
            path: definition
            for path, definition in schema.get("paths", {}).items()
            if not any(path.startswith(prefix) for prefix in retired_prefixes)
        }
        return schema

    application.openapi = active_openapi  # type: ignore[method-assign]

    @application.get("/healthz", response_model=HealthRead)
    async def health(request: Request) -> HealthRead:
        database: Database = request.app.state.database
        async with database.sessions() as session:
            await session.execute(text("SELECT 1"))
        return HealthRead(status="ok", database="ok")

    async def verify_trade_account(request: Request) -> ExecutionAccount:
        database: Database = request.app.state.database
        keychain: MacOSKeychain = request.app.state.keychain
        async with database.sessions() as session:
            account = await session.get(ExecutionAccount, 1)
            if account is None or not account.keychain_service or not account.keychain_account:
                raise HTTPException(status_code=409, detail="请先配置执行账户和钥匙串密钥")
            if account.signature_type != 3:
                raise HTTPException(
                    status_code=409,
                    detail="执行钱包仅支持 Deposit Wallet（signature_type=3），请重新保存配置",
                )
            trader = UnifiedPolymarketTrader(
                host=resolved_settings.clob_api_url,
                keychain=keychain,
                key_reference=KeychainReference(
                    service=account.keychain_service,
                    account=account.keychain_account,
                ),
                signature_type=account.signature_type,
                funder_address=account.funder_address,
                relayer_url=resolved_settings.relayer_api_url,
                rpc_url=resolved_settings.polygon_rpc_url,
                proxy_url=resolved_settings.proxy_url,
            )
            try:
                await trader.ensure_ready_approvals()
                signer_address, wallet_type, balance, allowances = await asyncio.gather(
                    trader.signer_address(),
                    trader.wallet_type(),
                    trader.collateral_balance(),
                    trader.collateral_allowances(),
                )
                if signer_address != (account.signer_address or "").lower():
                    raise TradingUnavailable("钥匙串私钥与配置的签名地址不一致")
                if wallet_type != "DEPOSIT_WALLET":
                    raise TradingUnavailable("SDK 钱包类型与 signature_type 配置不一致")
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
            finally:
                await trader.close()
            account.collateral_balance = balance
            account.last_balance_at = utcnow()
            account.status = (
                "ready" if balance > account.cash_reserve_usdc else "insufficient_balance"
            )
            account.last_error = None
            account.updated_at = utcnow()
            await session.commit()
            return account

    @application.get("/api/execution-account", response_model=ExecutionAccountRead | None)
    async def get_trade_account(request: Request) -> ExecutionAccountRead | None:
        database: Database = request.app.state.database
        async with database.sessions() as session:
            return execution_account_read(await session.get(ExecutionAccount, 1))

    @application.put("/api/execution-account", response_model=ExecutionAccountRead)
    async def configure_trade_account(
        payload: ExecutionAccountUpdate,
        request: Request,
    ) -> ExecutionAccountRead:
        signer = payload.signer_address.lower()
        funder = payload.funder_address.lower()
        if any(len(value) != 42 or not value.startswith("0x") for value in (signer, funder)):
            raise HTTPException(status_code=422, detail="钱包地址无效")
        async with request.app.state.whale_executor._lock:
            reference = KeychainReference(service=DEFAULT_SERVICE, account=signer)
            try:
                await asyncio.to_thread(request.app.state.keychain.get_secret, reference)
                account_status = "configured"
            except KeychainError:
                account_status = "missing_key"
            database: Database = request.app.state.database
            async with database.sessions() as session:
                account = await session.get(ExecutionAccount, 1)
                if account is not None and (
                    account.funder_address != funder or account.signer_address != signer
                ):
                    pending = await session.scalar(
                        select(WhaleOrder.id)
                        .where(
                            WhaleOrder.status.in_(WALLET_PENDING_STATUSES),
                        )
                        .limit(1)
                    )
                    if pending is not None:
                        raise HTTPException(
                            status_code=409,
                            detail="存在未完成订单，请先完成撤单或对账再切换执行钱包",
                        )
                now = utcnow()
                if account is None:
                    account = ExecutionAccount(id=1, created_at=now, updated_at=now)
                    session.add(account)
                for field, value in payload.model_dump().items():
                    setattr(account, field, value)
                account.signer_address = signer
                account.funder_address = funder
                account.keychain_service = reference.service
                account.keychain_account = reference.account
                account.status = account_status
                account.collateral_balance = None
                account.last_balance_at = None
                account.last_error = None
                account.updated_at = now
                await session.commit()
                result = execution_account_read(account)
                assert result is not None
                return result

    @application.post("/api/execution-account/verify", response_model=ExecutionAccountRead)
    async def verify_trade_account_endpoint(request: Request) -> ExecutionAccountRead:
        result = execution_account_read(await verify_trade_account(request))
        assert result is not None
        return result

    @application.post("/api/execution-account/balance/refresh", response_model=ExecutionAccountRead)
    async def refresh_trade_account(request: Request) -> ExecutionAccountRead:
        try:
            await request.app.state.whale_executor.refresh_balance()
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        database: Database = request.app.state.database
        async with database.sessions() as session:
            result = execution_account_read(await session.get(ExecutionAccount, 1))
            assert result is not None
            return result

    @application.post(
        "/api/execution-account/chain-test/resolve",
        response_model=ChainTestResolveRead,
    )
    async def resolve_chain_test_market(
        payload: ChainTestResolveRequest,
        request: Request,
    ) -> ChainTestResolveRead:
        try:
            resolution = await request.app.state.polymarket_client.resolve_market_url(
                payload.market_url
            )
        except InvalidWalletInput as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except PolymarketAPIError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        resolution_id = secrets.token_urlsafe(32)
        expires_at = utcnow() + timedelta(minutes=10)
        request.app.state.chain_test_resolutions[resolution_id] = {
            "resolution": resolution,
            "expires_at": expires_at,
        }
        markets = []
        for market in resolution.markets:
            markets.append(
                ChainTestMarketRead.model_validate(
                    {
                        "condition_id": market.condition_id,
                        "title": market.title,
                        "market_slug": market.market_slug,
                        "event_slug": market.event_slug,
                        "closed": market.closed,
                        "active": market.active,
                        "accepting_orders": market.accepting_orders,
                        "outcomes": [
                            {
                                "asset_id": asset_id,
                                "label": (
                                    market.outcomes[index]
                                    if index < len(market.outcomes)
                                    else f"Outcome {index + 1}"
                                ),
                                "outcome_index": index,
                                "reference_price": (
                                    market.outcome_prices[index]
                                    if index < len(market.outcome_prices)
                                    else Decimal("0")
                                ),
                            }
                            for index, asset_id in enumerate(market.clob_token_ids)
                        ],
                    }
                )
            )
        return ChainTestResolveRead.model_validate(
            {
                "resolution_id": resolution_id,
                "expires_at": expires_at,
                "market_url": resolution.market_url,
                "event_title": resolution.event_title,
                "markets": markets,
            }
        )

    @application.post(
        "/api/execution-account/chain-test/buy/preview",
        response_model=WhaleFollowPreviewRead,
    )
    async def preview_chain_test_buy(
        payload: ChainTestBuyPreviewRequest,
        request: Request,
    ) -> WhaleFollowPreviewRead:
        stored = request.app.state.chain_test_resolutions.get(payload.resolution_id)
        if stored is None or stored["expires_at"] < utcnow():
            request.app.state.chain_test_resolutions.pop(payload.resolution_id, None)
            raise HTTPException(status_code=409, detail="市场解析结果已失效，请重新解析链接")
        selected_market = next(
            (
                market
                for market in stored["resolution"].markets
                if payload.asset_id in market.clob_token_ids
            ),
            None,
        )
        if selected_market is None:
            raise HTTPException(status_code=409, detail="所选 outcome 与市场解析结果不匹配")
        try:
            quote = await request.app.state.whale_executor.quote_chain_test_buy(
                market=selected_market,
                asset_id=payload.asset_id,
                amount_usdc=payload.amount_usdc,
            )
        except ValueError as error:
            raise whale_value_error(error, preview=True) from error
        except (PolymarketAPIError, TradingUnavailable) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        confirmation_id = secrets.token_urlsafe(32)
        expires_at = utcnow() + timedelta(minutes=5)
        request.app.state.chain_test_buy_previews[confirmation_id] = {
            "quote": quote,
            "expires_at": expires_at,
        }
        return WhaleFollowPreviewRead.model_validate(
            {"confirmation_id": confirmation_id, "expires_at": expires_at, **asdict(quote)}
        )

    @application.post(
        "/api/execution-account/chain-test/buy/execute",
        response_model=WhaleOrderRead,
    )
    async def execute_chain_test_buy(
        payload: WhaleFollowExecuteRequest,
        request: Request,
    ) -> WhaleOrderRead:
        stored = request.app.state.chain_test_buy_previews.pop(payload.confirmation_id, None)
        if stored is None:
            raise HTTPException(status_code=409, detail="测试买入确认已失效，请重新预览")
        if stored["expires_at"] < utcnow():
            raise HTTPException(status_code=409, detail="测试买入确认已过期，请重新预览")
        try:
            order_id = await request.app.state.whale_executor.execute_follow(
                stored["quote"], payload.confirmation_id, order_source="chain_test"
            )
        except ValueError as error:
            raise whale_value_error(error) from error
        except (PolymarketAPIError, TradingUnavailable) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        database: Database = request.app.state.database
        async with database.sessions() as session:
            order = await session.scalar(
                select(WhaleOrder)
                .options(selectinload(WhaleOrder.fills))
                .where(WhaleOrder.id == order_id)
            )
            assert order is not None
            return WhaleOrderRead.model_validate(whale_order_payload(order))

    @application.post(
        "/api/execution-account/chain-test/orders/{buy_order_id}/sell/preview",
        response_model=WhaleSellPreviewRead,
    )
    async def preview_chain_test_sell(
        buy_order_id: int,
        request: Request,
    ) -> WhaleSellPreviewRead:
        database: Database = request.app.state.database
        async with database.sessions() as session:
            chain_test_buy = await session.get(WhaleOrder, buy_order_id)
        if (
            chain_test_buy is None
            or chain_test_buy.source != "chain_test"
            or chain_test_buy.side != "BUY"
            or chain_test_buy.filled_size <= 0
            or chain_test_buy.position_id is None
        ):
            raise HTTPException(status_code=409, detail="该订单不是已成交的链上环境测试买单")
        try:
            quote = await request.app.state.whale_executor.quote_sell(
                position_id=chain_test_buy.position_id,
                size=chain_test_buy.filled_size,
                sell_all=False,
            )
        except ValueError as error:
            raise whale_value_error(error, preview=True) from error
        except (PolymarketAPIError, TradingUnavailable) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        confirmation_id = secrets.token_urlsafe(32)
        expires_at = utcnow() + timedelta(minutes=5)
        request.app.state.chain_test_sell_previews[confirmation_id] = {
            "quote": quote,
            "buy_order_id": buy_order_id,
            "expires_at": expires_at,
        }
        return WhaleSellPreviewRead.model_validate(
            {"confirmation_id": confirmation_id, "expires_at": expires_at, **asdict(quote)}
        )

    @application.post(
        "/api/execution-account/chain-test/orders/{buy_order_id}/sell/execute",
        response_model=WhaleOrderRead,
    )
    async def execute_chain_test_sell(
        buy_order_id: int,
        payload: WhaleSellExecuteRequest,
        request: Request,
    ) -> WhaleOrderRead:
        stored = request.app.state.chain_test_sell_previews.pop(payload.confirmation_id, None)
        if stored is None:
            raise HTTPException(status_code=409, detail="测试卖出确认已失效，请重新预览")
        quote = stored["quote"]
        if stored["buy_order_id"] != buy_order_id:
            raise HTTPException(status_code=409, detail="测试卖出确认与买单不匹配")
        if stored["expires_at"] < utcnow():
            raise HTTPException(status_code=409, detail="测试卖出确认已过期，请重新预览")
        try:
            order_id = await request.app.state.whale_executor.execute_sell(
                quote, payload.confirmation_id, order_source="chain_test"
            )
        except ValueError as error:
            raise whale_value_error(error) from error
        except (PolymarketAPIError, TradingUnavailable) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        database: Database = request.app.state.database
        async with database.sessions() as session:
            order = await session.scalar(
                select(WhaleOrder)
                .options(selectinload(WhaleOrder.fills))
                .where(WhaleOrder.id == order_id)
            )
            assert order is not None
            return WhaleOrderRead.model_validate(whale_order_payload(order))

    def require_whale_module(request: Request) -> None:
        if not request.app.state.settings.whale_enabled:
            raise HTTPException(status_code=503, detail="巨鲸模块已通过环境配置关闭")

    def whale_value_error(error: ValueError, *, preview: bool = False) -> HTTPException:
        message = str(error)
        validation_markers = (
            "金额必须",
            "金额不能",
            "卖出份额必须",
            "不能超过",
            "不能低于",
            "投入记录与",
        )
        return HTTPException(
            status_code=(
                422 if preview and any(marker in message for marker in validation_markers) else 409
            ),
            detail=message,
        )

    async def wallet_call(operation: Any) -> Any:
        try:
            return await operation
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (TradingUnavailable, PolymarketAPIError) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    def wallet_confirmation(request: Request, key: str, confirmation_id: str) -> dict[str, Any]:
        stored = request.app.state.wallet_previews.pop(confirmation_id, None)
        if stored is None or stored["key"] != key or stored["expires_at"] < utcnow():
            raise HTTPException(status_code=409, detail="确认已失效，请重新预览")
        return stored["quote"]

    def store_wallet_preview(request: Request, key: str, quote: dict[str, Any]) -> dict[str, Any]:
        previews = request.app.state.wallet_previews
        now = utcnow()
        for token in list(previews):
            if previews[token]["expires_at"] < now:
                previews.pop(token, None)
        confirmation_id = secrets.token_urlsafe(32)
        expires_at = now + timedelta(minutes=5)
        previews[confirmation_id] = {"key": key, "quote": quote, "expires_at": expires_at}
        return {**quote, "confirmation_id": confirmation_id, "expires_at": expires_at}

    @application.get("/api/execution-account/positions", response_model=WalletPositionsRead)
    async def get_wallet_positions(request: Request) -> Any:
        executor = request.app.state.whale_executor
        warning = await wallet_call(executor.reconcile_pending_orders())
        result = await wallet_call(executor.wallet_positions())
        return {**result, "warning": warning}

    @application.get("/api/execution-account/orders", response_model=list[WalletOrderRead])
    async def get_wallet_orders(request: Request) -> Any:
        result = await get_wallet_positions(request)
        return result["orders"]

    @application.post(
        "/api/execution-account/positions/{asset_id}/sell/preview",
        response_model=WalletSellPreviewRead,
    )
    async def preview_wallet_sell(
        asset_id: str,
        payload: WalletSellPreviewRequest,
        request: Request,
    ) -> Any:
        quote = await wallet_call(
            request.app.state.whale_executor.quote_wallet_sell(asset_id, **payload.model_dump())
        )
        return store_wallet_preview(request, f"sell:{asset_id}", quote)

    @application.post(
        "/api/execution-account/positions/{asset_id}/sell/execute",
        response_model=WalletOrderRead,
    )
    async def execute_wallet_sell(
        asset_id: str,
        payload: WhaleSellExecuteRequest,
        request: Request,
    ) -> Any:
        quote = wallet_confirmation(request, f"sell:{asset_id}", payload.confirmation_id)
        return await wallet_call(
            request.app.state.whale_executor.execute_wallet_sell(quote, payload.confirmation_id)
        )

    @application.post(
        "/api/execution-account/orders/{order_id}/cancel/preview",
        response_model=WalletCancelPreviewRead,
    )
    async def preview_wallet_cancel(order_id: int, request: Request) -> Any:
        quote = await wallet_call(request.app.state.whale_executor.quote_wallet_cancel(order_id))
        return store_wallet_preview(request, f"cancel:{order_id}", quote)

    @application.post(
        "/api/execution-account/orders/{order_id}/cancel/execute",
        response_model=WalletOrderRead,
    )
    async def execute_wallet_cancel(
        order_id: int,
        payload: WalletCancelExecuteRequest,
        request: Request,
    ) -> Any:
        quote = wallet_confirmation(request, f"cancel:{order_id}", payload.confirmation_id)
        return await wallet_call(request.app.state.whale_executor.execute_wallet_cancel(quote))

    @application.get("/api/whales/settings", response_model=WhaleSettingsRead)
    async def get_whale_settings(request: Request) -> WhaleSettingsRead:
        require_whale_module(request)
        try:
            values = await whale_settings_read(request.app.state.database)
            return WhaleSettingsRead.model_validate(values)
        except ValueError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @application.get("/api/home/overview", response_model=HomeOverviewRead)
    async def get_home_overview(request: Request) -> HomeOverviewRead:
        require_whale_module(request)
        return HomeOverviewRead.model_validate(
            await home_overview(
                request.app.state.database,
                request.app.state.polymarket_client,
                scanner_running=request.app.state.whale_scanner.is_running,
            )
        )

    @application.get(
        "/api/whales/auto-decisions",
        response_model=WhaleAutoDecisionListRead,
    )
    async def get_whale_auto_decisions(
        request: Request,
        rule: str = Query(default="all", pattern="^(all|new_account|large_amount)$"),
        status_name: str = Query(default="all", alias="status", max_length=30),
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> WhaleAutoDecisionListRead:
        require_whale_module(request)
        try:
            payload = await list_whale_auto_decisions(
                request.app.state.database,
                rule=rule,
                status=status_name,
                limit=limit,
                offset=offset,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return WhaleAutoDecisionListRead.model_validate(payload)

    @application.get("/api/whales/exclusions", response_model=WhaleExclusionListRead)
    async def get_whale_exclusions(request: Request) -> WhaleExclusionListRead:
        require_whale_module(request)
        return WhaleExclusionListRead.model_validate(
            await list_whale_exclusions(request.app.state.database)
        )

    @application.post(
        "/api/whales/exclusions",
        response_model=WhaleExclusionRead,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_whale_exclusion(
        payload: WhaleExclusionCreate,
        request: Request,
    ) -> WhaleExclusionRead:
        require_whale_module(request)
        try:
            address = parse_wallet_input(payload.address)
        except InvalidWalletInput as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        label = payload.label.strip() if payload.label and payload.label.strip() else None
        database: Database = request.app.state.database
        now = utcnow()
        async with database.sessions() as session:
            if await session.get(WhaleExclusion, address) is not None:
                raise HTTPException(status_code=409, detail="该账户已在巨鲸排除名单中")
            session.add(WhaleExclusion(proxy_wallet=address, label=label, created_at=now))
            entries = list(
                (
                    await session.scalars(
                        select(WhaleEntry).where(func.lower(WhaleEntry.proxy_wallet) == address)
                    )
                ).all()
            )
            if entries:
                states = list(
                    (
                        await session.scalars(
                            select(WhaleEntryRuleState).where(
                                WhaleEntryRuleState.entry_id.in_([entry.id for entry in entries]),
                                WhaleEntryRuleState.active.is_(True),
                            )
                        )
                    ).all()
                )
                for state_row in states:
                    state_row.active = False
                    state_row.inactive_at = now
                    state_row.inactive_reason = "account_excluded"
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise HTTPException(
                    status_code=409,
                    detail="该账户已在巨鲸排除名单中",
                ) from error
        request.app.state.whale_scanner.wake()
        result = await list_whale_exclusions(database)
        item = next(item for item in result["items"] if item["proxy_wallet"] == address)
        return WhaleExclusionRead.model_validate(item)

    @application.delete(
        "/api/whales/exclusions/{proxy_wallet}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_whale_exclusion(proxy_wallet: str, request: Request) -> Response:
        require_whale_module(request)
        try:
            address = parse_wallet_input(proxy_wallet)
        except InvalidWalletInput as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        database: Database = request.app.state.database
        async with database.sessions() as session:
            exclusion = await session.get(WhaleExclusion, address)
            if exclusion is None:
                raise HTTPException(status_code=404, detail="该账户不在巨鲸排除名单中")
            await session.delete(exclusion)
            await session.commit()
        request.app.state.whale_scanner.wake()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @application.put("/api/whales/settings", response_model=WhaleSettingsRead)
    async def update_whale_settings(
        payload: WhaleSettingsUpdate,
        request: Request,
    ) -> WhaleSettingsRead:
        require_whale_module(request)
        database: Database = request.app.state.database
        values = payload.model_dump(exclude_none=True)
        for nullable_key in (
            "new_account_auto_follow_low_price_max_price",
            "new_account_auto_follow_low_price_amount_usdc",
            "large_amount_auto_follow_low_price_max_price",
            "large_amount_auto_follow_low_price_amount_usdc",
            "auto_follow_market_max_purchase_count",
            "auto_follow_market_max_amount_usdc",
        ):
            if nullable_key in payload.model_fields_set and getattr(payload, nullable_key) is None:
                values[nullable_key] = None
        for public_key, storage_key in (
            ("monitor_categories", "monitor_categories_json"),
            (
                "new_account_auto_follow_categories",
                "new_account_auto_follow_categories_json",
            ),
            (
                "large_amount_auto_follow_categories",
                "large_amount_auto_follow_categories_json",
            ),
        ):
            categories = values.pop(public_key, None)
            if categories is not None:
                values[storage_key] = json.dumps(categories, separators=(",", ":"))
        # The page exposes one “重仓阈值”.  Keep single and cumulative gates in
        # lockstep when only the cumulative value is supplied, otherwise raising
        # the visible threshold would not necessarily narrow results.
        if "cumulative_threshold_usdc" in values and "single_trade_threshold_usdc" not in values:
            values["single_trade_threshold_usdc"] = values["cumulative_threshold_usdc"]
        if "new_account_threshold_usdc" in values:
            values["single_trade_threshold_usdc"] = values["new_account_threshold_usdc"]
            values["cumulative_threshold_usdc"] = values["new_account_threshold_usdc"]
        elif "cumulative_threshold_usdc" in values:
            values["new_account_threshold_usdc"] = values["cumulative_threshold_usdc"]
            values["single_trade_threshold_usdc"] = values["cumulative_threshold_usdc"]
        elif "single_trade_threshold_usdc" in values:
            values["new_account_threshold_usdc"] = values["single_trade_threshold_usdc"]
            values["cumulative_threshold_usdc"] = values["single_trade_threshold_usdc"]
        async with database.sessions() as session:
            row = await session.get(WhaleSettings, 1)
            if row is None:
                raise HTTPException(status_code=503, detail="巨鲸模块尚未初始化")
            merged = {
                column.name: values.get(column.name, getattr(row, column.name))
                for column in WhaleSettings.__table__.columns
            }
            if merged["single_trade_threshold_usdc"] < merged["collect_filter_amount_usdc"]:
                raise HTTPException(status_code=422, detail="单笔重仓阈值不能低于采集金额阈值")
            if merged["cumulative_threshold_usdc"] < merged["collect_filter_amount_usdc"]:
                raise HTTPException(status_code=422, detail="累计重仓阈值不能低于采集金额阈值")
            if merged["new_account_threshold_usdc"] < merged["collect_filter_amount_usdc"]:
                raise HTTPException(status_code=422, detail="新号大额门槛不能低于采集金额阈值")
            if merged["large_amount_threshold_usdc"] < merged["collect_filter_amount_usdc"]:
                raise HTTPException(status_code=422, detail="全量超大额门槛不能低于采集金额阈值")
            if merged["exited_ratio_threshold"] >= merged["holding_ratio_threshold"]:
                raise HTTPException(status_code=422, detail="退出比例阈值必须低于持有比例阈值")
            if merged["default_follow_amount_usdc"] > merged["max_follow_amount_usdc"]:
                raise HTTPException(status_code=422, detail="默认买入金额不能超过单笔买入上限")
            for label, prefix in (
                ("新号大额", "new_account"),
                ("全量超大额", "large_amount"),
            ):
                amount = merged[f"{prefix}_auto_follow_amount_usdc"]
                minimum = merged[f"{prefix}_auto_follow_min_price"]
                maximum = merged[f"{prefix}_auto_follow_max_price"]
                low_maximum = merged[f"{prefix}_auto_follow_low_price_max_price"]
                low_amount = merged[f"{prefix}_auto_follow_low_price_amount_usdc"]
                if amount > merged["max_follow_amount_usdc"]:
                    raise HTTPException(
                        status_code=422,
                        detail=f"{label}自动跟单金额不能超过单笔买入上限",
                    )
                if minimum > maximum:
                    raise HTTPException(
                        status_code=422,
                        detail=f"{label}自动跟单最低买价不能高于最高买价",
                    )
                if (low_maximum is None) != (low_amount is None):
                    raise HTTPException(
                        status_code=422,
                        detail=f"{label}低价分界与低价金额必须同时设置或同时清除",
                    )
                if low_maximum is not None and low_amount is not None:
                    if not minimum < low_maximum < maximum:
                        raise HTTPException(
                            status_code=422,
                            detail=f"{label}低价分界必须严格位于实际买价区间内",
                        )
                    if low_amount >= amount:
                        raise HTTPException(
                            status_code=422,
                            detail=f"{label}低价金额必须小于基础单笔金额",
                        )
                    if low_amount > merged["max_follow_amount_usdc"]:
                        raise HTTPException(
                            status_code=422,
                            detail=f"{label}低价金额不能超过单笔买入上限",
                        )
            market_count_cap = merged["auto_follow_market_max_purchase_count"]
            market_amount_cap = merged["auto_follow_market_max_amount_usdc"]
            if (market_count_cap is None) != (market_amount_cap is None):
                raise HTTPException(
                    status_code=422,
                    detail="单市场最大购买次数与累计金额必须同时设置或同时清除",
                )
            if market_count_cap is not None and market_amount_cap is not None:
                enabled_amounts = []
                for prefix in ("new_account", "large_amount"):
                    if not merged[f"{prefix}_auto_follow_enabled"]:
                        continue
                    enabled_amounts.append(merged[f"{prefix}_auto_follow_amount_usdc"])
                if enabled_amounts and market_amount_cap < max(enabled_amounts):
                    raise HTTPException(
                        status_code=422,
                        detail="单市场累计金额不能低于已开启策略的基础单笔金额",
                    )
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = utcnow()
            await session.commit()
        request.app.state.whale_scanner.wake()
        request.app.state.whale_email_notifier.wake()
        response = await whale_settings_read(database)
        return WhaleSettingsRead.model_validate(response)

    @application.get(
        "/api/email-notifications",
        response_model=EmailDeliveryListRead,
    )
    async def get_whale_email_notifications(
        request: Request,
        delivery_status: str = Query(
            default="all",
            alias="status",
            pattern="^(all|pending|sending|retrying|sent|failed)$",
        ),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> EmailDeliveryListRead:
        return EmailDeliveryListRead.model_validate(
            await list_whale_email_deliveries(
                request.app.state.database,
                status=delivery_status,
                limit=limit,
                offset=offset,
            )
        )

    @application.get("/api/email-settings", response_model=EmailSettingsRead)
    async def get_email_settings(request: Request) -> EmailSettingsRead:
        try:
            return EmailSettingsRead.model_validate(
                await email_settings_read(
                    request.app.state.database,
                    request.app.state.settings,
                )
            )
        except ValueError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @application.put("/api/email-settings", response_model=EmailSettingsRead)
    async def update_email_settings(
        payload: EmailSettingsUpdate,
        request: Request,
    ) -> EmailSettingsRead:
        values = payload.model_dump(exclude_none=True)
        authorization_code = values.pop("smtp_authorization_code", None)
        notification_recipients = values.pop("notification_recipients", None)
        async with request.app.state.database.sessions() as session:
            row = await session.get(EmailSettings, 1)
            if row is None:
                raise HTTPException(status_code=503, detail="邮件设置尚未初始化")
            weekly_summary_enabled = values.get("weekly_summary_enabled")
            if weekly_summary_enabled is True and not row.weekly_summary_enabled:
                row.weekly_summary_enabled_at = utcnow()
            elif weekly_summary_enabled is False:
                row.weekly_summary_enabled_at = None
            next_username = values.get("smtp_username", row.smtp_username)
            if (
                "smtp_username" in values
                and row.smtp_keychain_account
                and values["smtp_username"] != row.smtp_keychain_account
                and authorization_code is None
            ):
                raise HTTPException(
                    status_code=422,
                    detail="修改发件邮箱时必须同时填写新的客户端授权码",
                )
            if authorization_code is not None:
                if not next_username:
                    raise HTTPException(status_code=422, detail="请先填写 SMTP 用户名")
                reference = KeychainReference(
                    service=SMTP_KEYCHAIN_SERVICE,
                    account=next_username,
                )
                try:
                    await asyncio.to_thread(
                        request.app.state.keychain.set_secret,
                        reference,
                        authorization_code,
                    )
                except KeychainError as error:
                    raise HTTPException(status_code=409, detail=str(error)) from error
                row.smtp_keychain_service = reference.service
                row.smtp_keychain_account = reference.account
            if "smtp_username" in values and "smtp_from_email" not in values:
                values["smtp_from_email"] = values["smtp_username"]
            for key, value in values.items():
                setattr(row, key, value)
            if notification_recipients is not None:
                now = utcnow()
                existing_recipients = {
                    item.email: item
                    for item in (await session.scalars(select(EmailRecipient))).all()
                }
                wanted = set(notification_recipients)
                for email, recipient in existing_recipients.items():
                    recipient.enabled = email in wanted
                    recipient.updated_at = now
                for email in wanted - set(existing_recipients):
                    session.add(
                        EmailRecipient(
                            email=email,
                            enabled=True,
                            created_at=now,
                            updated_at=now,
                        )
                    )
            row.updated_at = utcnow()
            await session.commit()
        request.app.state.whale_email_notifier.wake()
        return EmailSettingsRead.model_validate(
            await email_settings_read(
                request.app.state.database,
                request.app.state.settings,
            )
        )

    @application.post("/api/email-settings/test", response_model=EmailTestRead)
    async def test_email_settings(
        payload: EmailTestRequest,
        request: Request,
    ) -> EmailTestRead:
        recipient = payload.recipient_email if payload.send_email else None
        try:
            result = await request.app.state.whale_email_notifier.test_connection(
                recipient_email=recipient
            )
        except smtplib.SMTPAuthenticationError as error:
            raise HTTPException(
                status_code=422,
                detail="163 SMTP 身份认证失败，请检查邮箱账号和客户端授权码",
            ) from error
        except (TimeoutError, OSError, smtplib.SMTPException, KeychainError) as error:
            raise HTTPException(
                status_code=502,
                detail=f"SMTP 连接失败：{str(error)[:300]}",
            ) from error
        return EmailTestRead.model_validate(result)

    @application.post("/api/whales/scan", response_model=WhaleScanRead)
    async def scan_whales(request: Request) -> WhaleScanRead:
        require_whale_module(request)
        completed = await request.app.state.whale_scanner.scan_now()
        return WhaleScanRead(status="ok" if completed else "skipped")

    @application.get(
        "/api/whales/scan-runs",
        response_model=WhaleScanRunListRead,
    )
    async def get_whale_scan_runs(
        request: Request,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> WhaleScanRunListRead:
        require_whale_module(request)
        payload = await list_whale_scan_runs(
            request.app.state.database,
            limit=limit,
            offset=offset,
        )
        return WhaleScanRunListRead(
            total=payload["total"],
            items=[WhaleScanRunRead.model_validate(row) for row in payload["items"]],
        )

    @application.get(
        "/api/whales/request-logs",
        response_model=WhaleRequestLogListRead,
    )
    async def get_whale_request_logs(request: Request) -> WhaleRequestLogListRead:
        require_whale_module(request)
        monitor: WhaleRequestMonitor = request.app.state.whale_request_monitor
        records = await monitor.snapshot()
        return WhaleRequestLogListRead(
            generated_at=utcnow(),
            total=len(records),
            items=[WhaleRequestLogRead.model_validate(record) for record in records],
        )

    @application.get("/api/whales/request-logs/stream")
    async def stream_whale_request_logs(request: Request) -> StreamingResponse:
        require_whale_module(request)
        monitor: WhaleRequestMonitor = request.app.state.whale_request_monitor

        async def event_stream():
            yield ": connected\n\n"
            async with monitor.subscribe() as queue:
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        record = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    payload = WhaleRequestLogRead.model_validate(record).model_dump(mode="json")
                    yield (
                        f"id: {record.id}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
                    )

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @application.get("/api/whales/tags", response_model=list[WhaleTagRead])
    async def get_whale_tags(request: Request) -> list[WhaleTagRead]:
        require_whale_module(request)
        database: Database = request.app.state.database
        async with database.sessions() as session:
            tags = list(
                (
                    await session.scalars(
                        select(WhaleTag)
                        .where(WhaleTag.market_count > 0)
                        .order_by(WhaleTag.market_count.desc(), WhaleTag.label.asc())
                    )
                ).all()
            )
        return [WhaleTagRead.model_validate(tag) for tag in tags]

    @application.get("/api/whales/statistics", response_model=WhaleStatisticsRead)
    async def get_whale_statistics(
        request: Request,
        range_name: str = Query(
            default="all",
            alias="range",
            pattern="^(all|7d|30d|90d)$",
        ),
        category: str = Query(
            default="all",
            pattern="^(all|esports|sports|politics|crypto|science_tech|entertainment|other)$",
        ),
        subcategory: str = Query(default="all", max_length=100),
    ) -> WhaleStatisticsRead:
        require_whale_module(request)
        try:
            payload = await whale_statistics(
                request.app.state.database,
                range_name=range_name,
                category=category,
                subcategory=subcategory,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return WhaleStatisticsRead.model_validate(payload)

    @application.get(
        "/api/whales/statistics/signals",
        response_model=WhaleStatisticsSignalListRead,
    )
    async def get_whale_statistics_signals(
        request: Request,
        range_name: str = Query(
            default="all",
            alias="range",
            pattern="^(all|7d|30d|90d)$",
        ),
        rule: str = Query(default="all", pattern="^(all|new_account|large_amount|both)$"),
        result: str = Query(default="all", pattern="^(all|hit|miss|special)$"),
        amount_band: str = Query(
            default="all",
            pattern="^(all|lt_100k|100k_500k|500k_1m|gte_1m)$",
        ),
        category: str = Query(
            default="all",
            pattern="^(all|esports|sports|politics|crypto|science_tech|entertainment|other)$",
        ),
        subcategory: str = Query(default="all", max_length=100),
        sort: str = Query(
            default="settled_desc",
            pattern="^(settled_desc|amount_desc|pnl_desc|pnl_asc)$",
        ),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> WhaleStatisticsSignalListRead:
        require_whale_module(request)
        try:
            payload = await list_whale_statistics_signals(
                request.app.state.database,
                range_name=range_name,
                rule=rule,
                result=result,
                amount_band=amount_band,
                category=category,
                subcategory=subcategory,
                sort=sort,
                limit=limit,
                offset=offset,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return WhaleStatisticsSignalListRead.model_validate(payload)

    @application.get("/api/whales/markets", response_model=WhaleMarketListRead)
    async def get_whale_markets(
        request: Request,
        tag_slug: Annotated[str | None, Query(max_length=200)] = None,
        min_amount_usdc: Annotated[Decimal | None, Query(ge=0)] = None,
        min_remaining_minutes: Annotated[int | None, Query(ge=0)] = None,
        max_price_delta_cents: Annotated[Decimal | None, Query(ge=0)] = None,
        include_exited: bool = False,
        include_hedged: bool = True,
        sort: str = Query(default="default"),
        rule: str = Query(default="new_account", pattern="^(new_account|large_amount)$"),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> WhaleMarketListRead:
        require_whale_module(request)
        if sort not in {"default", "ending_soon", "max_entry", "least_delta"}:
            raise HTTPException(status_code=422, detail="巨鲸市场排序参数无效")
        payload = await list_whale_markets(
            request.app.state.database,
            tag_slug=tag_slug,
            min_amount_usdc=min_amount_usdc,
            min_remaining_minutes=min_remaining_minutes,
            max_price_delta_cents=max_price_delta_cents,
            include_exited=include_exited,
            include_hedged=include_hedged,
            sort=sort,
            limit=limit,
            offset=offset,
            rule=rule,
        )
        return WhaleMarketListRead.model_validate(payload)

    @application.get("/api/whales/history", response_model=WhaleHistoryListRead)
    async def get_whale_history(
        request: Request,
        rule: str = Query(default="new_account", pattern="^(new_account|large_amount)$"),
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> WhaleHistoryListRead:
        require_whale_module(request)
        payload = await list_whale_history(
            request.app.state.database,
            rule=rule,
            limit=limit,
            offset=offset,
        )
        return WhaleHistoryListRead.model_validate(payload)

    @application.get("/api/whales/markets/{condition_id}", response_model=WhaleMarketDetailRead)
    async def get_whale_market_detail(
        condition_id: str,
        request: Request,
        rule: str = Query(default="new_account", pattern="^(new_account|large_amount)$"),
    ) -> WhaleMarketDetailRead:
        require_whale_module(request)
        payload = await list_whale_markets(
            request.app.state.database,
            include_exited=True,
            condition_id=condition_id,
            include_trades=True,
            limit=1,
            rule=rule,
        )
        if not payload["items"]:
            raise HTTPException(status_code=404, detail="巨鲸市场不存在")
        return WhaleMarketDetailRead.model_validate(payload["items"][0])

    @application.post("/api/whales/follow/preview", response_model=WhaleFollowPreviewRead)
    async def preview_whale_follow(
        payload: WhaleFollowPreviewRequest,
        request: Request,
    ) -> WhaleFollowPreviewRead:
        require_whale_module(request)
        try:
            quote = await request.app.state.whale_executor.quote_follow(
                asset_id=payload.asset_id,
                amount_usdc=payload.amount_usdc,
                entry_id=payload.entry_id,
            )
        except ValueError as error:
            raise whale_value_error(error, preview=True) from error
        except (PolymarketAPIError, TradingUnavailable) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        confirmation_id = secrets.token_urlsafe(32)
        expires_at = utcnow() + timedelta(minutes=5)
        request.app.state.whale_follow_previews[confirmation_id] = {
            "quote": quote,
            "expires_at": expires_at,
        }
        return WhaleFollowPreviewRead.model_validate(
            {"confirmation_id": confirmation_id, "expires_at": expires_at, **asdict(quote)}
        )

    @application.post("/api/whales/follow/execute", response_model=WhaleOrderRead)
    async def execute_whale_follow(
        payload: WhaleFollowExecuteRequest,
        request: Request,
    ) -> WhaleOrderRead:
        require_whale_module(request)
        stored = request.app.state.whale_follow_previews.pop(payload.confirmation_id, None)
        if stored is None:
            raise HTTPException(status_code=409, detail="跟单确认已失效，请重新预览")
        if stored["expires_at"] < utcnow():
            raise HTTPException(status_code=409, detail="跟单确认已过期，请重新预览")
        try:
            order_id = await request.app.state.whale_executor.execute_follow(
                stored["quote"], payload.confirmation_id
            )
        except ValueError as error:
            raise whale_value_error(error) from error
        except (PolymarketAPIError, TradingUnavailable) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        database: Database = request.app.state.database
        async with database.sessions() as session:
            order = await session.scalar(
                select(WhaleOrder)
                .options(selectinload(WhaleOrder.fills))
                .where(WhaleOrder.id == order_id)
            )
            assert order is not None
            return WhaleOrderRead.model_validate(whale_order_payload(order))

    @application.post(
        "/api/whales/positions/{position_id}/sell/preview",
        response_model=WhaleSellPreviewRead,
    )
    async def preview_whale_sell(
        position_id: int,
        payload: WhaleSellPreviewRequest,
        request: Request,
    ) -> WhaleSellPreviewRead:
        require_whale_module(request)
        try:
            quote = await request.app.state.whale_executor.quote_sell(
                position_id=position_id,
                size=payload.size,
                sell_all=payload.sell_all,
            )
        except ValueError as error:
            raise whale_value_error(error, preview=True) from error
        except (PolymarketAPIError, TradingUnavailable) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        confirmation_id = secrets.token_urlsafe(32)
        expires_at = utcnow() + timedelta(minutes=5)
        request.app.state.whale_sell_previews[confirmation_id] = {
            "quote": quote,
            "expires_at": expires_at,
        }
        return WhaleSellPreviewRead.model_validate(
            {"confirmation_id": confirmation_id, "expires_at": expires_at, **asdict(quote)}
        )

    @application.post(
        "/api/whales/positions/{position_id}/sell/execute",
        response_model=WhaleOrderRead,
    )
    async def execute_whale_sell(
        position_id: int,
        payload: WhaleSellExecuteRequest,
        request: Request,
    ) -> WhaleOrderRead:
        require_whale_module(request)
        stored = request.app.state.whale_sell_previews.pop(payload.confirmation_id, None)
        if stored is None:
            raise HTTPException(status_code=409, detail="卖出确认已失效，请重新预览")
        quote = stored["quote"]
        if quote.position_id != position_id:
            raise HTTPException(status_code=409, detail="卖出确认与持仓不匹配")
        if stored["expires_at"] < utcnow():
            raise HTTPException(status_code=409, detail="卖出确认已过期，请重新预览")
        try:
            order_id = await request.app.state.whale_executor.execute_sell(
                quote, payload.confirmation_id
            )
        except ValueError as error:
            raise whale_value_error(error) from error
        except (PolymarketAPIError, TradingUnavailable) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        database: Database = request.app.state.database
        async with database.sessions() as session:
            order = await session.scalar(
                select(WhaleOrder)
                .options(selectinload(WhaleOrder.fills))
                .where(WhaleOrder.id == order_id)
            )
            assert order is not None
            return WhaleOrderRead.model_validate(whale_order_payload(order))

    @application.get("/api/whales/positions", response_model=WhalePositionListRead)
    async def get_whale_positions(
        request: Request,
        status: str = Query(default="all"),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> WhalePositionListRead:
        require_whale_module(request)
        if status not in {"open", "closed", "all"}:
            raise HTTPException(status_code=422, detail="巨鲸持仓状态参数无效")
        return WhalePositionListRead.model_validate(
            await list_whale_positions(
                request.app.state.database,
                request.app.state.polymarket_client,
                status=status,
                limit=limit,
                offset=offset,
            )
        )

    @application.get("/api/whales/positions/{position_id}", response_model=WhalePositionDetailRead)
    async def get_whale_position_detail(
        position_id: int,
        request: Request,
    ) -> WhalePositionDetailRead:
        require_whale_module(request)
        payload = await whale_position_detail(
            request.app.state.database,
            request.app.state.polymarket_client,
            position_id,
        )
        if payload is None:
            raise HTTPException(status_code=404, detail="巨鲸持仓不存在")
        return WhalePositionDetailRead.model_validate(payload)

    @application.get("/api/whales/records", response_model=WhaleRecordListRead)
    async def get_whale_records(
        request: Request,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> WhaleRecordListRead:
        require_whale_module(request)
        start = datetime.combine(start_date, datetime.min.time()) if start_date else None
        end = (
            datetime.combine(end_date + timedelta(days=1), datetime.min.time())
            - timedelta(microseconds=1)
            if end_date
            else None
        )
        return WhaleRecordListRead.model_validate(
            await list_whale_records(
                request.app.state.database,
                request.app.state.polymarket_client,
                start_date=start,
                end_date=end,
                limit=limit,
                offset=offset,
            )
        )

    return application


app = create_app()
