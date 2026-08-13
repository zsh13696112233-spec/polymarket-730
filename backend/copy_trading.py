from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.config import Settings
from backend.db import Database
from backend.keychain import KeychainReference, MacOSKeychain
from backend.models import (
    CopyFill,
    CopyLedger,
    CopyOrder,
    CopyPosition,
    CopyRedemption,
    CopyRedemptionExecution,
    CopySubscription,
    CurrentPosition,
    ExecutionAccount,
    PositionEvent,
    PositionEventFill,
)
from backend.polymarket import (
    OrderBookSnapshot,
    PolymarketAPIError,
    PolymarketClient,
    PositionSnapshot,
)
from backend.trading import (
    MarketTradeRequest,
    TradeResult,
    TradingUnavailable,
    UnifiedPolymarketTrader,
    normalize_fak_result,
)

ZERO = Decimal("0")
ONE = Decimal("1")
# CLOB outcome-token quantities are executable to two decimal places. A smaller
# remainder cannot be submitted as a follow-up sell order.
UNTRADEABLE_DUST_SIZE = Decimal("0.01")
REDEMPTION_SIZE_TOLERANCE = Decimal("0.000001")
REDEMPTION_SIZE_RELATIVE_TOLERANCE = Decimal("0.000001")
SHANGHAI = ZoneInfo("Asia/Shanghai")
RETRYABLE_REDEMPTION_CREDENTIAL_ERRORS = frozenset(
    {
        "Deposit Wallet 自动赎回需要 Relayer API 凭证",
        "钥匙串中没有找到执行钱包密钥",
        "缺少官方 Builder Relayer 客户端",
        "Builder 凭证格式无效",
    }
)
RETRYABLE_REDEMPTION_ERROR_MARKERS = (
    "expected safe ",
    "自动赎回准备失败：",
    "等待市场完成链上结算",
    "未消耗 outcome token",
)


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def redemption_sizes_match(actual: Decimal, expected: Decimal) -> bool:
    tolerance = max(
        REDEMPTION_SIZE_TOLERANCE,
        abs(expected) * REDEMPTION_SIZE_RELATIVE_TOLERANCE,
    )
    return abs(actual - expected) <= tolerance


def chain_redemption_sizes_match(actual: Decimal, expected: Decimal) -> bool:
    """Compare exact ERC-1155 balances without relying on Data API display precision."""
    return abs(actual - expected) <= REDEMPTION_SIZE_TOLERANCE


def redemption_execution_was_never_submitted(execution: CopyRedemptionExecution) -> bool:
    return (
        execution.attempts == 0
        and execution.relayer_transaction_id is None
        and execution.transaction_hash is None
        and execution.submitted_at is None
    )


def redemption_error_is_retryable(message: str | None) -> bool:
    if not message:
        return False
    normalized = message.lower()
    return message in RETRYABLE_REDEMPTION_CREDENTIAL_ERRORS or any(
        marker in normalized for marker in RETRYABLE_REDEMPTION_ERROR_MARKERS
    )


def is_confirmed_onchain_redemption(event: PositionEvent) -> bool:
    """Return whether an execution-wallet event proves an on-chain redemption."""
    return event.reconciliation_status == "onchain" and bool(event.transaction_hash)


def event_payout_for_size(event: PositionEvent, size: Decimal) -> Decimal:
    """Scale a source or execution redemption payout to the copied share count."""
    if event.payout_amount is None or event.before_size <= ZERO:
        return size
    payout_rate = max(ZERO, min(ONE, event.payout_amount / event.before_size))
    return size * payout_rate


def redeemable_position_payout_rate(position: PositionSnapshot) -> Decimal:
    """Estimate the finalized payout exposed by Data API for a redeemable token."""
    rate = position.current_price
    if position.size > ZERO and position.current_value >= ZERO:
        rate = position.current_value / position.size
    rate = max(ZERO, min(ONE, rate))
    # Data API marks resolved winners near 1 (for example 0.9995) rather than
    # returning the exact CTF payout. Preserve genuine partial resolutions.
    return ONE if rate >= Decimal("0.99") else rate


def market_worst_price(
    reference: Decimal,
    tick: Decimal,
    slippage_cents: Decimal,
    *,
    side: str,
) -> Decimal:
    buffer = slippage_cents / Decimal("100")
    raw = reference + buffer if side == "BUY" else reference - buffer
    rounding = ROUND_UP if side == "BUY" else ROUND_DOWN
    ticks = (raw / tick).to_integral_value(rounding=rounding)
    return max(tick, min(ONE - tick, ticks * tick))


@dataclass(frozen=True, slots=True)
class RiskUsage:
    position: Decimal = ZERO
    total: Decimal = ZERO
    global_total: Decimal = ZERO
    bought_today: Decimal = ZERO
    realized_loss_today: Decimal = ZERO
    wallet_capital: Decimal = ZERO
    account_daily_buy_limit: Decimal = Decimal("999999999")
    account_daily_loss_limit: Decimal = Decimal("999999999")


def allowed_buy_usdc(
    requested: Decimal,
    *,
    subscription: CopySubscription,
    usage: RiskUsage,
) -> tuple[Decimal, str | None]:
    if usage.realized_loss_today >= usage.account_daily_loss_limit:
        return ZERO, "已触发执行钱包当日亏损熔断"
    limits = {
        "单仓最大投入已满": subscription.position_cap_usdc - usage.position,
        "策略总敞口已满": subscription.total_exposure_cap_usdc - usage.total,
        "执行钱包当日买入额度已满": usage.account_daily_buy_limit - usage.bought_today,
        "执行钱包可用预算不足": usage.wallet_capital - usage.global_total,
    }
    allowed = min([requested, *limits.values()])
    if allowed > ZERO:
        return allowed, None
    reason = next((label for label, value in limits.items() if value <= ZERO), "风险额度不足")
    return ZERO, reason


async def latest_event_id(session: object, wallet_id: int) -> int:
    value = await session.scalar(
        select(func.max(PositionEvent.id)).where(PositionEvent.wallet_id == wallet_id)
    )
    return int(value or 0)


