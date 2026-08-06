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
    CopySubscription,
    CurrentPosition,
    ExecutionAccount,
    PositionEvent,
)
from backend.polymarket import OrderBookSnapshot, PolymarketClient
from backend.trading import (
    MarketTradeRequest,
    OfficialClobTrader,
    TradeResult,
    TradingUnavailable,
    simulate_market_order,
)

ZERO = Decimal("0")
ONE = Decimal("1")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


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
        "跟单总敞口已满": subscription.total_exposure_cap_usdc - usage.total,
        "执行钱包当日买入额度已满": usage.account_daily_buy_limit - usage.bought_today,
        "执行钱包可用预算不足": usage.wallet_capital - usage.total,
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

    `opened` buys once, `closed` sells all attributed tokens and `redeemed`
    redeems all attributed tokens. `increased` and `decreased` are deliberately
    acknowledged without creating an order.
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
        self._trader_cache: OfficialClobTrader | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="simple-copy-trading-engine")

    def wake(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

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
                    self._wake.wait(), timeout=max(1.0, self.settings.copy_poll_interval_seconds)
                )
            except TimeoutError:
                pass

    async def tick(self) -> None:
        if self._lock.locked():
            return
        async with self._lock:
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
                    await self.process_position_events(subscription_id)
                    await self.process_redemptions(subscription_id)
                    await self._finish_closing(subscription_id)
                except Exception as error:
                    async with self.database.sessions() as session:
                        subscription = await session.get(CopySubscription, subscription_id)
                        if subscription is not None:
                            subscription.last_error = str(error)[:1000]
                            subscription.updated_at = utcnow()
                            await session.commit()

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
                elif event_type == "closed":
                    await self._leader_closed(subscription_id, event_id)
                elif event_type == "redeemed":
                    await self._leader_redeemed(subscription_id, event_id)
                # increased/decreased and opens while exit-only are intentionally ignored.
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

    async def _leader_opened(self, subscription_id: int, event_id: int) -> None:
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
            mode = subscription.mode

        book = await self.client.fetch_order_book(event.asset_id)
        if book.best_ask is None:
            await self._record_skip(subscription_id, event, "市场当前没有可成交卖盘")
            return
        reference = event.average_fill_price or event.after_avg_price or book.best_ask
        leader_cost = abs(event.delta_size) * reference
        requested = leader_cost * subscription.copy_ratio_percent / Decimal("100")
        usage = await self._risk_usage(subscription_id, account)
        allowed, reason = allowed_buy_usdc(requested, subscription=subscription, usage=usage)
        if allowed <= ZERO:
            await self._record_skip(subscription_id, event, reason or "风险额度不足")
            return
        worst_price = market_worst_price(
            book.best_ask, book.tick_size, subscription.market_slippage_cents, side="BUY"
        )
        if allowed / worst_price < book.min_order_size:
            await self._record_skip(subscription_id, event, "按跟单比例计算后低于市场最小下单份数")
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
                mode=mode,
                amount=allowed,
                price=worst_price,
                reference=book.best_ask,
                key=f"copy:open:{event.id}",
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
                    return
                redemption = CopyRedemption(
                    copy_position_id=position.id,
                    status="pending",
                    size=position.attributed_size,
                    payout_usdc=None,
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
                mode = subscription.mode
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
                mode = subscription.mode
                position_id = position.id
                neg_risk = bool(position.neg_risk)
        if redeem:
            await self._execute_redemption(redemption_id, event_id, mode)
            return

        book = await self.client.fetch_order_book(event.asset_id)
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
                mode=mode,
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

    def _new_order(
        self,
        *,
        subscription_id: int | None,
        position_id: int | None,
        event: PositionEvent | None,
        side: str,
        mode: str,
        amount: Decimal,
        price: Decimal,
        reference: Decimal,
        key: str,
        asset_id: str | None = None,
        condition_id: str | None = None,
        source: str = "copy",
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
            asset_id=resolved_asset_id,
            condition_id=resolved_condition_id,
            side=side,
            mode=mode,
            requested_size=requested_size,
            requested_usdc=requested_usdc,
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
        async with self.database.sessions() as session:
            order = await session.get(CopyOrder, order_id)
            if order is None or order.status != "planned":
                return
            mode = order.mode
        if mode == "paper":
            result = simulate_market_order(request, book)
        else:
            if not self.settings.live_copy_enabled:
                await self._mark_order_failed(order_id, "自动实盘仍由服务端锁定")
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
            if result.filled_size > ZERO:
                fingerprint = hashlib.sha256(
                    f"{order.id}|{result.external_trade_id or result.external_order_id or 'paper'}|"
                    f"{result.filled_size}|{result.filled_usdc}".encode()
                ).hexdigest()
                existing = await session.scalar(
                    select(CopyFill.id).where(CopyFill.fingerprint == fingerprint)
                )
                if existing is None:
                    average = result.average_price or result.filled_usdc / result.filled_size
                    session.add(
                        CopyFill(
                            order_id=order.id,
                            fingerprint=fingerprint,
                            external_trade_id=result.external_trade_id,
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
                                detail="三事件跟单建仓",
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
                    if position is not None:
                        position.updated_at = utcnow()
            elif position is not None and order.side == "BUY":
                position.reserved_buy_usdc = ZERO
                position.status = "not_opened"
                position.updated_at = utcnow()
            await session.commit()

    async def _record_skip(self, subscription_id: int, event: PositionEvent, reason: str) -> None:
        order = self._new_order(
            subscription_id=subscription_id,
            position_id=None,
            event=event,
            side="BUY",
            mode="paper",
            amount=ZERO,
            price=event.after_avg_price or Decimal("0.01"),
            reference=event.after_avg_price or Decimal("0.01"),
            key=f"copy:open:{event.id}",
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
        self, subscription_id: int, account: ExecutionAccount | None
    ) -> RiskUsage:
        local_start = datetime.now(SHANGHAI).replace(hour=0, minute=0, second=0, microsecond=0)
        utc_start = local_start.astimezone(UTC).replace(tzinfo=None)
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
            ledger = list(
                (
                    await session.scalars(
                        select(CopyLedger).where(
                            CopyLedger.subscription_id == subscription_id,
                            CopyLedger.timestamp >= utc_start,
                        )
                    )
                ).all()
            )
        total = sum((p.attributed_cost + p.reserved_buy_usdc for p in positions), ZERO)
        bought = sum((row.amount_usdc for row in ledger if row.type == "buy"), ZERO)
        realized = sum((row.realized_pnl for row in ledger), ZERO)
        if account is None:
            return RiskUsage(total=total, wallet_capital=Decimal("999999999"), bought_today=bought)
        capital = max(ZERO, account.budget_usdc - account.cash_reserve_usdc)
        return RiskUsage(
            total=total,
            bought_today=bought,
            realized_loss_today=max(ZERO, -realized),
            wallet_capital=min(capital, account.max_total_exposure_usdc),
            account_daily_buy_limit=account.daily_buy_limit_usdc,
            account_daily_loss_limit=account.daily_loss_limit_usdc,
        )

    async def _execute_redemption(self, redemption_id: int, event_id: int, mode: str) -> None:
        async with self.database.sessions() as session:
            redemption = await session.get(CopyRedemption, redemption_id)
            event = await session.get(PositionEvent, event_id)
            if redemption is None or event is None or redemption.status != "pending":
                return
            position = await session.get(CopyPosition, redemption.copy_position_id)
            if position is None:
                return
            size = redemption.size
            if mode == "paper":
                payout_per_share = (
                    event.payout_amount / event.before_size
                    if event.payout_amount is not None and event.before_size > ZERO
                    else ONE
                )
                payout = size * payout_per_share
                transaction_hash = event.transaction_hash
            else:
                payout = None
                transaction_hash = None
                condition_id = position.condition_id
                outcome_index = position.outcome_index
                neg_risk = bool(position.neg_risk)
        if mode == "live":
            trader = await self._trader()
            transaction_hash = await trader.redeem(
                condition_id=condition_id,
                size=size,
                outcome_index=outcome_index,
                neg_risk=neg_risk,
            )
        async with self.database.sessions() as session:
            redemption = await session.get(CopyRedemption, redemption_id)
            if redemption is None or redemption.status != "pending":
                return
            position = await session.get(CopyPosition, redemption.copy_position_id)
            assert position is not None
            cost = position.attributed_cost
            realized = (payout - cost) if payout is not None else ZERO
            redemption.status = "completed"
            redemption.payout_usdc = payout
            redemption.transaction_hash = transaction_hash
            redemption.attempts += 1
            redemption.updated_at = utcnow()
            position.attributed_size = ZERO
            position.attributed_cost = ZERO
            position.realized_pnl += realized
            position.status = "redeemed"
            position.updated_at = utcnow()
            session.add(
                CopyLedger(
                    subscription_id=position.subscription_id,
                    copy_position_id=position.id,
                    order_id=None,
                    type="redeem",
                    amount_usdc=payout or ZERO,
                    realized_pnl=realized,
                    detail="观察钱包赎回后一次性赎回归因仓位",
                    timestamp=utcnow(),
                )
            )
            await session.commit()

    async def process_redemptions(self, subscription_id: int) -> None:
        async with self.database.sessions() as session:
            rows = list(
                (
                    await session.scalars(
                        select(CopyRedemption)
                        .join(CopyPosition)
                        .where(
                            CopyPosition.subscription_id == subscription_id,
                            CopyRedemption.status == "pending",
                        )
                    )
                ).all()
            )
        # A pending live redemption is deliberately not retried automatically: its
        # chain transaction must first be reconciled by transaction hash.
        del rows

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
                        position.status = "not_opened"
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
                    mode=subscription.mode,
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
                        position.status = "not_opened"
                        position.updated_at = utcnow()
                await session.commit()

    async def _trader(self) -> OfficialClobTrader:
        async with self.database.sessions() as session:
            account = await session.get(ExecutionAccount, 1)
        if (
            account is None
            or not account.keychain_service
            or not account.keychain_account
            or account.signature_type != 1
        ):
            raise TradingUnavailable("V2 实盘要求已配置的 Magic/Proxy 签名类型 1")
        key = (
            account.keychain_service,
            account.keychain_account,
            account.signature_type,
            account.funder_address,
        )
        if self._trader_cache is None or self._trader_cache_key != key:
            self._trader_cache = OfficialClobTrader(
                host=self.settings.clob_api_url,
                keychain=self.keychain,
                key_reference=KeychainReference(
                    service=account.keychain_service,
                    account=account.keychain_account,
                ),
                signature_type=1,
                funder_address=account.funder_address,
                relayer_url=self.settings.relayer_api_url,
                rpc_url=self.settings.polygon_rpc_url,
            )
            self._trader_cache_key = key
        return self._trader_cache
