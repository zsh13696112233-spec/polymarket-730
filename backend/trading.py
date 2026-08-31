from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from backend.keychain import KeychainError, KeychainReference, MacOSKeychain
from backend.polymarket import OrderBookSnapshot

ZERO = Decimal("0")
BASE_UNITS = Decimal("1000000")
FAK_IGNORABLE_REMAINDER_USDC = Decimal("0.50")
REDEMPTION_SIZE_TOLERANCE = Decimal("0.000001")
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
PUSD_ADDRESS = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
COLLATERAL_ADAPTER = "0xAdA100Db00Ca00073811820692005400218FcE1f"
NEG_RISK_COLLATERAL_ADAPTER = "0xadA2005600Dec949baf300f4C6120000bDB6eAab"
V2_EXCHANGE_ADDRESS = "0xE111180000d2663C0091e4f400237545B87B996B"
V2_NEG_RISK_EXCHANGE_ADDRESS = "0xe2222d279d744050d28e00520010520000310F59"


def redeemable_position_payout_rate(position: Any) -> Decimal:
    """Estimate the finalized payout exposed by a redeemable position snapshot."""
    size = Decimal(str(position.size))
    current_value = Decimal(str(position.current_value))
    rate = Decimal(str(position.current_price))
    if size > ZERO and current_value >= ZERO:
        rate = current_value / size
    rate = max(ZERO, min(Decimal("1"), rate))
    if rate <= Decimal("0.01"):
        return ZERO
    return Decimal("1") if rate >= Decimal("0.99") else rate


def market_worst_price(
    reference: Decimal,
    tick: Decimal,
    slippage_cents: Decimal,
    *,
    side: str,
) -> Decimal:
    """Apply a slippage boundary and snap it to a valid market tick."""
    buffer = slippage_cents / Decimal("100")
    raw = reference + buffer if side == "BUY" else reference - buffer
    rounding = ROUND_UP if side == "BUY" else ROUND_DOWN
    ticks = (raw / tick).to_integral_value(rounding=rounding)
    return max(tick, min(Decimal("1") - tick, ticks * tick))


def error_detail(error: Exception) -> str:
    message = str(error).strip() or type(error).__name__
    cause = error.__cause__
    if cause is None:
        return message
    cause_message = str(cause).strip()
    cause_detail = f"{type(cause).__name__}: {cause_message}" if cause_message else repr(cause)
    return f"{message} ({cause_detail})"


class TradingUnavailable(RuntimeError):
    pass