class CopyTradingEngine:
    """Low-frequency copy engine driven only by stable PositionEvent rows.

    `opened` buys once, qualifying `increased` events add to an existing
    attributed position, `closed` sells all attributed tokens and `redeemed`
    redeems all attributed tokens. `decreased` is monitor-only.
    """

    def __init__(
        self,
        *,
        database: Database,
        client: PolymarketClient,
        settings: Settings,
        keychain: MacOSKeychain | None = None,
    ) -> None:
        self.database = database
        self.client = client
        self.settings = settings
        self.keychain = keychain or MacOSKeychain()
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()
        self._trader_cache_key: tuple[object, ...] | None = None
        self._trader_cache: UnifiedPolymarketTrader | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="simple-copy-trading-engine")

    def wake(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._trader_cache is not None:
            await self._trader_cache.close()
            self._trader_cache = None
            self._trader_cache_key = None

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            self._wake.clear()
            try:
                await asyncio.wait_for(
                    self._wake.wait(),
                    timeout=max(
                        1.0,
                        min(
                            self.settings.copy_poll_interval_seconds,
                            self.settings.copy_balance_refresh_interval_seconds,
                        ),
                    ),
                )
            except TimeoutError:
                pass

    async def tick(self) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            await self.refresh_execution_balance()
            async with self.database.sessions() as session:
                ids = list(
                    (
                        await session.scalars(
                            select(CopySubscription.id).where(
                                CopySubscription.state.in_(
                                    ["active", "paused", "exit_only", "closing"]
                                )
                            )
                        )
                    ).all()
                )
            for subscription_id in ids:
                try:
                    await self.reconcile_pending_orders(subscription_id)
                    await self.write_off_untradeable_dust(subscription_id)
                    await self.process_position_events(subscription_id)
                    await self.reconcile_terminal_positions(subscription_id)
                    await self.process_redemptions(subscription_id)
                    await self.process_execution_redeemable_positions(subscription_id)
                    await self._finish_closing(subscription_id)
                except Exception as error:
                    async with self.database.sessions() as session:
                        subscription = await session.get(CopySubscription, subscription_id)
                        if subscription is not None:
                            subscription.last_error = str(error)[:1000]
                            subscription.updated_at = utcnow()
                            await session.commit()

    async def refresh_execution_balance(self, *, force: bool = False) -> Decimal | None:
        """Refresh the CLOB collateral balance at a bounded cadence.

        The dashboard reads this persisted value, so a failed polling request never
        replaces a known balance with a fabricated zero.
        """
        now = utcnow()
        async with self.database.sessions() as session:
            account = await session.get(ExecutionAccount, 1)
            if account is None or not account.keychain_service or not account.keychain_account:
                return None
            last_balance_at = account.last_balance_at
        if (
            not force
            and last_balance_at is not None
            and (now - last_balance_at).total_seconds()
            < self.settings.copy_balance_refresh_interval_seconds
        ):
            return None
        try:
            balance = await (await self._trader()).collateral_balance()
        except Exception as error:
            async with self.database.sessions() as session:
                account = await session.get(ExecutionAccount, 1)
                if account is not None:
                    account.status = "error"
                    account.last_error = f"余额刷新失败：{error}"[:1000]
                    account.updated_at = utcnow()
                    await session.commit()
            return None
        async with self.database.sessions() as session:
            account = await session.get(ExecutionAccount, 1)
            if account is None:
                return balance
            account.collateral_balance = balance
            account.last_balance_at = utcnow()
            account.status = (
                "ready" if balance > account.cash_reserve_usdc else "insufficient_balance"
            )
            account.last_error = None
            account.updated_at = utcnow()
            await session.commit()
        return balance

    async def prime_subscription(self, subscription_id: int) -> None:
        async with self.database.sessions() as session:
            subscription = await session.get(CopySubscription, subscription_id)
            if subscription is None:
                return
            baseline = await latest_event_id(session, subscription.tracked_wallet_id)
            subscription.baseline_event_id = baseline
            subscription.last_processed_event_id = baseline
            subscription.last_processed_at = utcnow()
            subscription.updated_at = utcnow()
            await session.commit()

    async def process_position_events(self, subscription_id: int) -> None:
        while True:
            async with self.database.sessions() as session:
                subscription = await session.get(CopySubscription, subscription_id)
                if subscription is None:
                    return
                event = await session.scalar(
                    select(PositionEvent)
                    .where(
                        PositionEvent.wallet_id == subscription.tracked_wallet_id,
                        PositionEvent.id > subscription.last_processed_event_id,
                    )
                    .order_by(PositionEvent.id.asc())
                    .limit(1)
                )
                if event is None:
                    return
                event_id = event.id
                event_type = event.type
                state = subscription.state
            try:
                if event_type == "opened" and state == "active":
                    await self._leader_opened(subscription_id, event_id)
                elif event_type == "increased" and state == "active":
                    await self._leader_increased(subscription_id, event_id)
                elif event_type == "closed":
                    await self._leader_closed(subscription_id, event_id)
                elif event_type == "redeemed":
                    await self._leader_redeemed(subscription_id, event_id)
                # decreased and buy signals while exit-only are intentionally ignored.
            except Exception:
                # Do not advance an actionable event until its durable order/result exists.
                raise
            async with self.database.sessions() as session:
                subscription = await session.get(CopySubscription, subscription_id)
                if subscription is None:
                    return
                subscription.last_processed_event_id = max(
                    subscription.last_processed_event_id, event_id
                )
                subscription.last_processed_at = utcnow()
                subscription.last_error = None
                subscription.updated_at = utcnow()
                await session.commit()

    async def reconcile_terminal_positions(self, subscription_id: int) -> None:
        """Recover terminal source events that were marked processed before a crash.

        The copy-trading wallet is dedicated to the engine.  Once its on-chain
        balance is zero and the source wallet has a confirmed redemption, record a
        reconciliation-derived payout without fabricating an execution transaction
        hash.  External manual transfers remain visible in the ledger detail.
        """
        async with self.database.sessions() as session:
            subscription = await session.get(CopySubscription, subscription_id)
            if subscription is None:
                return
            account = await session.get(ExecutionAccount, 1)
            positions = list(
                (
                    await session.scalars(
                        select(CopyPosition).where(
                            CopyPosition.subscription_id == subscription_id,
                            CopyPosition.attributed_size > ZERO,
                            CopyPosition.status.in_(["open", "redeeming"]),
                        )
                    )
                ).all()
            )
            candidates: list[tuple[int, int | None, int | None]] = []
            repaired_execution_event = False
            for position in positions:
                buy_event_id = await session.scalar(
                    select(func.max(CopyOrder.leader_event_id)).where(
                        CopyOrder.copy_position_id == position.id,
                        CopyOrder.side == "BUY",
                        CopyOrder.status == "filled",
                    )
                )
                source_redemption = await session.scalar(
                    select(PositionEvent)
                    .where(
                        PositionEvent.wallet_id == subscription.tracked_wallet_id,
                        PositionEvent.asset_id == position.asset_id,
                        PositionEvent.id > int(buy_event_id or 0),
                        PositionEvent.type == "redeemed",
                    )
                    .order_by(PositionEvent.id.desc())
                    .limit(1)
                )
                execution_redemption = (
                    await session.scalar(
                        select(PositionEvent)
                        .where(
                            PositionEvent.wallet_id == account.wallet_id,
                            PositionEvent.asset_id == position.asset_id,
                            PositionEvent.type == "redeemed",
                            PositionEvent.settled_at >= position.created_at,
                        )
                        .order_by(PositionEvent.id.desc())
                        .limit(1)
                    )
                    if account is not None
                    else None
                )
                if execution_redemption is None and account is not None:
                    unresolved_execution_redemptions = list(
                        (
                            await session.scalars(
                                select(PositionEvent)
                                .where(
                                    PositionEvent.wallet_id == account.wallet_id,
                                    PositionEvent.condition_id == position.condition_id,
                                    PositionEvent.type == "redeemed",
                                    PositionEvent.settled_at >= position.created_at,
                                )
                                .order_by(PositionEvent.id.desc())
                            )
                        ).all()
                    )
                    size_matches = [
                        event
                        for event in unresolved_execution_redemptions
                        if event.asset_id.startswith("redeem:")
                        and redemption_sizes_match(event.before_size, position.attributed_size)
                    ]
                    if len(size_matches) == 1:
                        execution_redemption = size_matches[0]
                        execution_redemption.asset_id = position.asset_id
                        if not execution_redemption.outcome:
                            execution_redemption.outcome = position.outcome
                        repaired_execution_event = True
                if source_redemption is not None or execution_redemption is not None:
                    candidates.append(
                        (
                            position.id,
                            source_redemption.id if source_redemption is not None else None,
                            execution_redemption.id if execution_redemption is not None else None,
                        )
                    )
            if repaired_execution_event:
                await session.commit()
        for position_id, source_event_id, execution_event_id in candidates:
            async with self.database.sessions() as session:
                position = await session.get(CopyPosition, position_id)
                existing = await session.scalar(
                    select(CopyRedemption).where(CopyRedemption.copy_position_id == position_id)
                )
                if (
                    position is None
                    or position.attributed_size <= ZERO
                    or (existing is not None and existing.status != "pending")
                ):
                    continue
                asset_id = position.asset_id
                execution_event = (
                    await session.get(PositionEvent, execution_event_id)
                    if execution_event_id is not None
                    else None
                )
                source_event = (
                    await session.get(PositionEvent, source_event_id)
                    if source_event_id is not None
                    else None
                )
            if execution_event is not None and is_confirmed_onchain_redemption(execution_event):
                await self._record_reconciled_redemption(
                    position_id,
                    transaction_hash=execution_event.transaction_hash,
                    detail="执行钱包链上赎回事件与归因持仓匹配后的结算对账",
                    payout_usdc=event_payout_for_size(
                        execution_event,
                        position.attributed_size,
                    ),
                )
                continue

            # A settlement event only says that the resolved position disappeared
            # from the public positions API.  It is not proof that the execution
            # wallet submitted redeemPositions, so verify the token balance before
            # changing the durable copy-trading state.
            trader = await self._trader()
            balance = await trader.onchain_outcome_balance(asset_id)
            if balance > ZERO:
                if source_event is not None:
                    await self._leader_redeemed(subscription_id, source_event.id)
                continue
            terminal_event = source_event or execution_event
            assert terminal_event is not None
            await self._record_reconciled_redemption(
                position_id,
                transaction_hash=None,
                detail="终态结算事件与链上 outcome token 余额归零后的结算对账",
                payout_usdc=event_payout_for_size(terminal_event, position.attributed_size),
            )
        await self.reconcile_resolved_source_disappearances(subscription_id)

    async def reconcile_resolved_source_disappearances(self, subscription_id: int) -> None:
        """Settle copied positions when their source position vanished at resolution.

        The public positions endpoint removes settled outcomes. When its matching
        terminal event was missed, use the official resolution instead of leaving
        the execution wallet's copied outcome open indefinitely.
        """
        async with self.database.sessions() as session:
            subscription = await session.get(CopySubscription, subscription_id)
            if subscription is None:
                return
            account = await session.get(ExecutionAccount, 1)
            positions = list(
                (
                    await session.scalars(
                        select(CopyPosition).where(
                            CopyPosition.subscription_id == subscription_id,
                            CopyPosition.attributed_size > ZERO,
                            CopyPosition.status.in_(["open", "manual_exit"]),
                        )
                    )
                ).all()
            )
            source_assets = set(
                (
                    await session.scalars(
                        select(CurrentPosition.asset_id).where(
                            CurrentPosition.wallet_id == subscription.tracked_wallet_id,
                        )
                    )
                ).all()
            )
        missing_source_positions = [
            position for position in positions if position.asset_id not in source_assets
        ]
        if not missing_source_positions:
            return
        resolutions = await self.client.fetch_market_resolutions(
            position.condition_id for position in missing_source_positions
        )
        for position in missing_source_positions:
            resolution = resolutions.get(position.condition_id)
            payout = (
                resolution.payout_by_asset_id.get(position.asset_id)
                if resolution is not None
                else None
            )
            if payout is None:
                continue
            if payout <= ZERO:
                await self._record_resolved_loss(position.id, resolution.resolved_at)
                continue
            if account is not None and not account.auto_redeem:
                continue
            redemption_id = await self._start_resolution_redemption(position.id, payout)
            if redemption_id is not None:
                await self._execute_redemption(redemption_id, event_id=None)

    async def process_execution_redeemable_positions(self, subscription_id: int) -> None:
        """Redeem copied tokens as soon as the execution wallet reports them redeemable.

        Data API can expose the on-chain redeemable state before Gamma changes a
        market from proposed to finalized. This path deliberately does not wait for
        the source wallet to redeem its own remaining tokens.
        """
        async with self.database.sessions() as session:
            account = await session.get(ExecutionAccount, 1)
            if (
                account is None
                or not account.auto_redeem
                or not account.funder_address
                or account.status != "ready"
            ):
                return
            positions = list(
                (
                    await session.scalars(
                        select(CopyPosition).where(
                            CopyPosition.subscription_id == subscription_id,
                            CopyPosition.attributed_size > ZERO,
                            CopyPosition.status.in_(["open", "redeeming", "manual_exit"]),
                        )
                    )
                ).all()
            )
            funder_address = account.funder_address
        if not positions:
            return

        redeemable = await self.client.fetch_redeemable_positions(
            funder_address,
            condition_ids=(position.condition_id for position in positions),
        )
        redeemable_by_asset = {position.asset_id: position for position in redeemable}
        candidates = [
            (position, redeemable_by_asset[position.asset_id])
            for position in positions
            if position.asset_id in redeemable_by_asset
        ]
        if not candidates:
            return

        trader = await self._trader()
        for position, redeemable_position in candidates:
            payout_rate = redeemable_position_payout_rate(redeemable_position)
            if payout_rate <= ZERO:
                continue
            balance = await trader.onchain_outcome_balance(position.asset_id)
            if balance <= ZERO:
                await self._record_reconciled_redemption(
                    position.id,
                    transaction_hash=None,
                    detail="执行钱包可赎回仓位与链上 outcome token 余额归零后的结算对账",
                    payout_usdc=position.attributed_size * payout_rate,
                )
                continue
            redemption_id = await self._start_resolution_redemption(position.id, payout_rate)
            if redemption_id is not None:
                await self._execute_redemption(redemption_id, event_id=None)

    async def _start_resolution_redemption(
        self,
        position_id: int,
        payout_rate: Decimal,
    ) -> int | None:
        async with self.database.sessions() as session:
            position = await session.get(CopyPosition, position_id)
            account = await session.get(ExecutionAccount, 1)
            existing = await session.scalar(
                select(CopyRedemption).where(CopyRedemption.copy_position_id == position_id)
            )
            if (
                position is None
                or position.attributed_size <= ZERO
                or existing is not None
                or (account is not None and not account.auto_redeem)
            ):
                return None
            redemption = CopyRedemption(
                copy_position_id=position.id,
                status="pending",
                size=position.attributed_size,
                payout_usdc=position.attributed_size * payout_rate,
                transaction_hash=None,
                attempts=0,
                last_error=None,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            session.add(redemption)
            position.status = "redeeming"
            position.updated_at = utcnow()
            await session.commit()
            return redemption.id

    async def write_off_untradeable_dust(self, subscription_id: int) -> None:
        """Close historical partial-fill remainders that cannot be sold on CLOB."""
        async with self.database.sessions() as session:
            positions = list(
                (
                    await session.scalars(
                        select(CopyPosition).where(
                            CopyPosition.subscription_id == subscription_id,
                            CopyPosition.status == "manual_exit",
                            CopyPosition.attributed_size > ZERO,
                            CopyPosition.attributed_size < UNTRADEABLE_DUST_SIZE,
                        )
                    )
                ).all()
            )
            for position in positions:
                dust_cost = position.attributed_cost
                position.attributed_size = ZERO
                position.attributed_cost = ZERO
                position.realized_pnl -= dust_cost
                position.status = "dust_closed"
                position.updated_at = utcnow()
                session.add(
                    CopyLedger(
                        subscription_id=subscription_id,
                        copy_position_id=position.id,
                        order_id=None,
                        type="dust_writeoff",
                        amount_usdc=ZERO,
                        realized_pnl=-dust_cost,
                        detail="卖出后剩余数量低于最小可交易粒度，按零值核销",
                        timestamp=utcnow(),
                    )
                )
            if positions:
                await session.commit()

    async def _record_reconciled_redemption(
        self,
        position_id: int,
        *,
        transaction_hash: str | None,
        detail: str,
        payout_usdc: Decimal | None = None,
    ) -> None:
        async with self.database.sessions() as session:
            position = await session.get(CopyPosition, position_id)
            existing = await session.scalar(
                select(CopyRedemption).where(CopyRedemption.copy_position_id == position_id)
            )
            if (
                position is None
                or position.attributed_size <= ZERO
                or (existing is not None and existing.status != "pending")
            ):
                return
            payout = payout_usdc if payout_usdc is not None else position.attributed_size
            cost = position.attributed_cost
            realized = payout - cost
            failed_redemption_error = existing.last_error if existing is not None else None
            resolved_transaction_hash = transaction_hash or (
                existing.transaction_hash if existing is not None else None
            )
            reconciliation_note = "未找到执行钱包赎回交易哈希，按终态结算事件与链上余额完成对账。"
            reconciliation_error = None
            if not resolved_transaction_hash:
                reconciliation_error = (
                    f"{failed_redemption_error}\n对账说明：{reconciliation_note}"
                    if failed_redemption_error
                    else reconciliation_note
                )
            if existing is None:
                session.add(
                    CopyRedemption(
                        copy_position_id=position.id,
                        status="reconciled",
                        size=position.attributed_size,
                        payout_usdc=payout,
                        transaction_hash=resolved_transaction_hash,
                        attempts=0,
                        last_error=reconciliation_error,
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                )
            else:
                existing.status = "reconciled"
                existing.payout_usdc = payout
                existing.transaction_hash = resolved_transaction_hash
                existing.last_error = reconciliation_error
                existing.updated_at = utcnow()
            position.attributed_size = ZERO
            position.attributed_cost = ZERO
            position.realized_pnl += realized
            position.status = "reconciled"
            position.updated_at = utcnow()
            subscription = await session.get(CopySubscription, position.subscription_id)
            if (
                subscription is not None
                and failed_redemption_error
                and subscription.last_error == failed_redemption_error
            ):
                subscription.last_error = None
                subscription.updated_at = utcnow()
            session.add(
                CopyLedger(
                    copy_position_id=position.id,
                    subscription_id=position.subscription_id,
                    order_id=None,
                    type="reconcile_redeem",
                    amount_usdc=payout,
                    realized_pnl=realized,
                    detail=detail,
                    timestamp=utcnow(),
                )
            )
            await session.commit()

    async def _leader_opened(
        self,
        subscription_id: int,
        event_id: int,
        *,
        ratio_percent: Decimal | None = None,
        strict_minimum: bool = False,
        idempotency_scope: str = "open",
    ) -> None:
        async with self.database.sessions() as session:
            subscription = await session.get(CopySubscription, subscription_id)
            event = await session.get(PositionEvent, event_id)
            if subscription is None or event is None:
                return
            prior_order = await session.scalar(
                select(CopyOrder.id).where(
                    CopyOrder.leader_event_id == event_id,
                    CopyOrder.side == "BUY",
                    CopyOrder.source == "copy",
                )
            )
            if prior_order is not None:
                return
            active_position = await session.scalar(
                select(CopyPosition).where(
                    CopyPosition.subscription_id == subscription_id,
                    CopyPosition.asset_id == event.asset_id,
                    CopyPosition.attributed_size > ZERO,
                )
            )
            if active_position is not None:
                return
            monitored_position = await session.scalar(
                select(CurrentPosition).where(
                    CurrentPosition.wallet_id == event.wallet_id,
                    CurrentPosition.asset_id == event.asset_id,
                )
            )
            cycle_no = (
                int(
                    (
                        await session.scalar(
                            select(func.max(CopyPosition.cycle_no)).where(
                                CopyPosition.subscription_id == subscription_id,
                                CopyPosition.asset_id == event.asset_id,
                            )
                        )
                    )
                    or 0
                )
                + 1
            )
            account = await session.get(ExecutionAccount, 1)

        leader_purchase_usdc = await self._leader_purchase_usdc(event)
        if ratio_percent is None:
            if subscription.strategy_mode == "large_increase":
                ratio_percent = subscription.base_entry_ratio_percent
                strict_minimum = True
                if leader_purchase_usdc < subscription.base_entry_threshold_usdc:
                    await self._record_skip(
                        subscription_id,
                        event,
                        "底仓金额未达到建仓阈值",
                        proportional_target_usdc=leader_purchase_usdc
                        * ratio_percent
                        / Decimal("100"),
                    )
                    return
            else:
                ratio_percent = subscription.copy_ratio_percent
        requested = leader_purchase_usdc * ratio_percent / Decimal("100")

        if not self.settings.live_copy_enabled:
            await self._record_skip(subscription_id, event, "自动实盘已被系统紧急停用")
            return
        if account is None or account.status != "ready":
            await self._record_skip(subscription_id, event, "执行账户尚未通过余额验证")
            return
        trader = await self._trader()
        balance = await trader.collateral_balance()
        account.collateral_balance = balance
        account.last_balance_at = utcnow()
        account.status = "ready" if balance > account.cash_reserve_usdc else "insufficient_balance"
        async with self.database.sessions() as session:
            stored_account = await session.get(ExecutionAccount, account.id)
            assert stored_account is not None
            stored_account.collateral_balance = balance
            stored_account.last_balance_at = account.last_balance_at
            stored_account.status = account.status
            stored_account.updated_at = utcnow()
            await session.commit()
        if account.status != "ready":
            await self._record_skip(subscription_id, event, "执行钱包余额未高于现金保留额")
            return

        book = await self.client.fetch_order_book(event.asset_id)
        if book.best_ask is None:
            await self._record_skip(subscription_id, event, "市场当前没有可成交卖盘")
            return
        worst_price = market_worst_price(
            book.best_ask, book.tick_size, subscription.market_slippage_cents, side="BUY"
        )
        minimum_order_usdc = book.min_order_size * worst_price
        usage = await self._risk_usage(subscription_id, account)
        requested_for_risk = requested if strict_minimum else max(requested, minimum_order_usdc)
        allowed, reason = allowed_buy_usdc(
            requested_for_risk,
            subscription=subscription,
            usage=usage,
        )
        if allowed <= ZERO:
            await self._record_skip(subscription_id, event, reason or "风险额度不足")
            return
        if allowed / worst_price < book.min_order_size:
            minimum_reason = (
                "按执行比例计算后低于市场最小下单份数"
                if strict_minimum and requested < minimum_order_usdc
                else "剩余风控额度低于市场最小下单金额"
            )
            await self._record_skip(
                subscription_id,
                event,
                minimum_reason,
                proportional_target_usdc=requested,
            )
            return

        now = utcnow()
        async with self.database.sessions() as session:
            position = CopyPosition(
                subscription_id=subscription_id,
                asset_id=event.asset_id,
                condition_id=event.condition_id,
                title=event.title,
                outcome=event.outcome,
                outcome_index=(
                    monitored_position.outcome_index if monitored_position is not None else None
                ),
                neg_risk=book.neg_risk,
                event_slug=event.event_slug,
                settlement_date=None,
                cycle_no=cycle_no,
                attributed_size=ZERO,
                attributed_cost=ZERO,
                reserved_buy_usdc=allowed,
                realized_pnl=ZERO,
                status="opening",
                created_at=now,
                updated_at=now,
            )
            session.add(position)
            await session.flush()
            order = self._new_order(
                subscription_id=subscription_id,
                position_id=position.id,
                event=event,
                side="BUY",
                amount=allowed,
                price=worst_price,
                reference=book.best_ask,
                key=f"copy:{idempotency_scope}:{event.id}",
                leader_purchase_usdc=leader_purchase_usdc,
                proportional_target_usdc=requested,
            )
            session.add(order)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return
            order_id = order.id
        request = MarketTradeRequest(
            asset_id=event.asset_id,
            side="BUY",
            amount=allowed,
            worst_price=worst_price,
            neg_risk=book.neg_risk,
        )
        await self._execute_order(order_id, request, book)

    async def _leader_increased(self, subscription_id: int, event_id: int) -> None:
        async with self.database.sessions() as session:
            subscription = await session.get(CopySubscription, subscription_id)
            event = await session.get(PositionEvent, event_id)
            if subscription is None or event is None:
                return
            prior_order = await session.scalar(
                select(CopyOrder.id).where(
                    CopyOrder.leader_event_id == event_id,
                    CopyOrder.side == "BUY",
                    CopyOrder.source == "copy",
                )
            )
            if prior_order is not None:
                return
            leader_purchase_usdc = await self._leader_purchase_usdc(event)
            if subscription.strategy_mode == "large_increase":
                if leader_purchase_usdc >= subscription.tier_two_threshold_usdc:
                    ratio_percent = subscription.tier_two_ratio_percent
                elif leader_purchase_usdc >= subscription.tier_one_threshold_usdc:
                    ratio_percent = subscription.tier_one_ratio_percent
                else:
                    await self._record_skip(
                        subscription_id,
                        event,
                        "单次净加仓未达到第一档阈值",
                        proportional_target_usdc=ZERO,
                    )
                    return
            else:
                if leader_purchase_usdc < subscription.large_increase_threshold_usdc:
                    return
                ratio_percent = subscription.copy_ratio_percent
            position = await session.scalar(
                select(CopyPosition)
                .where(
                    CopyPosition.subscription_id == subscription_id,
                    CopyPosition.asset_id == event.asset_id,
                    CopyPosition.attributed_size > ZERO,
                )
                .order_by(CopyPosition.cycle_no.desc())
                .limit(1)
            )
            account = await session.get(ExecutionAccount, 1)

        if position is None and subscription.strategy_mode == "large_increase":
            await self._leader_opened(
                subscription_id,
                event_id,
                ratio_percent=ratio_percent,
                strict_minimum=True,
                idempotency_scope="increase",
            )
            return
        if position is None:
            await self._record_skip(
                subscription_id,
                event,
                "首次建仓未成功，不追随后续加仓",
            )
            return
        if position.reserved_buy_usdc > ZERO:
            await self._record_skip(subscription_id, event, "该仓位已有未决买单")
            return
        if not self.settings.live_copy_enabled:
            await self._record_skip(subscription_id, event, "自动实盘已被系统紧急停用")
            return
        if account is None or account.status != "ready":
            await self._record_skip(subscription_id, event, "执行账户尚未通过余额验证")
            return
        trader = await self._trader()
        balance = await trader.collateral_balance()
        account.collateral_balance = balance
        account.last_balance_at = utcnow()
        account.status = "ready" if balance > account.cash_reserve_usdc else "insufficient_balance"
        async with self.database.sessions() as session:
            stored_account = await session.get(ExecutionAccount, account.id)
            assert stored_account is not None
            stored_account.collateral_balance = balance
            stored_account.last_balance_at = account.last_balance_at
            stored_account.status = account.status
            stored_account.updated_at = utcnow()
            await session.commit()
        if account.status != "ready":
            await self._record_skip(subscription_id, event, "执行钱包余额未高于现金保留额")
            return

        book = await self.client.fetch_order_book(event.asset_id)
        if book.best_ask is None:
            await self._record_skip(subscription_id, event, "市场当前没有可成交卖盘")
            return
        requested = leader_purchase_usdc * ratio_percent / Decimal("100")
        usage = await self._risk_usage(subscription_id, account, position_id=position.id)
        allowed, reason = allowed_buy_usdc(requested, subscription=subscription, usage=usage)
        if allowed <= ZERO:
            await self._record_skip(subscription_id, event, reason or "风险额度不足")
            return
        worst_price = market_worst_price(
            book.best_ask, book.tick_size, subscription.market_slippage_cents, side="BUY"
        )
        if allowed / worst_price < book.min_order_size:
            await self._record_skip(subscription_id, event, "按执行比例计算后低于市场最小下单份数")
            return

        async with self.database.sessions() as session:
            current = await session.get(CopyPosition, position.id)
            if (
                current is None
                or current.attributed_size <= ZERO
                or current.reserved_buy_usdc > ZERO
            ):
                return
            current.reserved_buy_usdc = allowed
            current.updated_at = utcnow()
            order = self._new_order(
                subscription_id=subscription_id,
                position_id=current.id,
                event=event,
                side="BUY",
                amount=allowed,
                price=worst_price,
                reference=book.best_ask,
                key=f"copy:increase:{event.id}",
                leader_purchase_usdc=leader_purchase_usdc,
                proportional_target_usdc=requested,
            )
            session.add(order)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return
            order_id = order.id
            neg_risk = bool(current.neg_risk)
        await self._execute_order(
            order_id,
            MarketTradeRequest(
                asset_id=event.asset_id,
                side="BUY",
                amount=allowed,
                worst_price=worst_price,
                neg_risk=neg_risk,
            ),
            book,
        )

    async def _leader_closed(self, subscription_id: int, event_id: int) -> None:
        await self._close_asset(subscription_id, event_id, redeem=False)

    async def _leader_redeemed(self, subscription_id: int, event_id: int) -> None:
        await self._close_asset(subscription_id, event_id, redeem=True)

    async def _close_asset(self, subscription_id: int, event_id: int, *, redeem: bool) -> None:
        async with self.database.sessions() as session:
            subscription = await session.get(CopySubscription, subscription_id)
            event = await session.get(PositionEvent, event_id)
            if subscription is None or event is None:
                return
            account = await session.get(ExecutionAccount, 1)
            if redeem and account is not None and not account.auto_redeem:
                return
            position = await session.scalar(
                select(CopyPosition)
                .where(
                    CopyPosition.subscription_id == subscription_id,
                    CopyPosition.asset_id == event.asset_id,
                    CopyPosition.attributed_size > ZERO,
                )
                .order_by(CopyPosition.cycle_no.desc())
                .limit(1)
            )
            if position is None:
                return
            if redeem:
                existing = await session.scalar(
                    select(CopyRedemption).where(CopyRedemption.copy_position_id == position.id)
                )
                if existing is not None:
                    if (
                        existing.status != "pending"
                        or existing.transaction_hash is not None
                        or not redemption_error_is_retryable(existing.last_error)
                    ):
                        return
                    if existing.payout_usdc is None:
                        existing.payout_usdc = event_payout_for_size(
                            event,
                            position.attributed_size,
                        )
                        existing.updated_at = utcnow()
                        await session.commit()
                    redemption_id = existing.id
                else:
                    payout = event_payout_for_size(event, position.attributed_size)
                    redemption = CopyRedemption(
                        copy_position_id=position.id,
                        status="pending",
                        size=position.attributed_size,
                        payout_usdc=payout,
                        transaction_hash=None,
                        attempts=0,
                        last_error=None,
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                    session.add(redemption)
                    position.status = "redeeming"
                    position.updated_at = utcnow()
                    await session.commit()
                    redemption_id = redemption.id
            else:
                prior_order = await session.scalar(
                    select(CopyOrder.id).where(
                        CopyOrder.leader_event_id == event_id,
                        CopyOrder.side == "SELL",
                        CopyOrder.source == "copy",
                    )
                )
                if prior_order is not None:
                    return
                size = position.attributed_size
                position_id = position.id
                neg_risk = bool(position.neg_risk)
        if redeem:
            await self._execute_redemption(redemption_id, event_id)
            return

        try:
            book = await self.client.fetch_order_book(event.asset_id)
        except PolymarketAPIError:
            resolutions = await self.client.fetch_market_resolutions([event.condition_id])
            resolution = resolutions.get(event.condition_id)
            payout = (
                resolution.payout_by_asset_id.get(event.asset_id)
                if resolution is not None
                else None
            )
            if payout == ZERO:
                await self._record_resolved_loss(position_id, resolution.resolved_at)
                return
            if payout == ONE:
                await self._leader_redeemed(subscription_id, event_id)
                return
            raise
        if book.best_bid is None:
            raise TradingUnavailable("市场当前没有可成交买盘，完整清仓未执行")
        worst_price = market_worst_price(
            book.best_bid, book.tick_size, subscription.market_slippage_cents, side="SELL"
        )
        async with self.database.sessions() as session:
            event = await session.get(PositionEvent, event_id)
            assert event is not None
            order = self._new_order(
                subscription_id=subscription_id,
                position_id=position_id,
                event=event,
                side="SELL",
                amount=size,
                price=worst_price,
                reference=book.best_bid,
                key=f"copy:close:{event.id}",
            )
            session.add(order)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return
            order_id = order.id
        request = MarketTradeRequest(
            asset_id=event.asset_id,
            side="SELL",
            amount=size,
            worst_price=worst_price,
            neg_risk=neg_risk,
        )
        await self._execute_order(order_id, request, book)

    async def _record_resolved_loss(self, position_id: int, resolved_at: datetime) -> None:
        """Close a losing resolved outcome that no longer has a CLOB book."""
        async with self.database.sessions() as session:
            position = await session.get(CopyPosition, position_id)
            if position is None or position.attributed_size <= ZERO:
                return
            cost = position.attributed_cost
            position.attributed_size = ZERO
            position.attributed_cost = ZERO
            position.reserved_buy_usdc = ZERO
            position.realized_pnl -= cost
            position.status = "settled_loss"
            position.updated_at = utcnow()
            session.add(
                CopyLedger(
                    subscription_id=position.subscription_id,
                    copy_position_id=position.id,
                    order_id=None,
                    type="settle_loss",
                    amount_usdc=ZERO,
                    realized_pnl=-cost,
                    detail="市场已结算且该 outcome 兑付为 0，按已结算亏损入账",
                    timestamp=resolved_at,
                )
            )
            await session.commit()

    async def _leader_purchase_usdc(self, event: PositionEvent) -> Decimal:
        """Return the source wallet's net position-cost increase for this event."""
        net_cost_increase = (
            event.after_size * event.after_avg_price - event.before_size * event.before_avg_price
        )
        if net_cost_increase > ZERO:
            return net_cost_increase
        async with self.database.sessions() as session:
            buy_amount = await session.scalar(
                select(func.sum(PositionEventFill.amount)).where(
                    PositionEventFill.event_id == event.id, PositionEventFill.side == "BUY"
                )
            )
            sell_amount = await session.scalar(
                select(func.sum(PositionEventFill.amount)).where(
                    PositionEventFill.event_id == event.id, PositionEventFill.side == "SELL"
                )
            )
        net_fill_amount = (buy_amount or ZERO) - (sell_amount or ZERO)
        if net_fill_amount > ZERO:
            return net_fill_amount
        reference = event.average_fill_price or event.after_avg_price
        return max(ZERO, event.delta_size * reference)

    def _new_order(
        self,
        *,
        subscription_id: int | None,
        position_id: int | None,
        event: PositionEvent | None,
        side: str,
        amount: Decimal,
        price: Decimal,
        reference: Decimal,
        key: str,
        asset_id: str | None = None,
        condition_id: str | None = None,
        source: str = "copy",
        leader_purchase_usdc: Decimal | None = None,
        proportional_target_usdc: Decimal | None = None,
    ) -> CopyOrder:
        requested_size = amount / price if side == "BUY" else amount
        requested_usdc = amount if side == "BUY" else amount * price
        resolved_asset_id = event.asset_id if event is not None else asset_id
        resolved_condition_id = event.condition_id if event is not None else condition_id
        if not resolved_asset_id or not resolved_condition_id:
            raise ValueError("创建订单需要 asset_id 和 condition_id")
        now = utcnow()
        return CopyOrder(
            subscription_id=subscription_id,
            copy_position_id=position_id,
            leader_event_id=event.id if event is not None and source == "copy" else None,
            idempotency_key=key,
            source=source,
            signed_order_hash=None,
            execution_provider="unified_sdk",
            asset_id=resolved_asset_id,
            condition_id=resolved_condition_id,
            side=side,
            requested_size=requested_size,
            requested_usdc=requested_usdc,
            leader_purchase_usdc=leader_purchase_usdc,
            proportional_target_usdc=proportional_target_usdc,
            limit_price=price,
            reference_price=reference,
            filled_size=ZERO,
            filled_usdc=ZERO,
            fee_usdc=ZERO,
            status="planned",
            reason=None,
            external_order_id=None,
            external_trade_id=None,
            created_at=now,
            updated_at=now,
        )

    async def _execute_order(
        self, order_id: int, request: MarketTradeRequest, book: OrderBookSnapshot
    ) -> None:
        buy_blocked = False
        async with self.database.sessions() as session:
            order = await session.get(CopyOrder, order_id)
            if order is None or order.status != "planned":
                return
            subscription = (
                await session.get(CopySubscription, order.subscription_id)
                if order.subscription_id is not None
                else None
            )
            if order.side == "BUY" and subscription is not None and subscription.state != "active":
                buy_blocked = True
        if buy_blocked:
            await self._mark_order_failed(order_id, "实盘策略已关闭，新买入已取消")
            return
        if request.side == "BUY" and not self.settings.live_copy_enabled:
            await self._mark_order_failed(order_id, "自动实盘已被系统紧急停用")
            return
        trader = await self._trader()
        prepared = await trader.prepare_market(request)
        async with self.database.sessions() as session:
            order = await session.get(CopyOrder, order_id)
            if order is None or order.status != "planned":
                return
            order.signed_order_hash = prepared.signed_order_hash
            order.status = "signed"
            order.updated_at = utcnow()
            await session.commit()
        try:
            result = await trader.submit_prepared_market(prepared)
        except TradingUnavailable as error:
            # A signed request may have reached the exchange. Never resubmit it blindly.
            async with self.database.sessions() as session:
                order = await session.get(CopyOrder, order_id)
                if order is not None:
                    order.status = "reconciliation_pending"
                    order.reason = str(error)[:1000]
                    order.updated_at = utcnow()
                    await session.commit()
            return
        if result.external_trade_id and result.fee_usdc == ZERO:
            for attempt in range(3):
                fee = await trader.trade_fee(result.external_trade_id)
                if fee > ZERO:
                    result = replace(result, fee_usdc=fee)
                    break
                if attempt < 2:
                    await asyncio.sleep(1)
        await self._apply_result(order_id, result)

    async def _apply_result(self, order_id: int, result: TradeResult) -> None:
        async with self.database.sessions() as session:
            order = await session.get(CopyOrder, order_id)
            if order is None:
                return
            result = normalize_fak_result(
                MarketTradeRequest(
                    asset_id=order.asset_id,
                    side=order.side,
                    amount=(order.requested_usdc if order.side == "BUY" else order.requested_size),
                    worst_price=order.limit_price,
                ),
                result,
            )
            order.status = result.status
            order.external_order_id = result.external_order_id
            order.external_trade_id = result.external_trade_id
            order.signed_order_hash = result.signed_order_hash or order.signed_order_hash
            order.filled_size = result.filled_size
            order.filled_usdc = result.filled_usdc
            order.fee_usdc = result.fee_usdc
            order.reason = result.reason
            order.updated_at = utcnow()
            position = (
                await session.get(CopyPosition, order.copy_position_id)
                if order.copy_position_id is not None
                else None
            )
            leader_event = (
                await session.get(PositionEvent, order.leader_event_id)
                if order.leader_event_id is not None
                else None
            )
            if result.filled_size > ZERO:
                fingerprint = hashlib.sha256(
                    f"{order.id}|{result.external_trade_id or result.external_order_id or 'local'}|"
                    f"{result.filled_size}|{result.filled_usdc}".encode()
                ).hexdigest()
                if result.fills:
                    existing = await session.scalar(
                        select(CopyFill.id).where(
                            CopyFill.order_id == order.id,
                            CopyFill.external_trade_id.in_(
                                [fill.external_trade_id for fill in result.fills]
                            ),
                        )
                    )
                else:
                    existing = await session.scalar(
                        select(CopyFill.id).where(CopyFill.fingerprint == fingerprint)
                    )
                if existing is None:
                    average = result.average_price or result.filled_usdc / result.filled_size
                    if result.fills:
                        for fill in result.fills:
                            fill_fingerprint = hashlib.sha256(
                                f"{order.id}|{fill.external_trade_id}|{fill.size}|"
                                f"{fill.price}|{fill.bucket_index}".encode()
                            ).hexdigest()
                            session.add(
                                CopyFill(
                                    order_id=order.id,
                                    fingerprint=fill_fingerprint,
                                    external_trade_id=fill.external_trade_id,
                                    transaction_hash=fill.transaction_hash,
                                    bucket_index=fill.bucket_index,
                                    settlement_status=fill.settlement_status,
                                    size=fill.size,
                                    price=fill.price,
                                    amount=fill.amount,
                                    fee_usdc=fill.fee_usdc,
                                    timestamp=utcnow(),
                                )
                            )
                    else:
                        session.add(
                            CopyFill(
                                order_id=order.id,
                                fingerprint=fingerprint,
                                external_trade_id=result.external_trade_id,
                                transaction_hash=None,
                                bucket_index=None,
                                settlement_status="confirmed",
                                size=result.filled_size,
                                price=average,
                                amount=result.filled_usdc,
                                fee_usdc=result.fee_usdc,
                                timestamp=utcnow(),
                            )
                        )
                    if position is not None and order.side == "BUY":
                        position.attributed_size += result.filled_size
                        position.attributed_cost += result.filled_usdc + result.fee_usdc
                        position.reserved_buy_usdc = ZERO
                        position.status = "open"
                        session.add(
                            CopyLedger(
                                subscription_id=order.subscription_id,
                                copy_position_id=position.id,
                                order_id=order.id,
                                type="buy",
                                amount_usdc=result.filled_usdc + result.fee_usdc,
                                realized_pnl=ZERO,
                                detail=(
                                    "观察钱包大额加仓"
                                    if leader_event is not None and leader_event.type == "increased"
                                    else "三事件策略建仓"
                                ),
                                timestamp=utcnow(),
                            )
                        )
                    elif position is not None and order.side == "SELL":
                        sold = min(position.attributed_size, result.filled_size)
                        cost = (
                            position.attributed_cost * sold / position.attributed_size
                            if position.attributed_size > ZERO
                            else ZERO
                        )
                        proceeds = result.filled_usdc - result.fee_usdc
                        pnl = proceeds - cost
                        position.attributed_size -= sold
                        position.attributed_cost -= cost
                        position.realized_pnl += pnl
                        position.status = (
                            "closed" if position.attributed_size <= ZERO else "manual_exit"
                        )
                        session.add(
                            CopyLedger(
                                subscription_id=order.subscription_id,
                                copy_position_id=position.id,
                                order_id=order.id,
                                type="sell",
                                amount_usdc=proceeds,
                                realized_pnl=pnl,
                                detail="观察钱包归零后完整清仓",
                                timestamp=utcnow(),
                            )
                        )
                        if ZERO < position.attributed_size < UNTRADEABLE_DUST_SIZE:
                            dust_cost = position.attributed_cost
                            position.attributed_size = ZERO
                            position.attributed_cost = ZERO
                            position.realized_pnl -= dust_cost
                            position.status = "dust_closed"
                            session.add(
                                CopyLedger(
                                    subscription_id=order.subscription_id,
                                    copy_position_id=position.id,
                                    order_id=order.id,
                                    type="dust_writeoff",
                                    amount_usdc=ZERO,
                                    realized_pnl=-dust_cost,
                                    detail="卖出后剩余数量低于最小可交易粒度，按零值核销",
                                    timestamp=utcnow(),
                                )
                            )
                    if position is not None:
                        position.updated_at = utcnow()
            elif position is not None and order.side == "BUY":
                position.reserved_buy_usdc = ZERO
                position.status = "open" if position.attributed_size > ZERO else "not_opened"
                position.updated_at = utcnow()
            await session.commit()

    async def _record_skip(
        self,
        subscription_id: int,
        event: PositionEvent,
        reason: str,
        *,
        proportional_target_usdc: Decimal | None = None,
    ) -> None:
        async with self.database.sessions() as session:
            subscription = await session.get(CopySubscription, subscription_id)
        leader_purchase_usdc = await self._leader_purchase_usdc(event)
        if proportional_target_usdc is None:
            if subscription is None:
                proportional_target_usdc = None
            elif subscription.strategy_mode == "large_increase" and event.type == "opened":
                proportional_target_usdc = (
                    leader_purchase_usdc * subscription.base_entry_ratio_percent / Decimal("100")
                )
            elif subscription.strategy_mode == "large_increase" and event.type == "increased":
                ratio = (
                    subscription.tier_two_ratio_percent
                    if leader_purchase_usdc >= subscription.tier_two_threshold_usdc
                    else subscription.tier_one_ratio_percent
                    if leader_purchase_usdc >= subscription.tier_one_threshold_usdc
                    else ZERO
                )
                proportional_target_usdc = leader_purchase_usdc * ratio / Decimal("100")
            else:
                proportional_target_usdc = (
                    leader_purchase_usdc * subscription.copy_ratio_percent / Decimal("100")
                )
        order = self._new_order(
            subscription_id=subscription_id,
            position_id=None,
            event=event,
            side="BUY",
            amount=ZERO,
            price=event.after_avg_price or Decimal("0.01"),
            reference=event.after_avg_price or Decimal("0.01"),
            key=f"copy:{'increase' if event.type == 'increased' else 'open'}:{event.id}",
            leader_purchase_usdc=leader_purchase_usdc,
            proportional_target_usdc=proportional_target_usdc,
        )
        order.status = "skipped"
        order.reason = reason
        async with self.database.sessions() as session:
            session.add(order)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()

    async def _risk_usage(
        self,
        subscription_id: int,
        account: ExecutionAccount | None,
        *,
        position_id: int | None = None,
    ) -> RiskUsage:
        local_start = datetime.now(SHANGHAI).replace(hour=0, minute=0, second=0, microsecond=0)
        utc_start = local_start.astimezone(UTC).replace(tzinfo=None)
        async with self.database.sessions() as session:
            positions = list(
                (
                    await session.scalars(
                        select(CopyPosition).where(
                            CopyPosition.subscription_id == subscription_id,
                            (CopyPosition.attributed_size > ZERO)
                            | (CopyPosition.reserved_buy_usdc > ZERO),
                        )
                    )
                ).all()
            )
            ledger = list(
                (
                    await session.scalars(
                        select(CopyLedger).where(
                            CopyLedger.timestamp >= utc_start,
                        )
                    )
                ).all()
            )
            global_positions = list(
                (
                    await session.scalars(
                        select(CopyPosition).where(
                            (CopyPosition.attributed_size > ZERO)
                            | (CopyPosition.reserved_buy_usdc > ZERO),
                        )
                    )
                ).all()
            )
        total = sum((p.attributed_cost + p.reserved_buy_usdc for p in positions), ZERO)
        position_total = sum(
            (
                p.attributed_cost + p.reserved_buy_usdc
                for p in positions
                if position_id is not None and p.id == position_id
            ),
            ZERO,
        )
        global_total = sum(
            (p.attributed_cost + p.reserved_buy_usdc for p in global_positions),
            ZERO,
        )
        bought = sum((row.amount_usdc for row in ledger if row.type == "buy"), ZERO)
        realized = sum((row.realized_pnl for row in ledger), ZERO)
        if account is None:
            return RiskUsage(
                position=position_total,
                total=total,
                global_total=global_total,
                wallet_capital=Decimal("999999999"),
                bought_today=bought,
            )
        balance = account.collateral_balance or ZERO
        capital = max(ZERO, min(account.budget_usdc, balance) - account.cash_reserve_usdc)
        return RiskUsage(
            position=position_total,
            total=total,
            global_total=global_total,
            bought_today=bought,
            realized_loss_today=max(ZERO, -realized),
            wallet_capital=min(capital, account.max_total_exposure_usdc),
            account_daily_buy_limit=account.daily_buy_limit_usdc,
            account_daily_loss_limit=account.daily_loss_limit_usdc,
        )

    async def _execute_redemption(self, redemption_id: int, event_id: int | None) -> None:
        async with self.database.sessions() as session:
            redemption = await session.get(CopyRedemption, redemption_id)
            event = await session.get(PositionEvent, event_id) if event_id is not None else None
            if (
                redemption is None
                or redemption.status != "pending"
                or (event_id is not None and event is None)
            ):
                return
            position = await session.get(CopyPosition, redemption.copy_position_id)
            if position is None:
                return
            size = redemption.size
            payout = redemption.payout_usdc if redemption.payout_usdc is not None else size
            asset_id = position.asset_id
            condition_id = position.condition_id
            outcome_index = position.outcome_index
            neg_risk = bool(position.neg_risk)
        trader = await self._trader()
        if hasattr(trader, "start_redemption"):
            try:
                await self._execute_unified_redemption(redemption_id)
            except Exception as error:
                async with self.database.sessions() as session:
                    redemption = await session.get(CopyRedemption, redemption_id)
                    if redemption is not None and redemption.status == "pending":
                        redemption.attempts += 1
                        redemption.last_error = str(error)[:1000]
                        redemption.updated_at = utcnow()
                        await session.commit()
                raise
            return
        try:
            if not neg_risk:
                payout_rate = await trader.onchain_redemption_payout_rate(
                    condition_id,
                    outcome_index,
                    neg_risk,
                )
                if payout_rate is None:
                    raise TradingUnavailable("等待市场完成链上结算后再自动赎回")
                if payout_rate <= ZERO:
                    raise TradingUnavailable("链上结算结果没有可赎回金额")
                payout = size * payout_rate
            transaction_hash = await trader.redeem(
                condition_id=condition_id,
                size=size,
                outcome_index=outcome_index,
                neg_risk=neg_risk,
            )
            remaining_balance = await trader.onchain_outcome_balance(asset_id)
            if remaining_balance > REDEMPTION_SIZE_TOLERANCE:
                raise TradingUnavailable(
                    f"自动赎回链上交易 {transaction_hash} 未消耗 outcome token，任务保持待处理"
                )
        except Exception as error:
            async with self.database.sessions() as session:
                redemption = await session.get(CopyRedemption, redemption_id)
                if redemption is not None and redemption.status == "pending":
                    redemption.attempts += 1
                    redemption.last_error = str(error)[:1000]
                    transaction_hash = getattr(error, "transaction_hash", None)
                    if transaction_hash:
                        redemption.transaction_hash = str(transaction_hash)[:100]
                    redemption.updated_at = utcnow()
                    await session.commit()
            raise
        async with self.database.sessions() as session:
            redemption = await session.get(CopyRedemption, redemption_id)
            if redemption is None or redemption.status != "pending":
                return
            position = await session.get(CopyPosition, redemption.copy_position_id)
            assert position is not None
            failed_redemption_error = redemption.last_error
            cost = position.attributed_cost
            realized = payout - cost
            redemption.status = "completed"
            redemption.payout_usdc = payout
            redemption.transaction_hash = transaction_hash
            redemption.attempts += 1
            redemption.last_error = None
            redemption.updated_at = utcnow()
            position.attributed_size = ZERO
            position.attributed_cost = ZERO
            position.realized_pnl += realized
            position.status = "redeemed"
            position.updated_at = utcnow()
            subscription = await session.get(CopySubscription, position.subscription_id)
            if (
                subscription is not None
                and failed_redemption_error
                and subscription.last_error == failed_redemption_error
            ):
                subscription.last_error = None
                subscription.updated_at = utcnow()
            session.add(
                CopyLedger(
                    subscription_id=position.subscription_id,
                    copy_position_id=position.id,
                    order_id=None,
                    type="redeem",
                    amount_usdc=payout,
                    realized_pnl=realized,
                    detail="观察钱包赎回后一次性赎回归因仓位",
                    timestamp=utcnow(),
                )
            )
            await session.commit()

    async def _execute_unified_redemption(self, redemption_id: int) -> None:
        trader = await self._trader()
        now = utcnow()
        async with self.database.sessions() as session:
            redemption = await session.get(CopyRedemption, redemption_id)
            account = await session.get(ExecutionAccount, 1)
            if redemption is None or redemption.status != "pending" or account is None:
                return
            if not account.auto_redeem or not account.funder_address:
                return
            funder_address = account.funder_address.lower()
            position = await session.get(CopyPosition, redemption.copy_position_id)
            if position is None or position.attributed_size <= ZERO:
                return
            condition_id = position.condition_id
            outcome_index = position.outcome_index
            neg_risk = bool(position.neg_risk)
            payout_rate = await trader.onchain_redemption_payout_rate(
                condition_id, outcome_index, neg_risk
            )
            if not neg_risk and payout_rate is None:
                raise TradingUnavailable("等待市场完成链上结算后再自动赎回")
            if payout_rate is not None and payout_rate <= ZERO:
                raise TradingUnavailable("链上结算结果没有可赎回金额")
            if payout_rate is not None:
                effective_payout_rate = payout_rate
            elif redemption.payout_usdc is not None and redemption.size > ZERO:
                effective_payout_rate = redemption.payout_usdc / redemption.size
            else:
                effective_payout_rate = ONE
            estimated_payout = position.attributed_size * effective_payout_rate
            execution = await session.scalar(
                select(CopyRedemptionExecution).where(
                    CopyRedemptionExecution.wallet_address == funder_address,
                    CopyRedemptionExecution.condition_id == condition_id,
                )
            )
            if execution is not None:
                redemption.execution_id = execution.id
                redemption.execution_provider = "unified_sdk"
                recoverable_review = (
                    execution.status == "manual_review"
                    and redemption_execution_was_never_submitted(execution)
                )
                if execution.status in {"submitting", "submitted", "completed"} or (
                    execution.status == "manual_review" and not recoverable_review
                ):
                    if execution.status == "completed":
                        redemption.status = "manual_review"
                        redemption.last_error = (
                            "同 condition 的统一 SDK 赎回已完成，需要按链上余额人工分账"
                        )
                    await session.commit()
                    return
            else:
                execution = CopyRedemptionExecution(
                    wallet_address=funder_address,
                    condition_id=condition_id,
                    method="redeem_positions",
                    execution_provider="unified_sdk",
                    status="pending",
                    estimated_payout_usdc=estimated_payout,
                    attempts=0,
                    created_at=now,
                    updated_at=now,
                )
                session.add(execution)
                await session.flush()
                redemption.execution_id = execution.id
                redemption.execution_provider = "unified_sdk"
            execution_id = execution.id
            siblings = list(
                (
                    await session.scalars(
                        select(CopyRedemption)
                        .join(CopyPosition)
                        .where(
                            CopyPosition.condition_id == condition_id,
                            CopyRedemption.status == "pending",
                        )
                    )
                ).all()
            )
            for sibling in siblings:
                sibling.execution_id = execution.id
                sibling.execution_provider = "unified_sdk"
            estimated_payout = sum(
                (
                    sibling.payout_usdc
                    if sibling.payout_usdc is not None
                    else sibling.size * effective_payout_rate
                    for sibling in siblings
                ),
                ZERO,
            )
            attributed_rows = (
                await session.execute(
                    select(CopyPosition.asset_id, func.sum(CopyPosition.attributed_size))
                    .where(
                        CopyPosition.condition_id == condition_id,
                        CopyPosition.attributed_size > ZERO,
                    )
                    .group_by(CopyPosition.asset_id)
                )
            ).all()
            attributed_by_asset = {
                str(linked_asset_id): Decimal(linked_size or ZERO)
                for linked_asset_id, linked_size in attributed_rows
            }
            execution.estimated_payout_usdc = estimated_payout
            execution.updated_at = utcnow()
            await session.commit()

        redeemable_before = await self.client.fetch_redeemable_positions(
            funder_address,
            condition_ids=[condition_id],
        )
        condition_positions = [
            wallet_position
            for wallet_position in redeemable_before
            if wallet_position.condition_id == condition_id
        ]
        if not condition_positions:
            raise TradingUnavailable("Data API 尚未将 condition 标记为可赎回")
        observed_asset_ids = {wallet_position.asset_id for wallet_position in condition_positions}
        chain_asset_ids = set(attributed_by_asset) | observed_asset_ids
        before_balances = {
            linked_asset_id: await trader.onchain_outcome_balance(linked_asset_id)
            for linked_asset_id in chain_asset_ids
        }
        before_outcome = sum(before_balances.values(), ZERO)
        before_pusd = await trader.onchain_collateral_balance()
        unattributed_assets = [
            linked_asset_id
            for linked_asset_id in observed_asset_ids - set(attributed_by_asset)
            if before_balances.get(linked_asset_id, ZERO) > REDEMPTION_SIZE_TOLERANCE
        ]
        if unattributed_assets:
            async with self.database.sessions() as session:
                execution = await session.get(CopyRedemptionExecution, execution_id)
                assert execution is not None
                execution.status = "manual_review"
                execution.last_error = "执行钱包包含有无法归因的同 condition token"
                execution.before_outcome_balance = before_outcome
                execution.updated_at = utcnow()
                await session.commit()
            return
        if any(
            not chain_redemption_sizes_match(
                before_balances.get(linked_asset_id, ZERO), attributed_size
            )
            for linked_asset_id, attributed_size in attributed_by_asset.items()
        ):
            async with self.database.sessions() as session:
                execution = await session.get(CopyRedemptionExecution, execution_id)
                assert execution is not None
                execution.status = "manual_review"
                execution.last_error = "执行钱包含有无法归因的同 outcome token，拒绝全量赎回"
                execution.before_outcome_balance = before_outcome
                execution.updated_at = utcnow()
                await session.commit()
            return

        async with self.database.sessions() as session:
            execution = await session.get(CopyRedemptionExecution, execution_id)
            assert execution is not None
            if execution.status == "manual_review":
                if not redemption_execution_was_never_submitted(execution):
                    return
                execution.status = "pending"
                execution.last_error = None
                execution.updated_at = utcnow()
                await session.commit()

        async with self.database.sessions() as session:
            execution = await session.get(CopyRedemptionExecution, execution_id)
            assert execution is not None
            execution.status = "submitting"
            execution.before_outcome_balance = before_outcome
            execution.before_pusd_balance = before_pusd
            execution.attempts += 1
            execution.updated_at = utcnow()
            await session.commit()

        try:
            prepared = await trader.start_redemption(
                condition_id=condition_id,
                neg_risk=neg_risk,
            )
        except Exception as error:
            async with self.database.sessions() as session:
                execution = await session.get(CopyRedemptionExecution, execution_id)
                redemption = await session.get(CopyRedemption, redemption_id)
                if execution is not None:
                    execution.status = "manual_review"
                    execution.last_error = str(error)[:1000]
                    execution.relayer_transaction_id = getattr(error, "transaction_id", None)
                    execution.transaction_hash = getattr(error, "transaction_hash", None)
                    execution.updated_at = utcnow()
                if redemption is not None:
                    redemption.status = "manual_review"
                    redemption.last_error = str(error)[:1000]
                    redemption.transaction_hash = getattr(error, "transaction_hash", None)
                    redemption.attempts += 1
                    redemption.updated_at = utcnow()
                await session.commit()
            raise

        async with self.database.sessions() as session:
            execution = await session.get(CopyRedemptionExecution, execution_id)
            redemption = await session.get(CopyRedemption, redemption_id)
            assert execution is not None and redemption is not None
            execution.status = "submitted"
            execution.relayer_transaction_id = prepared.transaction_id
            execution.transaction_hash = prepared.transaction_hash
            execution.submitted_at = utcnow()
            execution.updated_at = utcnow()
            redemption.status = "submitted"
            redemption.transaction_hash = prepared.transaction_hash
            redemption.attempts += 1
            redemption.updated_at = utcnow()
            await session.commit()

        try:
            transaction_hash = await trader.wait_redemption(prepared)
            receipt_ok = await trader.transaction_receipt_success(transaction_hash)
            after_balances = {
                linked_asset_id: await trader.onchain_outcome_balance(linked_asset_id)
                for linked_asset_id in chain_asset_ids
            }
            after_outcome = sum(after_balances.values(), ZERO)
            after_pusd = await trader.onchain_collateral_balance()
            actual_delta = after_pusd - before_pusd
            if receipt_ok is not True:
                raise TradingUnavailable("赎回交易尚未获得成功 receipt")
            if any(balance > REDEMPTION_SIZE_TOLERANCE for balance in after_balances.values()):
                raise TradingUnavailable("赎回确认后 outcome token 仍未归零")
            expected = estimated_payout
            if abs(actual_delta - expected) > Decimal("0.000001"):
                raise TradingUnavailable(f"赎回 pUSD 增量 {actual_delta} 与预期 {expected} 不一致")
            remaining = await self.client.fetch_redeemable_positions(
                funder_address,
                condition_ids=[condition_id],
            )
            if any(item.condition_id == condition_id for item in remaining):
                raise TradingUnavailable("Data API 仍将 condition token 标记为可赎回")
        except Exception as error:
            async with self.database.sessions() as session:
                execution = await session.get(CopyRedemptionExecution, execution_id)
                redemption = await session.get(CopyRedemption, redemption_id)
                if execution is not None:
                    execution.status = "manual_review"
                    execution.transaction_hash = (
                        str(getattr(error, "transaction_hash", None) or prepared.transaction_hash)
                        if getattr(error, "transaction_hash", None) or prepared.transaction_hash
                        else None
                    )
                    execution.last_error = str(error)[:1000]
                    execution.updated_at = utcnow()
                if redemption is not None:
                    redemption.status = "manual_review"
                    redemption.transaction_hash = execution.transaction_hash if execution else None
                    redemption.last_error = str(error)[:1000]
                    redemption.updated_at = utcnow()
                await session.commit()
            raise

        async with self.database.sessions() as session:
            execution = await session.get(CopyRedemptionExecution, execution_id)
            assert execution is not None
            execution.status = "completed"
            execution.transaction_hash = transaction_hash
            execution.after_outcome_balance = after_outcome
            execution.after_pusd_balance = after_pusd
            execution.actual_pusd_delta = actual_delta
            execution.completed_at = utcnow()
            execution.last_error = None
            execution.updated_at = utcnow()
            linked = list(
                (
                    await session.scalars(
                        select(CopyRedemption).where(
                            CopyRedemption.execution_id == execution_id,
                            CopyRedemption.status.in_(["pending", "submitted"]),
                        )
                    )
                ).all()
            )
            if not linked:
                linked = [await session.get(CopyRedemption, redemption_id)]  # type: ignore[list-item]
            for item in linked:
                if item is None:
                    continue
                linked_position = await session.get(CopyPosition, item.copy_position_id)
                if linked_position is None:
                    continue
                payout = (
                    item.payout_usdc
                    if item.payout_usdc is not None
                    else linked_position.attributed_size * effective_payout_rate
                )
                cost = linked_position.attributed_cost
                realized = payout - cost
                item.status = "completed"
                item.payout_usdc = payout
                item.transaction_hash = transaction_hash
                item.last_error = None
                item.updated_at = utcnow()
                linked_position.attributed_size = ZERO
                linked_position.attributed_cost = ZERO
                linked_position.realized_pnl += realized
                linked_position.status = "redeemed"
                linked_position.updated_at = utcnow()
                session.add(
                    CopyLedger(
                        subscription_id=linked_position.subscription_id,
                        copy_position_id=linked_position.id,
                        order_id=None,
                        type="redeem",
                        amount_usdc=payout,
                        realized_pnl=realized,
                        detail="统一 SDK condition 级自动赎回",
                        timestamp=utcnow(),
                    )
                )
            await session.commit()

    async def process_redemptions(self, subscription_id: int) -> None:
        async with self.database.sessions() as session:
            account = await session.get(ExecutionAccount, 1)
            if account is not None and not account.auto_redeem:
                return
            rows = list(
                (
                    await session.execute(
                        select(CopyRedemption, CopyRedemptionExecution)
                        .join(CopyPosition)
                        .outerjoin(
                            CopyRedemptionExecution,
                            CopyRedemptionExecution.id == CopyRedemption.execution_id,
                        )
                        .where(
                            CopyPosition.subscription_id == subscription_id,
                            CopyRedemption.status == "pending",
                        )
                    )
                ).all()
            )
        # Only errors known to occur before submission are retried. Unknown submission
        # results remain pending until their transaction or token balance is reconciled.
        for redemption, execution in rows:
            safe_pre_submit_review = (
                execution is not None
                and execution.status == "manual_review"
                and redemption_execution_was_never_submitted(execution)
            )
            if redemption.transaction_hash is None and (
                safe_pre_submit_review or redemption_error_is_retryable(redemption.last_error)
            ):
                await self._execute_redemption(redemption.id, event_id=None)

    async def reconcile_pending_orders(self, subscription_id: int) -> None:
        async with self.database.sessions() as session:
            interrupted = list(
                (
                    await session.scalars(
                        select(CopyOrder).where(
                            CopyOrder.subscription_id == subscription_id,
                            CopyOrder.status.in_(["planned", "signed"]),
                        )
                    )
                ).all()
            )
            for order in interrupted:
                order.status = (
                    "interrupted_before_submit"
                    if order.status == "planned"
                    else "reconciliation_pending"
                )
                order.reason = (
                    "服务重启前尚未签名，订单没有重试"
                    if order.status == "interrupted_before_submit"
                    else "服务重启时订单已签名，等待按签名哈希人工对账"
                )
                order.updated_at = utcnow()
                if order.copy_position_id is not None and order.side == "BUY":
                    position = await session.get(CopyPosition, order.copy_position_id)
                    if position is not None:
                        position.reserved_buy_usdc = ZERO
                        position.status = (
                            "open" if position.attributed_size > ZERO else "not_opened"
                        )
                        position.updated_at = utcnow()
            if interrupted:
                await session.commit()
            orders = list(
                (
                    await session.scalars(
                        select(CopyOrder).where(
                            CopyOrder.subscription_id == subscription_id,
                            CopyOrder.status.in_(["submitted", "reconciliation_pending"]),
                            CopyOrder.external_order_id.is_not(None),
                        )
                    )
                ).all()
            )
        if not orders:
            return
        trader = await self._trader()
        for order in orders:
            result = await trader.order_status(order.external_order_id)
            await self._apply_result(order.id, result)

    async def cancel_open_orders(self, subscription_id: int) -> None:
        # FAK orders never remain on the book. Only reconcile accepted responses.
        await self.reconcile_pending_orders(subscription_id)

    async def close_all_positions(self, subscription_id: int) -> None:
        async with self.database.sessions() as session:
            positions = list(
                (
                    await session.scalars(
                        select(CopyPosition).where(
                            CopyPosition.subscription_id == subscription_id,
                            CopyPosition.attributed_size > ZERO,
                        )
                    )
                ).all()
            )
        for position in positions:
            book = await self.client.fetch_order_book(position.asset_id)
            if book.best_bid is None:
                continue
            async with self.database.sessions() as session:
                subscription = await session.get(CopySubscription, subscription_id)
                current = await session.get(CopyPosition, position.id)
                if subscription is None or current is None or current.attributed_size <= ZERO:
                    continue
                worst_price = market_worst_price(
                    book.best_bid,
                    book.tick_size,
                    subscription.market_slippage_cents,
                    side="SELL",
                )
                size = current.attributed_size
                order = self._new_order(
                    subscription_id=subscription_id,
                    position_id=current.id,
                    event=None,
                    asset_id=current.asset_id,
                    condition_id=current.condition_id,
                    side="SELL",
                    amount=size,
                    price=worst_price,
                    reference=book.best_bid,
                    key=f"copy:manual-close:{current.id}",
                )
                session.add(order)
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    continue
                order_id = order.id
                neg_risk = bool(current.neg_risk)
            await self._execute_order(
                order_id,
                MarketTradeRequest(
                    asset_id=position.asset_id,
                    side="SELL",
                    amount=size,
                    worst_price=worst_price,
                    neg_risk=neg_risk,
                ),
                book,
            )

    async def _finish_closing(self, subscription_id: int) -> None:
        async with self.database.sessions() as session:
            subscription = await session.get(CopySubscription, subscription_id)
            if subscription is None or subscription.state != "closing":
                return
            has_position = await session.scalar(
                select(CopyPosition.id).where(
                    CopyPosition.subscription_id == subscription_id,
                    CopyPosition.attributed_size > ZERO,
                )
            )
        if has_position is not None:
            await self.close_all_positions(subscription_id)
        async with self.database.sessions() as session:
            subscription = await session.get(CopySubscription, subscription_id)
            if subscription is not None:
                remaining = await session.scalar(
                    select(CopyPosition.id).where(
                        CopyPosition.subscription_id == subscription_id,
                        CopyPosition.attributed_size > ZERO,
                    )
                )
                if remaining is None:
                    subscription.state = "disabled"
                    subscription.updated_at = utcnow()
                    await session.commit()

    async def _mark_order_failed(self, order_id: int, reason: str) -> None:
        async with self.database.sessions() as session:
            order = await session.get(CopyOrder, order_id)
            if order is not None:
                order.status = "blocked"
                order.reason = reason
                order.updated_at = utcnow()
                if order.copy_position_id is not None:
                    position = await session.get(CopyPosition, order.copy_position_id)
                    if position is not None and order.side == "BUY":
                        position.reserved_buy_usdc = ZERO
                        position.status = (
                            "open" if position.attributed_size > ZERO else "not_opened"
                        )
                        position.updated_at = utcnow()
                await session.commit()

    async def _trader(self) -> UnifiedPolymarketTrader:
        async with self.database.sessions() as session:
            account = await session.get(ExecutionAccount, 1)
        if (
            account is None
            or not account.keychain_service
            or not account.keychain_account
            or account.signature_type not in {1, 3}
        ):
            raise TradingUnavailable("V2 实盘要求已配置的 Proxy 或 Deposit Wallet")
        key = (
            account.keychain_service,
            account.keychain_account,
            account.signature_type,
            account.funder_address,
        )
        if self._trader_cache is None or self._trader_cache_key != key:
            if self._trader_cache is not None:
                await self._trader_cache.close()
            self._trader_cache = UnifiedPolymarketTrader(
                host=self.settings.clob_api_url,
                keychain=self.keychain,
                key_reference=KeychainReference(
                    service=account.keychain_service,
                    account=account.keychain_account,
                ),
                signature_type=account.signature_type,
                funder_address=account.funder_address,
                relayer_url=self.settings.relayer_api_url,
                rpc_url=self.settings.polygon_rpc_url,
            )
            self._trader_cache_key = key
        return self._trader_cache
