from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from backend.config import Settings
from backend.db import Database
from backend.keychain import KeychainReference, MacOSKeychain
from backend.models import (
    CopyFill,
    CopyLeaderState,
    CopyLedger,
    CopyOrder,
    CopyPosition,
    CopyRedemption,
    CopySubscription,
    CopyTradeSignal,
    CurrentPosition,
    ExecutionAccount,
    PositionEvent,
    WalletTrade,
    WatchedWallet,
)
from backend.polymarket import (
    OrderBookSnapshot,
    PolymarketClient,
    fingerprint_trades,
)
from backend.trading import (
    MarketTradeRequest,
    OfficialClobTrader,
    TradeRequest,
    TradeResult,
    TradingUnavailable,
    simulate_limit_order,
    simulate_market_order,
)

ZERO = Decimal("0")
ONE = Decimal("1")
SHANGHAI = ZoneInfo("Asia/Shanghai")
OPEN_ORDER_STATES = {"open", "submitted"}
TEMPERATURE_BUCKET_MARKET = re.compile(
    r"\b(?:highest|lowest)\s+(?:daily\s+)?temperature\b|(?:最高|最低)(?:气温|温度)",
    re.I,
)


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def is_temperature_bucket(title: str, outcome: str) -> bool:
    """Recognize every bucket inside a highest/lowest-temperature market.

    Polymarket emits the selected bucket in the title for exact-value markets and
    commonly emits only ``Yes`` as the position outcome. Matching only the outcome
    therefore drops valid exact-value and range buckets.
    """

    del outcome
    return TEMPERATURE_BUCKET_MARKET.search(title) is not None


def quantize_price(value: Decimal, tick: Decimal, *, side: str) -> Decimal:
    rounding = ROUND_UP if side == "BUY" else ROUND_DOWN
    ticks = (value / tick).to_integral_value(rounding=rounding)
    return max(tick, min(ONE - tick, ticks * tick))


def tolerated_price(
    reference: Decimal,
    tick: Decimal,
    ticks: int,
    percent: Decimal,
    *,
    side: str,
) -> Decimal:
    percent_delta = reference * percent / Decimal("100")
    tick_delta = tick * ticks
    delta = min(percent_delta, tick_delta)
    raw = reference + delta if side == "BUY" else reference - delta
    return quantize_price(raw, tick, side=side)


def market_worst_price(
    reference: Decimal,
    tick: Decimal,
    slippage_cents: Decimal,
    *,
    side: str,
) -> Decimal:
    buffer = slippage_cents / Decimal("100")
    raw = reference + buffer if side == "BUY" else reference - buffer
    rounding = ROUND_DOWN if side == "BUY" else ROUND_UP
    ticks = (raw / tick).to_integral_value(rounding=rounding)
    lower_bound = max(Decimal("0.01"), tick)
    upper_bound = min(Decimal("0.99"), ONE - tick)
    return max(lower_bound, min(upper_bound, ticks * tick))


@dataclass(frozen=True, slots=True)
class RiskUsage:
    bucket: Decimal = ZERO
    event: Decimal = ZERO
    settlement_day: Decimal = ZERO
    total: Decimal = ZERO
    bought_today: Decimal = ZERO
    realized_loss_today: Decimal = ZERO
    wallet_capital: Decimal = ZERO
    account_daily_buy_limit: Decimal = Decimal("999999999")
    account_daily_loss_limit: Decimal = Decimal("999999999")


def allowed_buy_usdc(
    requested: Decimal,
    *,
    bucket_cap: Decimal,
    subscription: CopySubscription,
    usage: RiskUsage,
) -> tuple[Decimal, str | None]:
    loss_limit = min(subscription.daily_loss_limit_usdc, usage.account_daily_loss_limit)
    buy_limit = min(subscription.daily_buy_limit_usdc, usage.account_daily_buy_limit)
    if usage.realized_loss_today >= loss_limit:
        return ZERO, "已触发当日已实现亏损熔断"
    limits = {
        "温度桶额度已满": bucket_cap - usage.bucket,
        "同事件额度已满": subscription.event_cap_usdc - usage.event,
        "同结算日额度已满": subscription.settlement_day_cap_usdc - usage.settlement_day,
        "总敞口额度已满": subscription.total_exposure_cap_usdc - usage.total,
        "当日买入额度已满": buy_limit - usage.bought_today,
        "执行钱包可用预算不足": usage.wallet_capital - usage.total,
    }
    allowed = min([requested, *limits.values()])
    if allowed > ZERO:
        return allowed, None
    reason = next((label for label, remaining in limits.items() if remaining <= ZERO), None)
    return ZERO, reason or "风险额度不足"


