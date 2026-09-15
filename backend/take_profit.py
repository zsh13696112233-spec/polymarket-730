from __future__ import annotations

import asyncio
from decimal import ROUND_UP, Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from backend.models import (
    ExecutionAccount,
    TakeProfitExecution,
    TakeProfitPolicy,
    TakeProfitProtection,
    WhaleOrder,
)
from backend.time_utils import utcnow
from backend.trading import market_worst_price

ZERO = Decimal("0")
ONE = Decimal("1")
USDC_UNIT = Decimal("0.000001")
TERMINAL = {
    "filled",
    "partially_filled",
    "unfilled",
    "blocked",
    "rejected",
    "cancelled",
    "canceled",
    "expired",
}


async def ensure_no_take_profit_rebuy(session: Any, wallet: str, asset_id: str) -> None:
    protection = await session.get(TakeProfitProtection, (wallet, asset_id))
    if protection is not None and protection.rebuy_blocked:
        raise ValueError("该方向已自动止盈，本市场不再自动买回")
    pending = await session.scalar(
        select(WhaleOrder.id).where(
            WhaleOrder.execution_wallet == wallet,
            WhaleOrder.asset_id == asset_id,
            WhaleOrder.source == "auto_take_profit",
            WhaleOrder.status.not_in(TERMINAL),
        )
    )
    if pending is not None:
        raise ValueError("该方向正在自动止盈，等待卖出对账后再判断")