class RedemptionSubmissionUnknown(TradingUnavailable):
    """A redemption may have been submitted and must not be blindly retried."""

    def __init__(
        self,
        message: str,
        transaction_hash: str | None = None,
        transaction_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.transaction_hash = transaction_hash
        self.transaction_id = transaction_id


@dataclass(frozen=True, slots=True)
class MarketTradeRequest:
    asset_id: str
    side: str
    amount: Decimal
    worst_price: Decimal
    neg_risk: bool = False


@dataclass(frozen=True, slots=True)
class TradeFillResult:
    external_trade_id: str
    size: Decimal
    price: Decimal
    amount: Decimal
    fee_usdc: Decimal
    transaction_hash: str | None
    bucket_index: int | None
    settlement_status: str


@dataclass(frozen=True, slots=True)
class TradeResult:
    status: str
    external_order_id: str | None
    filled_size: Decimal = ZERO
    filled_usdc: Decimal = ZERO
    average_price: Decimal | None = None
    fee_usdc: Decimal = ZERO
    signed_order_hash: str | None = None
    external_trade_id: str | None = None
    external_trade_ids: tuple[str, ...] = ()
    fills: tuple[TradeFillResult, ...] = ()
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedMarketOrder:
    request: MarketTradeRequest
    signed_order: Any
    signed_order_hash: str


@dataclass(frozen=True, slots=True)
class PreparedRedemption:
    condition_id: str
    transaction_id: str | None
    transaction_hash: str | None
    handle: Any


def is_effectively_filled(
    requested: Decimal,
    filled: Decimal,
    *,
    tolerance: Decimal,
) -> bool:
    return filled >= requested - tolerance


def normalize_fak_result(request: MarketTradeRequest, result: TradeResult) -> TradeResult:
    """Keep the existing product rule for economically irrelevant FAK dust."""
    if result.status != "partially_filled":
        return result
    requested_fill = result.filled_usdc if request.side == "BUY" else result.filled_size
    if request.side == "BUY":
        tolerance = FAK_IGNORABLE_REMAINDER_USDC
    elif request.worst_price > ZERO:
        tolerance = FAK_IGNORABLE_REMAINDER_USDC / request.worst_price
    else:
        return result
    if not is_effectively_filled(request.amount, requested_fill, tolerance=tolerance):
        return result
    return dataclasses.replace(result, status="filled", reason=None)


def simulate_market_order(request: MarketTradeRequest, book: OrderBookSnapshot) -> TradeResult:
    """Execute once against current depth and cancel any unfilled remainder."""
    levels = (
        sorted(book.asks, key=lambda level: level.price)
        if request.side == "BUY"
        else sorted(book.bids, key=lambda level: level.price, reverse=True)
    )
    remaining = request.amount
    filled_size = ZERO
    filled_usdc = ZERO
    for level in levels:
        marketable = (
            level.price <= request.worst_price
            if request.side == "BUY"
            else level.price >= request.worst_price
        )
        if not marketable:
            break
        if request.side == "BUY":
            size = min(level.size, remaining / level.price)
            amount = size * level.price
            remaining -= amount
        else:
            size = min(level.size, remaining)
            amount = size * level.price
            remaining -= size
        filled_size += size
        filled_usdc += amount
        if remaining <= Decimal("0.0000001"):
            remaining = ZERO
            break
    average = filled_usdc / filled_size if filled_size > ZERO else None
    if remaining <= ZERO:
        status = "filled"
    elif filled_size > ZERO:
        status = "partially_filled"
    else:
        status = "unfilled"
    return TradeResult(
        status=status,
        external_order_id=None,
        filled_size=filled_size,
        filled_usdc=filled_usdc,
        average_price=average,
        reason=("盘口深度不足，未成交部分已取消" if remaining > ZERO else None),
    )


class UnifiedPolymarketTrader:
    """Project boundary around Polymarket's unified async Python SDK."""

    def __init__(
        self,
        *,
        host: str,
        keychain: MacOSKeychain,
        key_reference: KeychainReference,
        signature_type: int,
        funder_address: str | None,
        relayer_url: str = "https://relayer-v2.polymarket.com",
        rpc_url: str = "https://polygon.drpc.org",
    ) -> None:
        self.host = host
        self.keychain = keychain
        self.key_reference = key_reference
        self.signature_type = signature_type
        self.funder_address = funder_address
        self.relayer_url = relayer_url
        self.rpc_url = rpc_url
        self._client: Any | None = None
        self._client_lock = asyncio.Lock()

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.close()

    async def _client_async(self) -> Any:
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is not None:
                return self._client
            self._client = await self._build_client()
            return self._client

    async def _build_client(self) -> Any:
        try:
            from polymarket import AsyncSecureClient, BuilderApiKey
        except ImportError as error:
            raise TradingUnavailable("缺少 polymarket-client==0.7.1，实盘功能不可用") from error
        if self.signature_type != 3 or not self.funder_address:
            raise TradingUnavailable("统一 SDK 实盘仅允许 Deposit Wallet（signature_type=3）")
        private_key = self.keychain.get_secret(self.key_reference)
        api_key = None
        builder_reference = KeychainReference(
            service=f"{self.key_reference.service}.builder",
            account=self.key_reference.account,
        )
        try:
            values = json.loads(self.keychain.get_secret(builder_reference))
            api_key = BuilderApiKey(
                key=values["key"],
                secret=values["secret"],
                passphrase=values["passphrase"],
            )
        except KeychainError:
            # Orders do not require a Builder key. Gasless wallet operations will
            # fail closed if the wallet cannot be operated without one.
            api_key = None
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise TradingUnavailable("Builder 凭证格式无效") from error
        try:
            client = await AsyncSecureClient.create(
                private_key=private_key,
                wallet=self.funder_address,
                api_key=api_key,
            )
        except Exception as error:
            raise TradingUnavailable(f"统一 SDK 客户端创建失败：{error_detail(error)}") from error
        if str(client.wallet).lower() != self.funder_address.lower():
            await client.close()
            raise TradingUnavailable("SDK 资金钱包与配置地址不一致")
        if str(client.wallet_type) != "DEPOSIT_WALLET":
            await client.close()
            raise TradingUnavailable(f"SDK 钱包类型 {client.wallet_type} 与 signature_type 不一致")
        return client

    async def signer_address(self) -> str:
        return str((await self._client_async()).signer).lower()

    async def wallet_type(self) -> str:
        return str((await self._client_async()).wallet_type)

    async def submit_market(self, request: MarketTradeRequest) -> TradeResult:
        prepared = await self.prepare_market(request)
        return await self.submit_prepared_market(prepared)

    async def prepare_market(self, request: MarketTradeRequest) -> PreparedMarketOrder:
        client = await self._client_async()
        try:
            # The pinned SDK owns market metadata resolution. This explicit check
            # prevents stale local attribution data from changing the exchange used.
            book = await client.get_order_book(token_id=request.asset_id)
            sdk_neg_risk = book.neg_risk
            if sdk_neg_risk != request.neg_risk:
                raise TradingUnavailable("本地 Neg Risk 标记与 SDK 市场元数据不一致")
            if request.side == "BUY":
                signed_order = await client.create_market_order(
                    token_id=request.asset_id,
                    side="BUY",
                    amount=request.amount,
                    max_spend=request.amount,
                    max_price=request.worst_price,
                    order_type="FAK",
                )
            else:
                signed_order = await client.create_market_order(
                    token_id=request.asset_id,
                    side="SELL",
                    shares=request.amount,
                    min_price=request.worst_price,
                    order_type="FAK",
                )
        except TradingUnavailable:
            raise
        except Exception as error:
            raise TradingUnavailable(f"Polymarket 统一 SDK FAK 签名失败：{error}") from error
        canonical_fields = {
            field.name: getattr(signed_order, field.name)
            for field in dataclasses.fields(signed_order)
            if field.name != "signature"
        }
        canonical = json.dumps(canonical_fields, sort_keys=True, separators=(",", ":"), default=str)
        fingerprint = "0x" + hashlib.sha256(canonical.encode()).hexdigest()
        return PreparedMarketOrder(request, signed_order, fingerprint)

    async def submit_prepared_market(self, prepared: PreparedMarketOrder) -> TradeResult:
        client = await self._client_async()
        try:
            response = await client.post_order(prepared.signed_order)
        except Exception as error:
            raise TradingUnavailable(f"Polymarket FAK 提交结果不明：{error}") from error
        if not response.ok and response.code == "not_enough_balance":
            if await self._repair_missing_order_approval(prepared.request):
                try:
                    response = await client.post_order(prepared.signed_order)
                except Exception as error:
                    raise TradingUnavailable(f"补授权后 FAK 提交结果不明：{error}") from error
        if not response.ok:
            return self._rejected_result(response, prepared.signed_order_hash)
        trade_ids = tuple(str(value) for value in response.trade_ids)
        if not trade_ids:
            # An accepted FAK can be matched before the POST response is enriched
            # with trade ids. Resolve its associate trades without resubmitting.
            for attempt in range(10):
                try:
                    accepted = await client.get_order(order_id=str(response.order_id))
                    trade_ids = tuple(str(value) for value in accepted.associate_trades)
                except Exception:
                    trade_ids = ()
                if trade_ids:
                    response = response.model_copy(update={"trade_ids": trade_ids})
                    break
                if attempt < 9:
                    await asyncio.sleep(0.5)
        if not trade_ids:
            return TradeResult(
                status="submitted",
                external_order_id=str(response.order_id),
                signed_order_hash=prepared.signed_order_hash,
                external_trade_id=None,
                external_trade_ids=trade_ids,
                reason="订单已接受，等待成交 ID 和结算对账",
            )
        try:
            await client.wait_for_order_fill_settlement(response)
        except TimeoutError:
            return TradeResult(
                status="submitted",
                external_order_id=str(response.order_id),
                signed_order_hash=prepared.signed_order_hash,
                external_trade_id=trade_ids[0],
                external_trade_ids=trade_ids,
                reason="成交结算超时，等待对账",
            )
        except Exception as error:
            return TradeResult(
                status="reconciliation_pending",
                external_order_id=str(response.order_id),
                signed_order_hash=prepared.signed_order_hash,
                external_trade_id=trade_ids[0],
                external_trade_ids=trade_ids,
                reason=f"成交结算需要人工核对：{error}",
            )
        fills = await self._confirmed_fills(trade_ids)
        if not fills:
            return TradeResult(
                status="reconciliation_pending",
                external_order_id=str(response.order_id),
                signed_order_hash=prepared.signed_order_hash,
                external_trade_id=trade_ids[0],
                external_trade_ids=trade_ids,
                reason="订单已接受但没有可确认的 fill",
            )
        filled_size = sum((fill.size for fill in fills), ZERO)
        filled_usdc = sum((fill.amount for fill in fills), ZERO)
        fee_usdc = sum((fill.fee_usdc for fill in fills), ZERO)
        requested = prepared.request.amount
        actual = filled_usdc if prepared.request.side == "BUY" else filled_size
        status = "filled" if actual >= requested else "partially_filled"
        result = TradeResult(
            status=status,
            external_order_id=str(response.order_id),
            filled_size=filled_size,
            filled_usdc=filled_usdc,
            average_price=filled_usdc / filled_size if filled_size else None,
            fee_usdc=fee_usdc,
            signed_order_hash=prepared.signed_order_hash,
            external_trade_id=trade_ids[0],
            external_trade_ids=trade_ids,
            fills=fills,
            reason=("FAK 部分成交，剩余已取消" if status == "partially_filled" else None),
        )
        return normalize_fak_result(prepared.request, result)

    @staticmethod
    def _rejected_result(response: Any, fingerprint: str) -> TradeResult:
        code = str(response.code)
        status = "blocked" if code == "not_enough_balance" else "unfilled"
        retryable = code == "market_not_ready"
        return TradeResult(
            status=status,
            external_order_id=None,
            signed_order_hash=fingerprint,
            reason=(f"可重试：{response.message}" if retryable else str(response.message)),
        )

    async def _repair_missing_order_approval(self, request: MarketTradeRequest) -> bool:
        client = await self._client_async()
        asset_type = "COLLATERAL" if request.side == "BUY" else "CONDITIONAL"
        balance = await client.get_balance_allowance(
            asset_type=asset_type,
            token_id=None if request.side == "BUY" else request.asset_id,
        )
        required = int(request.amount * BASE_UNITS)
        if balance.balance < required:
            return False
        spender = V2_NEG_RISK_EXCHANGE_ADDRESS if request.neg_risk else V2_EXCHANGE_ADDRESS
        allowance = next(
            (
                value
                for address, value in balance.allowances.items()
                if address.lower() == spender.lower()
            ),
            0,
        )
        if allowance >= required:
            return False
        try:
            if request.side == "BUY":
                handle = await client.approve_erc20(
                    token_address=PUSD_ADDRESS,
                    spender_address=spender,
                    amount="max",
                    metadata="Approve pUSD for Polymarket V2 order",
                )
            else:
                handle = await client.approve_erc1155_for_all(
                    token_address=CTF_ADDRESS,
                    operator_address=spender,
                    metadata="Approve outcome tokens for Polymarket V2 order",
                )
            await handle.wait()
        except Exception as error:
            raise TradingUnavailable(f"目标 Exchange 授权失败：{error}") from error
        refreshed = await client.get_balance_allowance(
            asset_type=asset_type,
            token_id=None if request.side == "BUY" else request.asset_id,
        )
        return any(
            address.lower() == spender.lower() and value >= required
            for address, value in refreshed.allowances.items()
        )

    async def _confirmed_fills(self, trade_ids: tuple[str, ...]) -> tuple[TradeFillResult, ...]:
        client = await self._client_async()
        rows: list[TradeFillResult] = []
        for trade_id in trade_ids:
            page = await client.list_account_trades(id=trade_id).first_page()
            trade = next((item for item in page.items if str(item.id) == trade_id), None)
            if (
                trade is None
                or str(trade.status).upper() != "CONFIRMED"
                or not trade.transaction_hash
            ):
                continue
            fee = await self._fee_for_trade(trade)
            rows.append(
                TradeFillResult(
                    external_trade_id=trade_id,
                    size=Decimal(trade.size),
                    price=Decimal(trade.price),
                    amount=Decimal(trade.size) * Decimal(trade.price),
                    fee_usdc=fee,
                    transaction_hash=(
                        str(trade.transaction_hash) if trade.transaction_hash else None
                    ),
                    bucket_index=int(trade.bucket_index),
                    settlement_status=str(trade.status).lower(),
                )
            )
        return tuple(rows)

    async def _fee_for_trade(self, trade: Any) -> Decimal:
        if str(trade.trader_side).upper() != "TAKER":
            return ZERO
        try:
            client = await self._client_async()
            page = await client.list_markets(
                condition_ids=[trade.condition_id],
                page_size=1,
            ).first_page()
            market = next(
                (item for item in page.items if str(item.condition_id) == str(trade.condition_id)),
                None,
            )
            if market is None:
                raise TradingUnavailable("公开 SDK 未返回成交市场元数据")
            schedule = market.trading.fee_schedule
            if schedule is None:
                if market.trading.fees_enabled is False:
                    return ZERO
                raise TradingUnavailable("公开 SDK 市场元数据缺少手续费参数")
            price = Decimal(trade.price)
            rate = Decimal(schedule.rate)
            exponent = Decimal(str(schedule.exponent))
            effective_rate = rate * ((price * (Decimal(1) - price)) ** exponent)
            fee = Decimal(trade.size) * effective_rate
            return fee.quantize(Decimal("0.00001"), rounding=ROUND_DOWN)
        except Exception as error:
            raise TradingUnavailable(f"无法计算成交 {trade.id} 的平台费：{error}") from error

    async def trade_fee(self, external_trade_id: str) -> Decimal:
        fills = await self._confirmed_fills((external_trade_id,))
        return fills[0].fee_usdc if fills else ZERO

    async def collateral_balance(self) -> Decimal:
        payload = await (await self._client_async()).get_balance_allowance(asset_type="COLLATERAL")
        return Decimal(payload.balance) / BASE_UNITS

    async def collateral_allowances(self) -> dict[str, Decimal]:
        payload = await (await self._client_async()).get_balance_allowance(asset_type="COLLATERAL")
        return {
            address.lower(): Decimal(value) / BASE_UNITS
            for address, value in payload.allowances.items()
        }

    async def ensure_ready_approvals(self) -> None:
        """Grant only the V2 exchange approvals required by this product."""
        client = await self._client_async()
        collateral = await client.get_balance_allowance(asset_type="COLLATERAL")
        for exchange in (V2_EXCHANGE_ADDRESS, V2_NEG_RISK_EXCHANGE_ADDRESS):
            allowance = next(
                (
                    value
                    for address, value in collateral.allowances.items()
                    if address.lower() == exchange.lower()
                ),
                0,
            )
            if allowance <= 0:
                handle = await client.approve_erc20(
                    token_address=PUSD_ADDRESS,
                    spender_address=exchange,
                    amount="max",
                    metadata="Approve pUSD for Polymarket V2 exchange",
                )
                await handle.wait()
            if not await self._is_approved_for_all(CTF_ADDRESS, exchange):
                handle = await client.approve_erc1155_for_all(
                    token_address=CTF_ADDRESS,
                    operator_address=exchange,
                    metadata="Approve outcome tokens for Polymarket V2 exchange",
                )
                await handle.wait()

    async def outcome_balance(self, asset_id: str) -> Decimal:
        payload = await (await self._client_async()).get_balance_allowance(
            asset_type="CONDITIONAL", token_id=asset_id
        )
        return Decimal(payload.balance) / BASE_UNITS

    async def order_status(self, external_order_id: str) -> TradeResult:
        try:
            order = await (await self._client_async()).get_order(order_id=external_order_id)
        except Exception as error:
            raise TradingUnavailable(f"无法同步统一 SDK 订单：{error}") from error
        matched = Decimal(order.size_matched)
        original = Decimal(order.original_size)
        status = str(order.status).lower()
        trade_ids = tuple(str(value) for value in getattr(order, "associate_trades", ()))
        if matched > ZERO and trade_ids:
            fills = await self._confirmed_fills(trade_ids)
            if fills:
                filled_size = sum((fill.size for fill in fills), ZERO)
                filled_usdc = sum((fill.amount for fill in fills), ZERO)
                fee_usdc = sum((fill.fee_usdc for fill in fills), ZERO)
                status = "filled" if matched >= original else "partially_filled"
                return TradeResult(
                    status=status,
                    external_order_id=external_order_id,
                    filled_size=filled_size,
                    filled_usdc=filled_usdc,
                    average_price=filled_usdc / filled_size if filled_size else None,
                    fee_usdc=fee_usdc,
                    external_trade_id=trade_ids[0],
                    external_trade_ids=trade_ids,
                    fills=fills,
                )
        if matched > ZERO:
            return TradeResult(
                status="reconciliation_pending",
                external_order_id=external_order_id,
                external_trade_id=trade_ids[0] if trade_ids else None,
                external_trade_ids=trade_ids,
                reason="订单已有匹配数量，等待官方 confirmed fill 和交易哈希",
            )
        return TradeResult(
            status=status,
            external_order_id=external_order_id,
        )

    async def cancel(self, external_order_id: str) -> None:
        try:
            await (await self._client_async()).cancel_order(order_id=external_order_id)
        except Exception as error:
            raise TradingUnavailable(f"Polymarket 实盘撤单失败：{error}") from error

    async def start_redemption(
        self,
        *,
        condition_id: str,
        neg_risk: bool,
    ) -> PreparedRedemption:
        client = await self._client_async()
        adapter = NEG_RISK_COLLATERAL_ADAPTER if neg_risk else COLLATERAL_ADAPTER
        if not await self._is_approved_for_all(CTF_ADDRESS, adapter):
            try:
                approval = await client.approve_erc1155_for_all(
                    token_address=CTF_ADDRESS,
                    operator_address=adapter,
                    metadata="Approve attributable outcome tokens for redemption",
                )
                await approval.wait()
            except Exception as error:
                raise TradingUnavailable(f"赎回 Adapter 授权失败：{error}") from error
            if not await self._is_approved_for_all(CTF_ADDRESS, adapter):
                raise TradingUnavailable("赎回 Adapter 授权确认后仍未生效")
        try:
            handle = await client.redeem_positions(condition_id=condition_id)
        except Exception as error:
            # polymarket-client resolves market metadata with
            # `closed=true` before it builds the CTF redemption call. Gamma can
            # already omit a resolved sports market from that filtered result
            # while Data API and the chain still correctly report the outcome as
            # redeemable. In that specific pre-dispatch failure, build the same
            # public SDK call directly from the condition data we have already
            # verified on-chain.
            if "No market found for condition" not in str(error):
                raise RedemptionSubmissionUnknown(
                    f"自动赎回提交结果不明：{error_detail(error)}"
                ) from error
            try:
                from polymarket.calls import ctf_redeem_positions_call

                call = ctf_redeem_positions_call(
                    ctf=adapter,
                    collateral=PUSD_ADDRESS,
                    condition_id=condition_id,
                )
                handle = await client.execute_transaction(
                    calls=[call],
                    metadata=f"Redeem positions for condition {condition_id}",
                )
            except Exception as fallback_error:
                raise RedemptionSubmissionUnknown(
                    f"自动赎回提交结果不明：{error_detail(fallback_error)}"
                ) from fallback_error
        return PreparedRedemption(
            condition_id=condition_id,
            transaction_id=getattr(handle, "transaction_id", None),
            transaction_hash=getattr(handle, "transaction_hash", None),
            handle=handle,
        )

    async def wait_redemption(self, prepared: PreparedRedemption) -> str:
        try:
            outcome = await prepared.handle.wait()
        except Exception as error:
            raise RedemptionSubmissionUnknown(
                f"自动赎回结果待确认：{error}",
                prepared.transaction_hash,
                prepared.transaction_id,
            ) from error
        transaction_hash = getattr(outcome, "transaction_hash", None) or prepared.transaction_hash
        if not transaction_hash:
            raise RedemptionSubmissionUnknown(
                "自动赎回确认结果没有交易哈希",
                transaction_id=prepared.transaction_id,
            )
        return str(transaction_hash)

    async def redeem(
        self,
        *,
        condition_id: str,
        size: Decimal,
        outcome_index: int | None,
        neg_risk: bool,
    ) -> str:
        del size, outcome_index
        return await self.wait_redemption(
            await self.start_redemption(condition_id=condition_id, neg_risk=neg_risk)
        )

    async def onchain_outcome_balance(self, asset_id: str) -> Decimal:
        wallet = self._validated_wallet_hex()
        try:
            token_id = int(asset_id)
        except ValueError as error:
            raise TradingUnavailable("outcome token id 无效，无法链上对账") from error
        data = "0x00fdd58e" + f"{wallet:064x}{token_id:064x}"
        return Decimal(await self._rpc_uint_call(CTF_ADDRESS, data)) / BASE_UNITS

    def _onchain_outcome_balance_sync(self, asset_id: str) -> Decimal:
        """Synchronous verifier retained for diagnostics and focused unit tests."""
        wallet = self._validated_wallet_hex()
        try:
            token_id = int(asset_id)
        except ValueError as error:
            raise TradingUnavailable("outcome token id 无效，无法链上对账") from error
        data = "0x00fdd58e" + f"{wallet:064x}{token_id:064x}"
        result = self._rpc_sync("eth_call", [{"to": CTF_ADDRESS, "data": data}, "latest"])
        return Decimal(int(str(result or "0x0"), 16)) / BASE_UNITS

    async def onchain_collateral_balance(self) -> Decimal:
        wallet = self._validated_wallet_hex()
        data = "0x70a08231" + f"{wallet:064x}"
        return Decimal(await self._rpc_uint_call(PUSD_ADDRESS, data)) / BASE_UNITS

    async def onchain_redemption_payout_rate(
        self,
        condition_id: str,
        outcome_index: int | None,
        neg_risk: bool,
    ) -> Decimal | None:
        if neg_risk or outcome_index is None:
            return None
        normalized = condition_id.removeprefix("0x")
        if len(normalized) != 64 or outcome_index < 0:
            raise TradingUnavailable("赎回 condition/outcome 无效，无法验证链上结算")
        denominator = await self._rpc_uint_call(CTF_ADDRESS, f"0xdd34de67{normalized}")
        if denominator <= 0:
            return None
        numerator = await self._rpc_uint_call(
            CTF_ADDRESS, f"0x0504c814{normalized}{outcome_index:064x}"
        )
        return Decimal(numerator) / Decimal(denominator)

    async def transaction_receipt_success(self, transaction_hash: str) -> bool | None:
        receipt = await self._rpc("eth_getTransactionReceipt", [transaction_hash])
        if receipt is None:
            return None
        return int(str(receipt.get("status") or "0x0"), 16) == 1

    async def _is_approved_for_all(self, token: str, operator: str) -> bool:
        owner = self._validated_wallet_hex()
        operator_int = int(operator.removeprefix("0x"), 16)
        data = "0xe985e9c5" + f"{owner:064x}{operator_int:064x}"
        return bool(await self._rpc_uint_call(token, data))

    def _validated_wallet_hex(self) -> int:
        normalized = (self.funder_address or "").removeprefix("0x")
        if len(normalized) != 40:
            raise TradingUnavailable("缺少可用于链上对账的执行钱包地址")
        return int(normalized, 16)

    async def _rpc_uint_call(self, to: str, data: str) -> int:
        result = await self._rpc("eth_call", [{"to": to, "data": data}, "latest"])
        return int(str(result or "0x0"), 16)

    async def _rpc(self, method: str, params: list[Any]) -> Any:
        return await asyncio.to_thread(self._rpc_sync, method, params)

    def _rpc_sync(self, method: str, params: list[Any]) -> Any:
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        ).encode()
        request = Request(
            self.rpc_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "polymarket-wallet-monitor/0.1",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=15) as response:  # noqa: S310
                body = json.loads(response.read().decode())
        except (OSError, URLError, ValueError, json.JSONDecodeError) as error:
            raise TradingUnavailable(f"无法读取 Polygon 链上状态：{error}") from error
        if body.get("error"):
            raise TradingUnavailable(f"Polygon RPC 链上对账失败：{body['error']}")
        return body.get("result")