class CopyTradingEngine:
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
        self._balance_task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()
        self._trader_cache_key: tuple[object, ...] | None = None
        self._trader_cache: OfficialClobTrader | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="copy-trading-engine")

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
        if self._balance_task is not None:
            self._balance_task.cancel()
            try:
                await self._balance_task
            except asyncio.CancelledError:
                pass
            self._balance_task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Individual subscription failures are persisted by tick. Keep the
                # process alive without ever printing key-bearing exception details.
                pass
            self._wake.clear()
            try:
                await asyncio.wait_for(
                    self._wake.wait(),
                    timeout=max(0.25, self.settings.copy_poll_interval_seconds),
                )
            except TimeoutError:
                pass

    async def tick(self) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            async with self.database.sessions() as session:
                subscription_ids = list(
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
            for subscription_id in subscription_ids:
                try:
                    await self.retire_legacy_orders(subscription_id)
                    await self.process_open_orders(subscription_id)
                    await self.process_fast_trades(subscription_id)
                    await self.process_subscription(subscription_id)
                    await self.process_redemptions(subscription_id)
                except Exception as error:
                    await self._record_error(subscription_id, error)
            if self._balance_task is None or self._balance_task.done():
                self._balance_task = asyncio.create_task(
                    self.refresh_stale_live_balance(),
                    name="copy-trading-balance-refresh",
                )

    async def process_subscription(self, subscription_id: int) -> None:
        async with self.database.sessions() as session:
            subscription = await session.get(CopySubscription, subscription_id)
            if subscription is None or subscription.state not in {
                "active",
                "paused",
                "exit_only",
                "closing",
            }:
                return
            if subscription.state == "closing":
                await self._liquidate(session, subscription)
                await session.commit()
                return
            events = list(
                (
                    await session.scalars(
                        select(PositionEvent)
                        .options(selectinload(PositionEvent.fills))
                        .where(
                            PositionEvent.wallet_id == subscription.tracked_wallet_id,
                            PositionEvent.id > subscription.last_processed_event_id,
                        )
                        .order_by(PositionEvent.id.asc())
                    )
                ).all()
            )
            for event in events:
                if event.type == "redeemed":
                    await self._leader_redeemed(session, subscription, event)
                subscription.last_processed_event_id = event.id
                subscription.last_processed_at = utcnow()
                subscription.updated_at = utcnow()
                await session.commit()

    async def prime_subscription(self, subscription_id: int) -> None:
        """Establish a no-backfill trade baseline and reconcile leader state."""

        await self.cancel_open_orders(subscription_id)
        async with self.database.sessions() as session:
            subscription = await session.get(CopySubscription, subscription_id)
            if subscription is None:
                return
            wallet = await session.get(WatchedWallet, subscription.tracked_wallet_id)
            if wallet is None:
                return
            proxy_wallet = wallet.proxy_wallet
        positions = await self.client.fetch_active_positions(proxy_wallet)
        scoped = [
            position
            for position in positions
            if is_temperature_bucket(position.title, position.outcome)
        ]
        now = utcnow()
        # Public trade timestamps have one-second precision. Starting at the next
        # whole second cleanly separates pre-enable trades from new trades without
        # creating a same-second historical backfill race.
        baseline_at = now.replace(microsecond=0) + timedelta(seconds=1)
        async with self.database.sessions() as session:
            subscription = await session.get(CopySubscription, subscription_id)
            if subscription is None:
                return
            existing_states = {
                state.asset_id: state
                for state in (
                    await session.scalars(
                        select(CopyLeaderState).where(
                            CopyLeaderState.subscription_id == subscription_id
                        )
                    )
                ).all()
            }
            active_assets: set[str] = set()
            for snapshot in scoped:
                active_assets.add(snapshot.asset_id)
                state = existing_states.get(snapshot.asset_id)
                if state is None:
                    state = CopyLeaderState(
                        subscription_id=subscription_id,
                        asset_id=snapshot.asset_id,
                        condition_id=snapshot.condition_id,
                        title=snapshot.title,
                        outcome=snapshot.outcome,
                        outcome_index=snapshot.outcome_index,
                        event_slug=snapshot.event_slug,
                        market_slug=snapshot.market_slug,
                        settlement_date=(
                            snapshot.end_date.date().isoformat()
                            if snapshot.end_date is not None
                            else None
                        ),
                        size=snapshot.size,
                        remaining_cost=snapshot.initial_value,
                        last_trade_at=None,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(state)
                else:
                    state.condition_id = snapshot.condition_id
                    state.title = snapshot.title
                    state.outcome = snapshot.outcome
                    state.outcome_index = snapshot.outcome_index
                    state.event_slug = snapshot.event_slug
                    state.market_slug = snapshot.market_slug
                    state.settlement_date = (
                        snapshot.end_date.date().isoformat()
                        if snapshot.end_date is not None
                        else None
                    )
                    state.size = snapshot.size
                    state.remaining_cost = snapshot.initial_value
                    state.updated_at = now
            for asset_id, state in existing_states.items():
                if asset_id not in active_assets:
                    state.size = ZERO
                    state.remaining_cost = ZERO
                    state.updated_at = now
            pending_signals = list(
                (
                    await session.scalars(
                        select(CopyTradeSignal).where(
                            CopyTradeSignal.subscription_id == subscription_id,
                            CopyTradeSignal.status.in_(["pending", "submitted", "deferred"]),
                        )
                    )
                ).all()
            )
            for signal in pending_signals:
                signal.status = "skipped"
                signal.reason = "策略重新建立成交基线，不补历史单"
                signal.processed_at = now
            copy_positions = list(
                (
                    await session.scalars(
                        select(CopyPosition).where(CopyPosition.subscription_id == subscription_id)
                    )
                ).all()
            )
            for position in copy_positions:
                position.pending_target_usdc = ZERO
                position.reserved_buy_usdc = ZERO
                state = existing_states.get(position.asset_id)
                if state is not None:
                    position.leader_size = state.size
                    position.leader_remaining_cost = state.remaining_cost
                position.updated_at = now
            subscription.fast_poll_started_at = baseline_at
            subscription.last_trade_poll_at = baseline_at
            subscription.last_trade_error = None
            subscription.last_error = None
            subscription.updated_at = now
            await session.commit()

    async def process_fast_trades(self, subscription_id: int) -> None:
        async with self.database.sessions() as session:
            subscription = await session.get(CopySubscription, subscription_id)
            if subscription is None or subscription.state not in {"active", "exit_only"}:
                return
            if subscription.fast_poll_started_at is None:
                needs_prime = True
                proxy_wallet = ""
                started_at = utcnow()
                last_poll_at = None
            else:
                needs_prime = False
                wallet = await session.get(WatchedWallet, subscription.tracked_wallet_id)
                if wallet is None:
                    return
                proxy_wallet = wallet.proxy_wallet
                started_at = subscription.fast_poll_started_at
                last_poll_at = subscription.last_trade_poll_at
        if needs_prime:
            await self.prime_subscription(subscription_id)
            return

        now = utcnow()
        if now < started_at:
            return
        start = last_poll_at or started_at
        start = max(start - timedelta(seconds=120), started_at)
        try:
            trades = await self.client.fetch_trades(
                proxy_wallet,
                start=start,
                end=now + timedelta(seconds=30),
            )
        except Exception as error:
            async with self.database.sessions() as session:
                subscription = await session.get(CopySubscription, subscription_id)
                if subscription is not None:
                    subscription.last_trade_error = str(error)[:1000]
                    subscription.updated_at = utcnow()
                    await session.commit()
            return

        fingerprinted = fingerprint_trades(proxy_wallet, trades)
        fingerprints = [fingerprint for fingerprint, _ in fingerprinted]
        async with self.database.sessions() as session:
            existing: set[str] = set()
            for offset in range(0, len(fingerprints), 500):
                existing.update(
                    (
                        await session.scalars(
                            select(WalletTrade.fingerprint).where(
                                WalletTrade.fingerprint.in_(fingerprints[offset : offset + 500])
                            )
                        )
                    ).all()
                )
            for fingerprint, trade in fingerprinted:
                if fingerprint in existing:
                    continue
                try:
                    async with session.begin_nested():
                        session.add(
                            WalletTrade(
                                wallet_id=subscription.tracked_wallet_id,
                                fingerprint=fingerprint,
                                asset_id=trade.asset_id,
                                condition_id=trade.condition_id,
                                side=trade.side,
                                size=trade.size,
                                price=trade.price,
                                amount=trade.size * trade.price,
                                timestamp=trade.timestamp,
                                transaction_hash=trade.transaction_hash,
                                title=trade.title,
                                outcome=trade.outcome,
                                outcome_index=trade.outcome_index,
                                event_slug=trade.event_slug,
                                market_slug=trade.market_slug,
                                imported_at=now,
                            )
                        )
                        await session.flush()
                except IntegrityError:
                    continue
            await session.flush()
            rows: list[WalletTrade] = []
            for offset in range(0, len(fingerprints), 500):
                rows.extend(
                    (
                        await session.scalars(
                            select(WalletTrade).where(
                                WalletTrade.wallet_id == subscription.tracked_wallet_id,
                                WalletTrade.fingerprint.in_(fingerprints[offset : offset + 500]),
                                WalletTrade.timestamp >= started_at,
                            )
                        )
                    ).all()
                )
            existing_signal_trade_ids: set[int] = set()
            row_ids = [row.id for row in rows]
            for offset in range(0, len(row_ids), 500):
                existing_signal_trade_ids.update(
                    (
                        await session.scalars(
                            select(CopyTradeSignal.wallet_trade_id).where(
                                CopyTradeSignal.subscription_id == subscription_id,
                                CopyTradeSignal.wallet_trade_id.in_(row_ids[offset : offset + 500]),
                            )
                        )
                    ).all()
                )
            for row in rows:
                title = row.title or ""
                outcome = row.outcome or ""
                if row.id in existing_signal_trade_ids or not is_temperature_bucket(title, outcome):
                    continue
                session.add(
                    CopyTradeSignal(
                        subscription_id=subscription_id,
                        wallet_trade_id=row.id,
                        status="pending",
                        detected_at=now,
                    )
                )
            subscription = await session.get(CopySubscription, subscription_id)
            if subscription is not None:
                subscription.last_trade_poll_at = now
                subscription.last_trade_error = None
                subscription.updated_at = now
            await session.commit()
        await self.process_pending_signals(subscription_id)

    async def process_pending_signals(self, subscription_id: int) -> None:
        async with self.database.sessions() as session:
            assets = list(
                dict.fromkeys(
                    (
                        await session.scalars(
                            select(WalletTrade.asset_id)
                            .join(
                                CopyTradeSignal,
                                CopyTradeSignal.wallet_trade_id == WalletTrade.id,
                            )
                            .where(
                                CopyTradeSignal.subscription_id == subscription_id,
                                CopyTradeSignal.status == "pending",
                            )
                            .order_by(WalletTrade.timestamp.asc(), WalletTrade.id.asc())
                        )
                    ).all()
                )
            )
        for asset_id in assets:
            async with self.database.sessions() as session:
                subscription = await session.get(CopySubscription, subscription_id)
                if subscription is None or subscription.state not in {"active", "exit_only"}:
                    return
                signals = list(
                    (
                        await session.scalars(
                            select(CopyTradeSignal)
                            .options(selectinload(CopyTradeSignal.trade))
                            .join(WalletTrade)
                            .where(
                                CopyTradeSignal.subscription_id == subscription_id,
                                CopyTradeSignal.status == "pending",
                                WalletTrade.asset_id == asset_id,
                            )
                            .order_by(WalletTrade.timestamp.asc(), WalletTrade.id.asc())
                        )
                    ).all()
                )
                if not signals:
                    continue
                await self._process_signal_group(session, subscription, signals)
                await session.commit()

    async def _process_signal_group(
        self,
        session: object,
        subscription: CopySubscription,
        signals: list[CopyTradeSignal],
    ) -> None:
        trades = [signal.trade for signal in signals]
        first = trades[0]
        current = await session.scalar(
            select(CurrentPosition).where(
                CurrentPosition.wallet_id == subscription.tracked_wallet_id,
                CurrentPosition.asset_id == first.asset_id,
            )
        )
        title = first.title or (current.title if current is not None else "")
        outcome = first.outcome or (current.outcome if current is not None else "")
        if not is_temperature_bucket(title, outcome):
            self._mark_signals(signals, "skipped", "非最高/最低气温市场")
            return
        state = await session.scalar(
            select(CopyLeaderState).where(
                CopyLeaderState.subscription_id == subscription.id,
                CopyLeaderState.asset_id == first.asset_id,
            )
        )
        now = utcnow()
        if state is None:
            state = CopyLeaderState(
                subscription_id=subscription.id,
                asset_id=first.asset_id,
                condition_id=first.condition_id,
                title=title,
                outcome=outcome,
                outcome_index=(
                    first.outcome_index
                    if first.outcome_index is not None
                    else (current.outcome_index if current is not None else None)
                ),
                event_slug=first.event_slug
                or (current.event_slug if current is not None else None),
                market_slug=first.market_slug
                or (current.market_slug if current is not None else None),
                settlement_date=(
                    current.end_date.date().isoformat()
                    if current is not None and current.end_date is not None
                    else None
                ),
                size=ZERO,
                remaining_cost=ZERO,
                last_trade_at=None,
                created_at=now,
                updated_at=now,
            )
            session.add(state)
            await session.flush()
        start_size = state.size
        start_cost = state.remaining_cost
        end_size = start_size
        end_cost = start_cost
        for trade in trades:
            if trade.side == "BUY":
                end_size += trade.size
                end_cost += trade.amount
            elif end_size > ZERO:
                fraction = min(ONE, trade.size / end_size)
                end_size = max(ZERO, end_size - trade.size)
                end_cost = max(ZERO, end_cost * (ONE - fraction))
        last_trade = max(trades, key=lambda trade: (trade.timestamp, trade.id))
        state.size = end_size
        state.remaining_cost = end_cost
        state.last_trade_at = last_trade.timestamp
        state.title = last_trade.title or state.title
        state.outcome = last_trade.outcome or state.outcome
        state.event_slug = last_trade.event_slug or state.event_slug
        state.market_slug = last_trade.market_slug or state.market_slug
        if last_trade.outcome_index is not None:
            state.outcome_index = last_trade.outcome_index
        if current is not None:
            state.condition_id = current.condition_id
            state.title = current.title
            state.outcome = current.outcome
            state.outcome_index = current.outcome_index
            state.event_slug = current.event_slug
            state.market_slug = current.market_slug
            state.settlement_date = (
                current.end_date.date().isoformat() if current.end_date is not None else None
            )
        state.updated_at = now

        if end_size > start_size and end_cost > start_cost:
            if subscription.state != "active":
                self._mark_signals(signals, "skipped", "策略处于仅退出状态")
                return
            await self._fast_follow_buy(
                session,
                subscription,
                state,
                signals,
                end_cost - start_cost,
                current,
            )
        elif end_size < start_size:
            fraction = ONE if start_size <= ZERO else min(ONE, (start_size - end_size) / start_size)
            await self._fast_follow_sell(
                session,
                subscription,
                state,
                signals,
                fraction,
            )
        else:
            self._mark_signals(signals, "netted", "同批成交最终净仓位未变化")

    async def _fast_follow_buy(
        self,
        session: object,
        subscription: CopySubscription,
        state: CopyLeaderState,
        signals: list[CopyTradeSignal],
        leader_cost_increase: Decimal,
        current: CurrentPosition | None,
    ) -> None:
        if subscription.mode == "live":
            account = await session.get(ExecutionAccount, 1)
            now = utcnow()
            if (
                account is None
                or account.collateral_balance is None
                or account.last_balance_at is None
                or now - account.last_balance_at > timedelta(seconds=60)
            ):
                raise RuntimeError("执行钱包余额超过60秒未核对，本次实盘买入等待余额刷新")
        position = await self._position_for_state(session, subscription, state)
        if position.attributed_size <= ZERO:
            position.status = "open"
        position.leader_size = state.size
        position.leader_remaining_cost = state.remaining_cost
        precise_end_date = await self.client.fetch_market_end_date(
            market_slug=state.market_slug,
            condition_id=state.condition_id,
        )
        if precise_end_date is not None:
            cutoff = precise_end_date - timedelta(minutes=subscription.close_buffer_minutes)
            if utcnow() >= cutoff:
                self._mark_signals(signals, "skipped", "市场已进入结算前停止下单窗口")
                return
        candidate = (
            leader_cost_increase * subscription.copy_ratio_percent / Decimal("100")
            + position.pending_target_usdc
        )
        usage = await self._risk_usage(session, subscription, position)
        bucket_cap = (
            subscription.strong_bucket_cap_usdc
            if state.remaining_cost > subscription.strong_threshold_usdc
            else subscription.base_bucket_cap_usdc
        )
        allowed, reason = allowed_buy_usdc(
            candidate,
            bucket_cap=bucket_cap,
            subscription=subscription,
            usage=usage,
        )
        if allowed <= ZERO:
            self._mark_signals(signals, "skipped", reason or "风控拒绝")
            return
        book = await self.client.fetch_order_book(state.asset_id)
        if book.best_ask is None:
            raise RuntimeError("订单簿没有可用卖价")
        estimated_size = allowed / book.best_ask
        if estimated_size < book.min_order_size:
            position.pending_target_usdc = min(candidate, max(ZERO, bucket_cap - usage.bucket))
            position.updated_at = utcnow()
            self._mark_signals(signals, "deferred", "低于市场最小买入量，已累计")
            return
        position.neg_risk = book.neg_risk
        position.pending_target_usdc = ZERO
        worst_price = market_worst_price(
            book.best_ask,
            book.tick_size,
            subscription.market_slippage_cents,
            side="BUY",
        )
        await self._place_market_order(
            session,
            subscription,
            position,
            signals=signals,
            side="BUY",
            amount=allowed,
            reference_price=book.best_ask,
            worst_price=worst_price,
            book=book,
            reason="跟随目标钱包净加仓",
        )

    async def _fast_follow_sell(
        self,
        session: object,
        subscription: CopySubscription,
        state: CopyLeaderState,
        signals: list[CopyTradeSignal],
        fraction: Decimal,
    ) -> None:
        position = await session.scalar(
            select(CopyPosition).where(
                CopyPosition.subscription_id == subscription.id,
                CopyPosition.asset_id == state.asset_id,
            )
        )
        await self._clear_deferred_signals(
            session,
            subscription.id,
            state.asset_id,
            "目标钱包已减仓，累计买入金额已清零",
        )
        if position is None:
            self._mark_signals(signals, "netted", "目标钱包减仓，但本策略没有归因持仓")
            return
        position.pending_target_usdc = ZERO
        if position.attributed_size <= ZERO:
            self._mark_signals(signals, "netted", "目标钱包减仓，但本策略没有归因持仓")
            return
        await self._cancel_open_buys(session, subscription, position)
        position.leader_size = state.size
        position.leader_remaining_cost = state.remaining_cost
        unallocated_size = max(ZERO, position.attributed_size - position.dust_size)
        new_size = unallocated_size if fraction >= ONE else unallocated_size * fraction
        size = min(position.attributed_size, new_size + position.dust_size)
        book = await self.client.fetch_order_book(state.asset_id)
        if book.best_bid is None:
            raise RuntimeError("订单簿没有可用买价")
        if size < book.min_order_size:
            position.dust_size = size
            position.updated_at = utcnow()
            self._mark_signals(signals, "deferred", "低于市场最小卖出量，已累计")
            return
        position.dust_size = ZERO
        worst_price = market_worst_price(
            book.best_bid,
            book.tick_size,
            subscription.market_slippage_cents,
            side="SELL",
        )
        await self._place_market_order(
            session,
            subscription,
            position,
            signals=signals,
            side="SELL",
            amount=size,
            reference_price=book.best_bid,
            worst_price=worst_price,
            book=book,
            reason="跟随目标钱包净减仓",
        )

    async def _position_for_state(
        self,
        session: object,
        subscription: CopySubscription,
        state: CopyLeaderState,
    ) -> CopyPosition:
        position = await session.scalar(
            select(CopyPosition).where(
                CopyPosition.subscription_id == subscription.id,
                CopyPosition.asset_id == state.asset_id,
            )
        )
        if position is not None:
            return position
        now = utcnow()
        position = CopyPosition(
            subscription_id=subscription.id,
            asset_id=state.asset_id,
            condition_id=state.condition_id,
            title=state.title,
            outcome=state.outcome,
            outcome_index=state.outcome_index,
            neg_risk=None,
            event_slug=state.event_slug,
            settlement_date=state.settlement_date,
            attributed_size=ZERO,
            attributed_cost=ZERO,
            reserved_buy_usdc=ZERO,
            pending_target_usdc=ZERO,
            dust_size=ZERO,
            leader_size=state.size,
            leader_remaining_cost=state.remaining_cost,
            realized_pnl=ZERO,
            status="open",
            created_at=now,
            updated_at=now,
        )
        session.add(position)
        await session.flush()
        return position

    async def _place_market_order(
        self,
        session: object,
        subscription: CopySubscription,
        position: CopyPosition,
        *,
        signals: list[CopyTradeSignal],
        side: str,
        amount: Decimal,
        reference_price: Decimal,
        worst_price: Decimal,
        book: OrderBookSnapshot,
        reason: str,
    ) -> CopyOrder:
        signal_key = (
            ",".join(str(signal.wallet_trade_id) for signal in signals)
            if signals
            else f"close-{position.id}-{int(utcnow().timestamp() // 5)}"
        )
        key = hashlib.sha256(
            f"{subscription.id}:trades:{signal_key}:{side}:{position.asset_id}".encode()
        ).hexdigest()
        existing = await session.scalar(select(CopyOrder).where(CopyOrder.idempotency_key == key))
        if existing is not None:
            for signal in signals:
                signal.order_id = existing.id
            return existing
        now = utcnow()
        requested_size = (amount / reference_price if side == "BUY" else amount).quantize(
            Decimal("0.000001"), rounding=ROUND_DOWN
        )
        requested_usdc = amount if side == "BUY" else amount * reference_price
        order = CopyOrder(
            subscription_id=subscription.id,
            copy_position_id=position.id,
            leader_event_id=None,
            idempotency_key=key,
            asset_id=position.asset_id,
            condition_id=position.condition_id,
            side=side,
            mode=subscription.mode,
            order_type="FAK",
            requested_size=requested_size,
            requested_usdc=requested_usdc,
            limit_price=worst_price,
            reference_price=reference_price,
            filled_size=ZERO,
            filled_usdc=ZERO,
            fee_usdc=ZERO,
            status="planned",
            reason=reason,
            expires_at=None,
            created_at=now,
            updated_at=now,
        )
        session.add(order)
        await session.flush()
        for signal in signals:
            signal.order_id = order.id
            signal.status = "submitted"
        request = MarketTradeRequest(
            asset_id=position.asset_id,
            side=side,
            amount=amount,
            worst_price=worst_price,
            neg_risk=book.neg_risk,
        )
        if subscription.mode == "paper":
            result = simulate_market_order(request, book)
        else:
            result = await (await self._live_trader(session)).submit_market(request)
        await self._apply_trade_result(session, order, position, result)
        return order

    @staticmethod
    def _mark_signals(
        signals: list[CopyTradeSignal],
        status: str,
        reason: str | None,
    ) -> None:
        now = utcnow()
        for signal in signals:
            signal.status = status
            signal.reason = reason
            signal.processed_at = now

    async def _clear_deferred_signals(
        self,
        session: object,
        subscription_id: int,
        asset_id: str,
        reason: str,
    ) -> None:
        signals = list(
            (
                await session.scalars(
                    select(CopyTradeSignal)
                    .join(WalletTrade)
                    .where(
                        CopyTradeSignal.subscription_id == subscription_id,
                        CopyTradeSignal.status == "deferred",
                        WalletTrade.asset_id == asset_id,
                    )
                )
            ).all()
        )
        self._mark_signals(signals, "skipped", reason)

    async def _follow_buy(
        self,
        session: object,
        subscription: CopySubscription,
        event: PositionEvent,
    ) -> None:
        if not is_temperature_bucket(event.title, event.outcome):
            return
        leader_amount = sum((fill.amount for fill in event.fills if fill.side == "BUY"), start=ZERO)
        if leader_amount <= ZERO and event.average_fill_price is not None:
            leader_amount = event.delta_size * event.average_fill_price
        if leader_amount <= ZERO:
            return
        position = await self._position_for_event(session, subscription, event)
        if position.attributed_size <= ZERO:
            position.status = "open"
        leader = await session.scalar(
            select(CurrentPosition).where(
                CurrentPosition.wallet_id == subscription.tracked_wallet_id,
                CurrentPosition.asset_id == event.asset_id,
            )
        )
        position.leader_size = leader.size if leader is not None else event.after_size
        position.leader_remaining_cost = (
            leader.initial_value if leader is not None else event.after_size * event.after_avg_price
        )
        if leader is not None and leader.end_date is not None:
            cutoff = leader.end_date - timedelta(minutes=subscription.close_buffer_minutes)
            if utcnow() >= cutoff:
                await self._record_skip(
                    session,
                    subscription,
                    position,
                    event,
                    "市场已进入结算前停止下单窗口",
                )
                return
        if subscription.mode == "live":
            await self._refresh_live_balance(session)
        candidate = leader_amount * subscription.copy_ratio_percent / Decimal("100")
        requested = candidate + position.pending_target_usdc
        usage = await self._risk_usage(session, subscription, position)
        bucket_cap = (
            subscription.strong_bucket_cap_usdc
            if position.leader_remaining_cost > subscription.strong_threshold_usdc
            else subscription.base_bucket_cap_usdc
        )
        allowed, reason = allowed_buy_usdc(
            requested,
            bucket_cap=bucket_cap,
            subscription=subscription,
            usage=usage,
        )
        if allowed <= ZERO:
            await self._record_skip(session, subscription, position, event, reason or "风控拒绝")
            return
        book = await self.client.fetch_order_book(event.asset_id)
        reference = self._leader_reference_price(event, side="BUY") or book.best_ask
        if reference is None:
            raise RuntimeError("订单簿没有可用卖价")
        limit_price = tolerated_price(
            reference,
            book.tick_size,
            subscription.price_tolerance_ticks,
            subscription.price_tolerance_percent,
            side="BUY",
        )
        size = (allowed / limit_price).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
        if size < book.min_order_size:
            position.pending_target_usdc = min(requested, bucket_cap - usage.bucket)
            position.updated_at = utcnow()
            return
        position.neg_risk = book.neg_risk
        position.pending_target_usdc = ZERO
        await self._place_order(
            session,
            subscription,
            position,
            event=event,
            side="BUY",
            size=size,
            limit_price=limit_price,
            book=book,
            reason=None,
        )

    async def _follow_sell(
        self,
        session: object,
        subscription: CopySubscription,
        event: PositionEvent,
    ) -> None:
        position = await session.scalar(
            select(CopyPosition).where(
                CopyPosition.subscription_id == subscription.id,
                CopyPosition.asset_id == event.asset_id,
            )
        )
        if position is None or position.attributed_size <= ZERO:
            return
        await self._cancel_open_buys(session, subscription, position)
        fraction = (
            ONE
            if event.after_size <= ZERO or event.before_size <= ZERO
            else min(ONE, abs(event.delta_size) / event.before_size)
        )
        size = position.attributed_size if fraction >= ONE else position.attributed_size * fraction
        book = await self.client.fetch_order_book(event.asset_id)
        reference = self._leader_reference_price(event, side="SELL") or book.best_bid
        if reference is None:
            raise RuntimeError("订单簿没有可用买价")
        limit_price = tolerated_price(
            reference,
            book.tick_size,
            subscription.price_tolerance_ticks,
            subscription.price_tolerance_percent,
            side="SELL",
        )
        await self._place_order(
            session,
            subscription,
            position,
            event=event,
            side="SELL",
            size=size,
            limit_price=limit_price,
            book=book,
            reason="跟随目标钱包减仓",
        )
        position.leader_size = event.after_size
        position.leader_remaining_cost = event.after_size * event.after_avg_price

    async def _place_order(
        self,
        session: object,
        subscription: CopySubscription,
        position: CopyPosition,
        *,
        event: PositionEvent | None,
        side: str,
        size: Decimal,
        limit_price: Decimal,
        book: OrderBookSnapshot,
        reason: str | None,
    ) -> CopyOrder:
        event_key = event.id if event is not None else f"close-{position.id}"
        key = hashlib.sha256(
            f"{subscription.id}:{event_key}:{side}:{position.asset_id}".encode()
        ).hexdigest()
        existing = await session.scalar(select(CopyOrder).where(CopyOrder.idempotency_key == key))
        if existing is not None:
            return existing
        now = utcnow()
        order = CopyOrder(
            subscription_id=subscription.id,
            copy_position_id=position.id,
            leader_event_id=event.id if event is not None else None,
            idempotency_key=key,
            asset_id=position.asset_id,
            condition_id=position.condition_id,
            side=side,
            mode=subscription.mode,
            order_type="GTD",
            requested_size=size,
            requested_usdc=size * limit_price,
            limit_price=limit_price,
            filled_size=ZERO,
            filled_usdc=ZERO,
            fee_usdc=ZERO,
            status="planned",
            reason=reason,
            expires_at=now + timedelta(minutes=subscription.order_ttl_minutes),
            created_at=now,
            updated_at=now,
        )
        session.add(order)
        await session.flush()
        request = TradeRequest(
            asset_id=position.asset_id,
            side=side,
            size=size,
            limit_price=limit_price,
            expiration=int(order.expires_at.replace(tzinfo=UTC).timestamp()),
            neg_risk=book.neg_risk,
        )
        if subscription.mode == "paper":
            result = simulate_limit_order(request, book)
        else:
            result = await (await self._live_trader(session)).submit(request)
        await self._apply_trade_result(session, order, position, result)
        return order

    async def _apply_trade_result(
        self,
        session: object,
        order: CopyOrder,
        position: CopyPosition,
        result: TradeResult,
    ) -> None:
        now = utcnow()
        delta_size = max(ZERO, result.filled_size - order.filled_size)
        delta_usdc = max(ZERO, result.filled_usdc - order.filled_usdc)
        order.status = result.status
        order.external_order_id = result.external_order_id or order.external_order_id
        order.reason = result.reason or order.reason
        order.updated_at = now
        if delta_size > ZERO:
            price = delta_usdc / delta_size if delta_usdc > ZERO else order.limit_price
            fingerprint = hashlib.sha256(
                f"{order.id}:{order.filled_size}:{delta_size}".encode()
            ).hexdigest()
            session.add(
                CopyFill(
                    order_id=order.id,
                    fingerprint=fingerprint,
                    size=delta_size,
                    price=price,
                    amount=delta_usdc,
                    fee_usdc=ZERO,
                    timestamp=now,
                )
            )
            if order.side == "BUY":
                position.attributed_size += delta_size
                position.attributed_cost += delta_usdc
                ledger_type = "buy"
                realized = ZERO
            else:
                average_cost = (
                    position.attributed_cost / position.attributed_size
                    if position.attributed_size > ZERO
                    else ZERO
                )
                removed_cost = min(position.attributed_cost, average_cost * delta_size)
                realized = delta_usdc - removed_cost
                position.attributed_size = max(ZERO, position.attributed_size - delta_size)
                position.attributed_cost = max(ZERO, position.attributed_cost - removed_cost)
                position.realized_pnl += realized
                ledger_type = "sell"
                if position.attributed_size <= ZERO:
                    position.status = "closed"
            session.add(
                CopyLedger(
                    subscription_id=order.subscription_id,
                    copy_position_id=position.id,
                    order_id=order.id,
                    type=ledger_type,
                    amount_usdc=delta_usdc,
                    realized_pnl=realized,
                    timestamp=now,
                )
            )
        order.filled_size = result.filled_size
        order.filled_usdc = result.filled_usdc
        if order.side == "BUY" and order.status in OPEN_ORDER_STATES:
            position.reserved_buy_usdc = max(ZERO, order.requested_usdc - order.filled_usdc)
        else:
            position.reserved_buy_usdc = ZERO
        position.updated_at = now
        signals = list(
            (
                await session.scalars(
                    select(CopyTradeSignal).where(CopyTradeSignal.order_id == order.id)
                )
            ).all()
        )
        if order.status in OPEN_ORDER_STATES:
            for signal in signals:
                signal.status = "submitted"
        else:
            followed = order.filled_size > ZERO
            for signal in signals:
                signal.status = "followed" if followed else "failed"
                signal.reason = order.reason
                signal.processed_at = now
        if order.mode == "live" and delta_usdc > ZERO:
            account = await session.get(ExecutionAccount, 1)
            if account is not None and account.collateral_balance is not None:
                account.collateral_balance = max(
                    ZERO,
                    account.collateral_balance
                    + (delta_usdc if order.side == "SELL" else -delta_usdc),
                )
                account.updated_at = now

    async def retire_legacy_orders(self, subscription_id: int) -> None:
        """Cancel resting V1 orders before processing any fast trade signal."""

        async with self.database.sessions() as session:
            subscription = await session.get(CopySubscription, subscription_id)
            if subscription is None:
                return
            orders = list(
                (
                    await session.scalars(
                        select(CopyOrder).where(
                            CopyOrder.subscription_id == subscription_id,
                            CopyOrder.order_type != "FAK",
                            CopyOrder.status.in_(["open", "submitted", "partially_filled"]),
                        )
                    )
                ).all()
            )
            for order in orders:
                position = await session.get(CopyPosition, order.copy_position_id)
                if position is not None:
                    await self._cancel_order(
                        session,
                        subscription,
                        order,
                        position,
                        "策略已升级为即时 FAK 跟单，旧挂单已撤销",
                    )
            await session.commit()

    async def process_open_orders(self, subscription_id: int) -> None:
        async with self.database.sessions() as session:
            subscription = await session.get(CopySubscription, subscription_id)
            if subscription is None:
                return
            orders = list(
                (
                    await session.scalars(
                        select(CopyOrder).where(
                            CopyOrder.subscription_id == subscription_id,
                            or_(
                                CopyOrder.status.in_(OPEN_ORDER_STATES),
                                and_(
                                    CopyOrder.order_type != "FAK",
                                    CopyOrder.status == "partially_filled",
                                ),
                            ),
                        )
                    )
                ).all()
            )
            now = utcnow()
            for order in orders:
                position = await session.get(CopyPosition, order.copy_position_id)
                if position is None:
                    continue
                if subscription.state == "paused":
                    await self._cancel_order(
                        session,
                        subscription,
                        order,
                        position,
                        "策略已暂停",
                    )
                    continue
                if order.expires_at is not None and order.expires_at <= now:
                    await self._cancel_order(session, subscription, order, position, "订单已过期")
                    continue
                if subscription.mode != "paper":
                    if order.external_order_id:
                        trader = await self._live_trader(session)
                        result = await trader.order_status(order.external_order_id)
                        await self._apply_trade_result(session, order, position, result)
                    continue
                leader_position = await session.scalar(
                    select(CurrentPosition).where(
                        CurrentPosition.wallet_id == subscription.tracked_wallet_id,
                        CurrentPosition.asset_id == order.asset_id,
                    )
                )
                if (
                    order.side == "BUY"
                    and leader_position is not None
                    and leader_position.end_date is not None
                    and now
                    >= leader_position.end_date
                    - timedelta(minutes=subscription.close_buffer_minutes)
                ):
                    await self._cancel_order(
                        session,
                        subscription,
                        order,
                        position,
                        "市场进入结算前停止下单窗口",
                    )
                    continue
                book = await self.client.fetch_order_book(order.asset_id)
                remaining = order.requested_size - order.filled_size
                result = simulate_limit_order(
                    TradeRequest(
                        asset_id=order.asset_id,
                        side=order.side,
                        size=remaining,
                        limit_price=order.limit_price,
                        expiration=int(order.expires_at.replace(tzinfo=UTC).timestamp()),
                        neg_risk=bool(position.neg_risk),
                    ),
                    book,
                )
                cumulative = TradeResult(
                    status=result.status,
                    external_order_id=None,
                    filled_size=order.filled_size + result.filled_size,
                    filled_usdc=order.filled_usdc + result.filled_usdc,
                )
                await self._apply_trade_result(session, order, position, cumulative)
            await session.commit()

    async def cancel_open_orders(self, subscription_id: int) -> None:
        async with self.database.sessions() as session:
            subscription = await session.get(CopySubscription, subscription_id)
            if subscription is None:
                return
            orders = list(
                (
                    await session.scalars(
                        select(CopyOrder).where(
                            CopyOrder.subscription_id == subscription_id,
                            or_(
                                CopyOrder.status.in_(OPEN_ORDER_STATES),
                                and_(
                                    CopyOrder.order_type != "FAK",
                                    CopyOrder.status == "partially_filled",
                                ),
                            ),
                        )
                    )
                ).all()
            )
            for order in orders:
                position = await session.get(CopyPosition, order.copy_position_id)
                if position is not None:
                    await self._cancel_order(session, subscription, order, position, "策略状态变更")
            positions = list(
                (
                    await session.scalars(
                        select(CopyPosition).where(CopyPosition.subscription_id == subscription_id)
                    )
                ).all()
            )
            for position in positions:
                position.pending_target_usdc = ZERO
                position.updated_at = utcnow()
            deferred = list(
                (
                    await session.scalars(
                        select(CopyTradeSignal).where(
                            CopyTradeSignal.subscription_id == subscription_id,
                            CopyTradeSignal.status == "deferred",
                        )
                    )
                ).all()
            )
            self._mark_signals(deferred, "skipped", "策略状态变更，累计金额已清零")
            await session.commit()

    async def _cancel_open_buys(
        self, session: object, subscription: CopySubscription, position: CopyPosition
    ) -> None:
        orders = list(
            (
                await session.scalars(
                    select(CopyOrder).where(
                        CopyOrder.subscription_id == subscription.id,
                        CopyOrder.copy_position_id == position.id,
                        CopyOrder.side == "BUY",
                        or_(
                            CopyOrder.status.in_(OPEN_ORDER_STATES),
                            and_(
                                CopyOrder.order_type != "FAK",
                                CopyOrder.status == "partially_filled",
                            ),
                        ),
                    )
                )
            ).all()
        )
        for order in orders:
            await self._cancel_order(session, subscription, order, position, "目标钱包已减仓")
        position.pending_target_usdc = ZERO

    async def _cancel_order(
        self,
        session: object,
        subscription: CopySubscription,
        order: CopyOrder,
        position: CopyPosition,
        reason: str,
    ) -> None:
        if subscription.mode == "live" and order.external_order_id:
            trader = await self._live_trader(session, allow_non_ready=True)
            await trader.cancel(order.external_order_id)
        order.status = "canceled"
        order.reason = reason
        order.updated_at = utcnow()
        if order.side == "BUY":
            position.reserved_buy_usdc = ZERO
        position.updated_at = utcnow()

    async def _liquidate(self, session: object, subscription: CopySubscription) -> None:
        positions = list(
            (
                await session.scalars(
                    select(CopyPosition).where(
                        CopyPosition.subscription_id == subscription.id,
                        CopyPosition.attributed_size > ZERO,
                    )
                )
            ).all()
        )
        for position in positions:
            await self._cancel_open_buys(session, subscription, position)
            book = await self.client.fetch_order_book(position.asset_id)
            if book.best_bid is None:
                continue
            price = market_worst_price(
                book.best_bid,
                book.tick_size,
                subscription.market_slippage_cents,
                side="SELL",
            )
            await self._place_market_order(
                session,
                subscription,
                position,
                signals=[],
                side="SELL",
                amount=position.attributed_size,
                reference_price=book.best_bid,
                worst_price=price,
                book=book,
                reason="关闭策略并清仓",
            )
        remaining = sum((position.attributed_size for position in positions), start=ZERO)
        if remaining <= ZERO:
            subscription.state = "disabled"
            subscription.updated_at = utcnow()

    async def _leader_redeemed(
        self, session: object, subscription: CopySubscription, event: PositionEvent
    ) -> None:
        position = await session.scalar(
            select(CopyPosition).where(
                CopyPosition.subscription_id == subscription.id,
                CopyPosition.asset_id == event.asset_id,
            )
        )
        if position is None or position.attributed_size <= ZERO:
            return
        existing = await session.scalar(
            select(CopyRedemption).where(CopyRedemption.copy_position_id == position.id)
        )
        if existing is not None:
            return
        now = utcnow()
        payout_per_share = (
            event.payout_amount / event.before_size
            if event.payout_amount is not None and event.before_size > ZERO
            else ZERO
        )
        status = "redeemed" if subscription.mode == "paper" else "ready"
        payout = position.attributed_size * payout_per_share
        session.add(
            CopyRedemption(
                copy_position_id=position.id,
                status=status,
                size=position.attributed_size,
                payout_usdc=payout,
                attempts=0,
                created_at=now,
                updated_at=now,
            )
        )
        if subscription.mode == "paper":
            realized = (payout or ZERO) - position.attributed_cost
            position.realized_pnl += realized
            position.attributed_size = ZERO
            position.attributed_cost = ZERO
            position.status = "redeemed"
            session.add(
                CopyLedger(
                    subscription_id=subscription.id,
                    copy_position_id=position.id,
                    type="redemption",
                    amount_usdc=payout or ZERO,
                    realized_pnl=realized,
                    timestamp=now,
                )
            )

    async def process_redemptions(self, subscription_id: int) -> None:
        async with self.database.sessions() as session:
            subscription = await session.get(CopySubscription, subscription_id)
            account = await session.get(ExecutionAccount, 1)
            if (
                subscription is None
                or subscription.mode != "live"
                or account is None
                or not account.auto_redeem
            ):
                return
            redemptions = list(
                (
                    await session.scalars(
                        select(CopyRedemption)
                        .join(CopyPosition)
                        .where(
                            CopyPosition.subscription_id == subscription_id,
                            CopyRedemption.status.in_(["ready", "error", "submitting"]),
                            CopyRedemption.attempts < 10,
                        )
                        .order_by(CopyRedemption.id.asc())
                    )
                ).all()
            )
            for redemption in redemptions:
                position = await session.get(CopyPosition, redemption.copy_position_id)
                if position is None or position.attributed_size <= ZERO:
                    redemption.status = "skipped"
                    redemption.updated_at = utcnow()
                    continue
                redemption.status = "submitting"
                redemption.attempts += 1
                redemption.updated_at = utcnow()
                await session.commit()
                try:
                    trader = await self._live_trader(session, allow_non_ready=True)
                    before_balance = await trader.collateral_balance()
                    transaction_hash = await trader.redeem(
                        condition_id=position.condition_id,
                        size=position.attributed_size,
                        outcome_index=position.outcome_index,
                        neg_risk=bool(position.neg_risk),
                    )
                    after_balance = await trader.collateral_balance()
                    payout = max(
                        ZERO,
                        after_balance - before_balance,
                        redemption.payout_usdc or ZERO,
                    )
                except Exception as error:
                    redemption.status = "error"
                    redemption.last_error = str(error)[:1000]
                    redemption.updated_at = utcnow()
                    await session.commit()
                    continue
                now = utcnow()
                realized = payout - position.attributed_cost
                redemption.status = "redeemed"
                redemption.payout_usdc = payout
                redemption.transaction_hash = transaction_hash
                redemption.last_error = None
                redemption.updated_at = now
                position.realized_pnl += realized
                position.attributed_size = ZERO
                position.attributed_cost = ZERO
                position.status = "redeemed"
                position.updated_at = now
                session.add(
                    CopyLedger(
                        subscription_id=subscription.id,
                        copy_position_id=position.id,
                        type="redemption",
                        amount_usdc=payout,
                        realized_pnl=realized,
                        detail=transaction_hash,
                        timestamp=now,
                    )
                )
                account.collateral_balance = after_balance
                account.last_balance_at = now
                account.updated_at = now
                await session.commit()

    async def _position_for_event(
        self, session: object, subscription: CopySubscription, event: PositionEvent
    ) -> CopyPosition:
        position = await session.scalar(
            select(CopyPosition).where(
                CopyPosition.subscription_id == subscription.id,
                CopyPosition.asset_id == event.asset_id,
            )
        )
        if position is not None:
            return position
        current = await session.scalar(
            select(CurrentPosition).where(
                CurrentPosition.wallet_id == subscription.tracked_wallet_id,
                CurrentPosition.asset_id == event.asset_id,
            )
        )
        now = utcnow()
        settlement_date = (
            current.end_date.date().isoformat() if current and current.end_date else None
        )
        position = CopyPosition(
            subscription_id=subscription.id,
            asset_id=event.asset_id,
            condition_id=event.condition_id,
            title=event.title,
            outcome=event.outcome,
            outcome_index=current.outcome_index if current is not None else None,
            neg_risk=None,
            event_slug=event.event_slug,
            settlement_date=settlement_date,
            attributed_size=ZERO,
            attributed_cost=ZERO,
            reserved_buy_usdc=ZERO,
            pending_target_usdc=ZERO,
            dust_size=ZERO,
            leader_size=event.after_size,
            leader_remaining_cost=event.after_size * event.after_avg_price,
            realized_pnl=ZERO,
            status="open",
            created_at=now,
            updated_at=now,
        )
        session.add(position)
        await session.flush()
        return position

    async def _risk_usage(
        self, session: object, subscription: CopySubscription, position: CopyPosition
    ) -> RiskUsage:
        positions = list(
            (
                await session.scalars(
                    select(CopyPosition).where(CopyPosition.subscription_id == subscription.id)
                )
            ).all()
        )
        total = sum(
            (item.attributed_cost + item.reserved_buy_usdc for item in positions),
            start=ZERO,
        )
        event = sum(
            (
                item.attributed_cost + item.reserved_buy_usdc
                for item in positions
                if (item.event_slug or item.condition_id)
                == (position.event_slug or position.condition_id)
            ),
            start=ZERO,
        )
        settlement_day = sum(
            (
                item.attributed_cost + item.reserved_buy_usdc
                for item in positions
                if item.settlement_date == position.settlement_date
            ),
            start=ZERO,
        )
        local_start = datetime.now(SHANGHAI).replace(hour=0, minute=0, second=0, microsecond=0)
        utc_start = local_start.astimezone(UTC).replace(tzinfo=None)
        rows = list(
            (
                await session.scalars(
                    select(CopyLedger).where(
                        CopyLedger.subscription_id == subscription.id,
                        CopyLedger.timestamp >= utc_start,
                    )
                )
            ).all()
        )
        bought_today = sum((row.amount_usdc for row in rows if row.type == "buy"), start=ZERO)
        realized = sum((row.realized_pnl for row in rows), start=ZERO)
        account = await session.get(ExecutionAccount, 1)
        capital = subscription.total_exposure_cap_usdc
        if account is not None:
            balance = account.collateral_balance
            funded = (
                min(account.budget_usdc, balance) if balance is not None else account.budget_usdc
            )
            capital = max(ZERO, funded - account.cash_reserve_usdc)
            capital = min(capital, account.max_total_exposure_usdc)
            account_daily_buy_limit = account.daily_buy_limit_usdc
            account_daily_loss_limit = account.daily_loss_limit_usdc
        else:
            account_daily_buy_limit = subscription.daily_buy_limit_usdc
            account_daily_loss_limit = subscription.daily_loss_limit_usdc
        return RiskUsage(
            bucket=position.attributed_cost + position.reserved_buy_usdc,
            event=event,
            settlement_day=settlement_day,
            total=total,
            bought_today=bought_today,
            realized_loss_today=max(ZERO, -realized),
            wallet_capital=capital,
            account_daily_buy_limit=account_daily_buy_limit,
            account_daily_loss_limit=account_daily_loss_limit,
        )

    async def _refresh_live_balance(self, session: object) -> None:
        account = await session.get(ExecutionAccount, 1)
        if account is None:
            raise TradingUnavailable("执行账户不存在，实盘买入已拒绝")
        trader = await self._live_trader(session)
        balance = await trader.collateral_balance()
        account.collateral_balance = balance
        account.last_balance_at = utcnow()
        account.updated_at = utcnow()
        if balance <= account.cash_reserve_usdc:
            account.status = "insufficient_balance"
            await session.commit()
            raise TradingUnavailable("真实 USDC 余额不足，实盘买入已停止")
        account.status = "ready"
        await session.commit()

    async def refresh_stale_live_balance(self) -> None:
        async with self.database.sessions() as session:
            subscription = await session.scalar(
                select(CopySubscription).where(
                    CopySubscription.mode == "live",
                    CopySubscription.state.in_(["active", "exit_only", "closing"]),
                )
            )
            account = await session.get(ExecutionAccount, 1)
            if subscription is None or account is None:
                return
            now = utcnow()
            if account.last_balance_at is not None and now - account.last_balance_at < timedelta(
                seconds=15
            ):
                return
            try:
                trader = await self._live_trader(session, allow_non_ready=True)
                balance = await trader.collateral_balance()
            except Exception as error:
                account.last_error = str(error)[:1000]
                account.updated_at = now
                await session.commit()
                return
            account.collateral_balance = balance
            account.last_balance_at = now
            account.last_error = None
            account.status = (
                "ready" if balance > account.cash_reserve_usdc else "insufficient_balance"
            )
            account.updated_at = now
            await session.commit()

    async def _record_skip(
        self,
        session: object,
        subscription: CopySubscription,
        position: CopyPosition,
        event: PositionEvent,
        reason: str,
    ) -> None:
        key = hashlib.sha256(
            f"{subscription.id}:{event.id}:SKIP:{position.asset_id}".encode()
        ).hexdigest()
        existing = await session.scalar(select(CopyOrder).where(CopyOrder.idempotency_key == key))
        if existing is None:
            now = utcnow()
            session.add(
                CopyOrder(
                    subscription_id=subscription.id,
                    copy_position_id=position.id,
                    leader_event_id=event.id,
                    idempotency_key=key,
                    asset_id=position.asset_id,
                    condition_id=position.condition_id,
                    side="BUY",
                    mode=subscription.mode,
                    order_type="NONE",
                    requested_size=ZERO,
                    requested_usdc=ZERO,
                    limit_price=ZERO,
                    filled_size=ZERO,
                    filled_usdc=ZERO,
                    fee_usdc=ZERO,
                    status="skipped",
                    reason=reason,
                    created_at=now,
                    updated_at=now,
                )
            )

    async def _live_trader(
        self,
        session: object,
        *,
        allow_non_ready: bool = False,
    ) -> OfficialClobTrader:
        account = await session.get(ExecutionAccount, 1)
        if account is None or (account.status != "ready" and not allow_non_ready):
            raise TradingUnavailable("执行钱包尚未就绪，实盘下单已拒绝")
        if not account.keychain_service or not account.keychain_account:
            raise TradingUnavailable("执行钱包没有钥匙串引用，实盘下单已拒绝")
        cache_key = (
            account.keychain_service,
            account.keychain_account,
            account.signature_type,
            account.funder_address,
            self.settings.clob_api_url,
        )
        if self._trader_cache is not None and self._trader_cache_key == cache_key:
            return self._trader_cache
        trader = OfficialClobTrader(
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
        self._trader_cache_key = cache_key
        self._trader_cache = trader
        return trader

    @staticmethod
    def _leader_reference_price(event: PositionEvent, *, side: str) -> Decimal | None:
        fills = [fill for fill in event.fills if fill.side == side and fill.size > ZERO]
        total_size = sum((fill.size for fill in fills), start=ZERO)
        if total_size > ZERO:
            return sum((fill.amount for fill in fills), start=ZERO) / total_size
        return event.average_fill_price

    async def _record_error(self, subscription_id: int, error: Exception) -> None:
        async with self.database.sessions() as session:
            subscription = await session.get(CopySubscription, subscription_id)
            if subscription is None:
                return
            subscription.last_error = str(error)[:1000]
            subscription.updated_at = utcnow()
            if isinstance(error, TradingUnavailable):
                subscription.state = "error"
            await session.commit()


async def latest_event_id(session: object, tracked_wallet_id: int) -> int:
    return (
        await session.scalar(
            select(func.max(PositionEvent.id)).where(PositionEvent.wallet_id == tracked_wallet_id)
        )
    ) or 0