class TakeProfitService:
    def __init__(self, executor: Any) -> None:
        self.executor = executor
        self.database = executor.database
        self.client = executor.client
        self.settings = executor.settings
        self._tick_lock = asyncio.Lock()
        self._last_resolution_check = 0.0

    async def wallet(self) -> str | None:
        async with self.database.sessions() as session:
            account = await session.get(ExecutionAccount, 1)
            if account is None:
                return None
            return (account.funder_address or "").lower() or None

    async def _require_wallet(self, expected: str) -> str:
        wallet = await self.wallet()
        if not wallet or wallet != expected.lower():
            raise ValueError("执行钱包已变化，请刷新后重新设置")
        return wallet

    async def read(self) -> dict[str, Any]:
        wallet = await self.wallet()
        async with self.database.sessions() as session:
            policy = await session.get(TakeProfitPolicy, wallet) if wallet else None
            rows = (
                list(
                    await session.scalars(
                        select(TakeProfitProtection).where(
                            TakeProfitProtection.wallet == wallet,
                            TakeProfitProtection.present.is_(True),
                        )
                    )
                )
                if wallet
                else []
            )
        enabled = bool(policy and policy.enabled)
        reason = (
            "请先配置执行钱包"
            if not wallet
            else "自动止盈已关闭"
            if not enabled
            else "实盘交易已停用"
            if not self.settings.trading_enabled
            else "后台监测已停用"
            if not self.settings.start_monitor
            else policy.reason or "运行中，每轮完成后间隔 10 秒检查"
        )
        return {
            "wallet": wallet,
            "enabled": enabled,
            "threshold_percent": policy.threshold_percent if policy else Decimal("90"),
            "running": enabled and self.settings.trading_enabled and self.settings.start_monitor,
            "reason": reason,
            "last_checked_at": policy.last_checked_at if policy else None,
            "protections": [
                {
                    "asset_id": row.asset_id,
                    "enabled": row.enabled,
                    "rebuy_blocked": row.rebuy_blocked,
                    "reason": row.reason,
                }
                for row in rows
            ],
        }

    async def update(
        self, wallet: str, enabled: bool, threshold_percent: Decimal
    ) -> dict[str, Any]:
        if not threshold_percent.is_finite() or not ZERO < threshold_percent < 100:
            raise ValueError("止盈比例必须大于 0 且小于 100")
        async with self.executor._lock:
            wallet = await self._require_wallet(wallet)
            async with self.database.sessions() as session:
                policy = await session.get(TakeProfitPolicy, wallet)
                activating = enabled and (policy is None or not policy.enabled)
            # Complete snapshot first; failures must not persist activation or its baseline.
            positions = await self.client.fetch_active_positions(wallet) if activating else None
            await self._require_wallet(wallet)
            async with self.database.sessions() as session:
                policy = await session.get(TakeProfitPolicy, wallet)
                if policy is None:
                    policy = TakeProfitPolicy(wallet=wallet, updated_at=utcnow())
                    session.add(policy)
                if positions is not None:
                    await self._observe(session, wallet, positions, auto_enroll=False)
                policy.enabled = enabled
                policy.threshold_percent = threshold_percent
                policy.reason = None
                policy.updated_at = utcnow()
                await session.commit()
        return await self.read()

    async def protect(self, wallet: str, asset_id: str, enabled: bool) -> dict[str, Any]:
        async with self.executor._lock:
            wallet = await self._require_wallet(wallet)
            positions = await self.client.fetch_active_positions(wallet)
            position = next((p for p in positions if p.asset_id == asset_id), None)
            if position is None:
                raise ValueError("该持仓已不在当前钱包，请刷新")
            await self._require_wallet(wallet)
            async with self.database.sessions() as session:
                row = await session.get(TakeProfitProtection, (wallet, asset_id))
                if row is None:
                    row = TakeProfitProtection(
                        wallet=wallet, asset_id=asset_id, condition_id=position.condition_id
                    )
                    session.add(row)
                row.enabled, row.present = enabled, True
                row.reason = None
                row.updated_at = utcnow()
                await session.commit()
        return await self.read()

    async def _observe(
        self, session: Any, wallet: str, positions: list[Any], *, auto_enroll: bool
    ) -> None:
        rows = {
            row.asset_id: row
            for row in await session.scalars(
                select(TakeProfitProtection).where(TakeProfitProtection.wallet == wallet)
            )
        }
        observed = {p.asset_id for p in positions}
        for position in positions:
            row = rows.get(position.asset_id)
            if row is None:
                row = TakeProfitProtection(
                    wallet=wallet,
                    asset_id=position.asset_id,
                    condition_id=position.condition_id,
                    enabled=auto_enroll,
                )
                session.add(row)
            elif not row.present:
                row.enabled = auto_enroll
            row.present = True
            row.updated_at = utcnow()
        for row in rows.values():
            if row.asset_id not in observed:
                row.present = False
                row.updated_at = utcnow()

    async def quote(self, asset_id: str) -> dict[str, Any]:
        from backend.whale import estimated_market_fee

        if not self.settings.trading_enabled or not self.settings.start_monitor:
            raise ValueError("实盘交易或后台监测已停用")
        wallet = await self.wallet()
        async with self.database.sessions() as session:
            policy = await session.get(TakeProfitPolicy, wallet) if wallet else None
            protection = (
                await session.get(TakeProfitProtection, (wallet, asset_id)) if wallet else None
            )
            if not policy or not policy.enabled or not protection or not protection.enabled:
                raise ValueError("该持仓未开启自动止盈")
            pending = await session.scalar(
                select(WhaleOrder.id).where(
                    WhaleOrder.execution_wallet == wallet,
                    WhaleOrder.asset_id == asset_id,
                    WhaleOrder.side == "SELL",
                    WhaleOrder.status.not_in(TERMINAL),
                )
            )
            if pending is not None:
                raise ValueError("已有卖出订单待对账，暂停重复止盈")
            threshold = policy.threshold_percent
        quote = await self.executor.quote_wallet_sell(
            asset_id, size=None, sell_all=True, order_type="FAK", price=None
        )
        if quote["wallet"] != wallet:
            raise ValueError("执行钱包已变化，请刷新")
        if quote["condition_id"] != protection.condition_id:
            raise ValueError("持仓市场归属已变化，暂停自动止盈")
        if quote["estimated_pnl"] is None:
            raise ValueError("持仓成本缺失，暂不能自动止盈")
        if not quote["cost_consistent"]:
            raise ValueError("公开持仓成本与平均买价不一致，请等待数据核对")
        cost = quote["estimated_proceeds"] - quote["estimated_fee"] - quote["estimated_pnl"]
        if cost <= ZERO:
            raise ValueError("持仓成本无法可靠核验，暂不能自动止盈")
        unit_cost = cost / quote["size"]
        trader = await self.executor._trader()
        market = await trader.sell_market(quote["condition_id"], asset_id)
        book = await self.client.fetch_order_book(asset_id)
        if (
            book.tick_size != quote["tick_size"]
            or book.min_order_size != quote["minimum_order_size"]
            or book.best_bid is None
        ):
            raise ValueError("盘口规则已变化，等待下轮检查")
        tick = book.tick_size
        latest_floor = market_worst_price(
            book.best_bid, tick, quote["sell_slippage_cents"], side="SELL"
        )
        price = (max(quote["price"], latest_floor, threshold / 100) / tick).to_integral_value(
            rounding=ROUND_UP
        ) * tick
        # Bound the fee over all potentially executable prices, including partial fills.
        # The fee curve peaks at 0.5 for the nonnegative exponents used by these markets.
        if market.fee_rate < ZERO or market.fee_exponent < ZERO:
            raise ValueError("市场费用参数无效，暂停止盈")
        while price <= min(ONE - tick, book.best_bid):
            peak = max(price, Decimal("0.5"))
            unit_fee = estimated_market_fee(ONE, peak, market.fee_rate, market.fee_exponent)
            if price - unit_fee >= threshold / 100 and price - unit_fee > unit_cost:
                break
            price += tick
        else:
            raise ValueError("尚未达到扣费后的止盈门槛，或卖出不能盈利")
        depth = sum((level.size for level in book.bids if level.price >= price), ZERO)
        if depth < quote["size"]:
            raise ValueError("止盈价格内买盘深度不足，等待下轮检查")
        quote.update(
            price=price,
            estimated_proceeds=quote["size"] * price,
            estimated_fee=quote["size"] * unit_fee,
            estimated_pnl=quote["size"] * (price - unit_fee) - cost,
            threshold_percent=threshold,
            unit_cost=unit_cost,
        )
        return quote

    async def tick(self) -> None:
        if self._tick_lock.locked() or not self.settings.start_monitor:
            return
        async with self._tick_lock:
            wallet = await self.wallet()
            if not wallet:
                return
            async with self.database.sessions() as session:
                policy = await session.get(TakeProfitPolicy, wallet)
            if policy and policy.enabled and self.settings.trading_enabled:
                try:
                    async with self.executor._lock:
                        positions = await self.client.fetch_active_positions(wallet)
                        await self._require_wallet(wallet)
                        async with self.database.sessions() as session:
                            current = await session.get(TakeProfitPolicy, wallet)
                            if current and current.enabled:
                                await self._observe(session, wallet, positions, auto_enroll=True)
                                current.last_checked_at = utcnow()
                                current.reason = None
                                await session.commit()
                    async with self.database.sessions() as session:
                        assets = list(
                            await session.scalars(
                                select(TakeProfitProtection.asset_id).where(
                                    TakeProfitProtection.wallet == wallet,
                                    TakeProfitProtection.enabled.is_(True),
                                    TakeProfitProtection.present.is_(True),
                                )
                            )
                        )
                    for asset in assets:
                        try:
                            quote = await self.quote(asset)
                            await self.executor.execute_wallet_sell(
                                quote, f"take-profit:{uuid4().hex}", auto_take_profit=True
                            )
                            reason = "止盈订单已提交，请查看成交与对账结果"
                        except ValueError as error:
                            reason = str(error)
                        except Exception:
                            reason = "止盈检查或提交未完成，请查看订单对账及连接状态"
                        async with self.database.sessions() as session:
                            row = await session.get(TakeProfitProtection, (wallet, asset))
                            if row:
                                row.reason, row.updated_at = reason, utcnow()
                                await session.commit()
                except Exception:
                    async with self.database.sessions() as session:
                        current = await session.get(TakeProfitPolicy, wallet)
                        if current:
                            current.reason = "持仓检查未完成，请检查连接并刷新；未更新覆盖基线"
                            await session.commit()
            loop_time = asyncio.get_running_loop().time()
            if loop_time - self._last_resolution_check >= 60:
                await self.resolve()
                self._last_resolution_check = loop_time

    async def resolve(self) -> None:
        async with self.database.sessions() as session:
            pairs = (
                await session.execute(
                    select(TakeProfitExecution, WhaleOrder)
                    .join(WhaleOrder, WhaleOrder.id == TakeProfitExecution.order_id)
                    .where(TakeProfitExecution.payout_rate.is_(None), WhaleOrder.filled_size > ZERO)
                )
            ).all()
        if not pairs:
            return
        resolutions = await self.client.fetch_market_resolutions(
            [order.condition_id for _, order in pairs]
        )
        async with self.database.sessions() as session:
            for execution, order in pairs:
                result = resolutions.get(order.condition_id)
                rate = result.payout_by_asset_id.get(order.asset_id) if result else None
                if rate is None or not rate.is_finite() or not ZERO <= rate <= ONE:
                    continue
                current = await session.get(TakeProfitExecution, execution.order_id)
                current.payout_rate, current.resolved_at = rate, result.resolved_at
            await session.commit()

    async def statistics(self, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        wallet = await self.wallet()
        async with self.database.sessions() as session:
            pairs = (
                (
                    await session.execute(
                        select(TakeProfitExecution, WhaleOrder)
                        .join(WhaleOrder, WhaleOrder.id == TakeProfitExecution.order_id)
                        .where(WhaleOrder.execution_wallet == wallet)
                        .order_by(
                            TakeProfitExecution.created_at.desc(),
                            TakeProfitExecution.order_id.desc(),
                        )
                    )
                ).all()
                if wallet
                else []
            )
        summary: dict[str, Any] = dict(
            realized_profit=ZERO,
            saved=ZERO,
            foregone=ZERO,
            net_impact=ZERO,
            filled_count=0,
            pending_resolution_count=0,
            pending_reconciliation_count=0,
        )
        items = []
        for execution, order in pairs:
            confirmed = [
                f for f in order.fills if f.settlement_status == "confirmed" and f.transaction_hash
            ]
            size = sum((f.size for f in confirmed), ZERO)
            # SDK confirmed fills include verified fee metadata; aggregate-only results do not.
            verified = (
                size > ZERO
                and abs(size - order.filled_size) < Decimal("0.000001")
                and order.status in TERMINAL
            )
            net = (
                sum((f.amount - f.fee_usdc for f in confirmed), ZERO).quantize(USDC_UNIT)
                if verified
                else None
            )
            cost = (size * execution.unit_cost).quantize(USDC_UNIT) if verified else None
            payout = (
                (size * execution.payout_rate).quantize(USDC_UNIT)
                if verified and execution.payout_rate is not None
                else None
            )
            saved = max(ZERO, net - payout) if payout is not None else None
            foregone = max(ZERO, payout - net) if payout is not None else None
            state = (
                "settled"
                if payout is not None
                else "pending_resolution"
                if verified
                else "pending_reconciliation"
                if order.status not in TERMINAL or order.filled_size > ZERO
                else "no_fill"
            )
            if verified:
                summary["filled_count"] += 1
                summary["realized_profit"] += net - cost
            if state == "pending_resolution":
                summary["pending_resolution_count"] += 1
            if state == "pending_reconciliation":
                summary["pending_reconciliation_count"] += 1
            if payout is not None:
                summary["saved"] += saved
                summary["foregone"] += foregone
            items.append(
                dict(
                    order_id=order.id,
                    title=order.title,
                    outcome=order.outcome,
                    threshold_percent=execution.threshold_percent,
                    filled_size=order.filled_size,
                    net_proceeds=net,
                    cost=cost,
                    cost_source=execution.cost_source,
                    realized_profit=net - cost if verified else None,
                    hypothetical_payout=payout,
                    saved=saved,
                    foregone=foregone,
                    status=state,
                    order_status=order.status,
                    created_at=execution.created_at,
                    resolved_at=execution.resolved_at,
                )
            )
        summary["net_impact"] = summary["saved"] - summary["foregone"]
        return {"summary": summary, "items": items[offset : offset + limit], "total": len(items)}

    async def run(self) -> None:
        while True:
            await asyncio.sleep(10)
            try:
                await self.tick()
            except Exception:
                # Preserve durable orders and retry read-only settlement checks next round.
                continue
