from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from backend.keychain import KeychainReference, MacOSKeychain
from backend.polymarket import OrderBookSnapshot

ZERO = Decimal("0")
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"


class TradingUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TradeRequest:
    asset_id: str
    side: str
    size: Decimal
    limit_price: Decimal
    expiration: int
    neg_risk: bool = False


@dataclass(frozen=True, slots=True)
class MarketTradeRequest:
    asset_id: str
    side: str
    amount: Decimal
    worst_price: Decimal
    neg_risk: bool = False


@dataclass(frozen=True, slots=True)
class TradeResult:
    status: str
    external_order_id: str | None
    filled_size: Decimal = ZERO
    filled_usdc: Decimal = ZERO
    average_price: Decimal | None = None
    reason: str | None = None


def simulate_limit_order(request: TradeRequest, book: OrderBookSnapshot) -> TradeResult:
    levels = (
        sorted(book.asks, key=lambda level: level.price)
        if request.side == "BUY"
        else sorted(book.bids, key=lambda level: level.price, reverse=True)
    )
    remaining = request.size
    filled_size = ZERO
    filled_usdc = ZERO
    for level in levels:
        marketable = (
            level.price <= request.limit_price
            if request.side == "BUY"
            else level.price >= request.limit_price
        )
        if not marketable:
            break
        size = min(remaining, level.size)
        filled_size += size
        filled_usdc += size * level.price
        remaining -= size
        if remaining <= ZERO:
            break
    average = filled_usdc / filled_size if filled_size > ZERO else None
    if remaining <= ZERO:
        status = "filled"
    elif filled_size > ZERO:
        status = "partially_filled"
    else:
        status = "open"
    return TradeResult(
        status=status,
        external_order_id=None,
        filled_size=filled_size,
        filled_usdc=filled_usdc,
        average_price=average,
    )


def simulate_market_order(
    request: MarketTradeRequest,
    book: OrderBookSnapshot,
) -> TradeResult:
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


class OfficialClobTrader:
    """Thin adapter around Polymarket's official py-clob-client package."""

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
        self._client_lock = threading.Lock()

    async def submit(self, request: TradeRequest) -> TradeResult:
        return await asyncio.to_thread(self._submit_sync, request)

    async def submit_market(self, request: MarketTradeRequest) -> TradeResult:
        return await asyncio.to_thread(self._submit_market_sync, request)

    def _submit_market_sync(self, request: MarketTradeRequest) -> TradeResult:
        try:
            from py_clob_client.clob_types import (
                MarketOrderArgs,
                OrderType,
                PartialCreateOrderOptions,
            )
        except ImportError as error:
            raise TradingUnavailable("缺少官方 py-clob-client，实盘下单已拒绝") from error

        try:
            client = self._client_sync()
            args = MarketOrderArgs(
                token_id=request.asset_id,
                amount=float(request.amount),
                side="BUY" if request.side == "BUY" else "SELL",
                price=float(request.worst_price),
                order_type=OrderType.FAK,
            )
            options = PartialCreateOrderOptions(neg_risk=request.neg_risk)
            signed_order = client.create_market_order(args, options)
            response = client.post_order(signed_order, OrderType.FAK)
        except Exception as error:
            raise TradingUnavailable(f"Polymarket FAK 市价下单失败：{error}") from error
        if not isinstance(response, dict):
            raise TradingUnavailable("Polymarket 实盘下单返回格式无效")
        if response.get("success") is False or response.get("errorMsg"):
            return TradeResult(
                status="unfilled",
                external_order_id=None,
                reason=str(response.get("errorMsg") or "Polymarket 拒绝订单"),
            )
        order_id = response.get("orderID") or response.get("orderId") or response.get("id")
        try:
            making = Decimal(str(response.get("makingAmount") or 0))
            taking = Decimal(str(response.get("takingAmount") or 0))
        except (ArithmeticError, ValueError):
            making = ZERO
            taking = ZERO
        if making > ZERO and taking > ZERO:
            if request.side == "BUY":
                filled_size = taking
                filled_usdc = making
            else:
                filled_size = making
                filled_usdc = taking
            requested_fill = filled_usdc if request.side == "BUY" else filled_size
            fully_filled = requested_fill >= request.amount - Decimal("0.000001")
            return TradeResult(
                status="filled" if fully_filled else "partially_filled",
                external_order_id=str(order_id) if order_id else None,
                filled_size=filled_size,
                filled_usdc=filled_usdc,
                average_price=filled_usdc / filled_size,
                reason=None if fully_filled else "FAK 部分成交，剩余已取消",
            )
        if order_id:
            try:
                result = self._order_status_sync(str(order_id))
                if result.filled_size <= ZERO:
                    return TradeResult(
                        status="unfilled",
                        external_order_id=str(order_id),
                        reason="FAK 未成交，订单已取消",
                    )
                return result
            except TradingUnavailable:
                # A successful POST with an order id is not retried. Returning the
                # accepted id lets the engine reconcile it without duplicating a market order.
                return TradeResult(status="submitted", external_order_id=str(order_id))
        raise TradingUnavailable("FAK 市价订单已提交但没有返回订单编号，结果无法确认")

    def _submit_sync(self, request: TradeRequest) -> TradeResult:
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import (
                OrderArgs,
                OrderType,
                PartialCreateOrderOptions,
            )
            from py_clob_client.constants import POLYGON
        except ImportError as error:
            raise TradingUnavailable("缺少官方 py-clob-client，实盘下单已拒绝") from error

        private_key = self.keychain.get_secret(self.key_reference)
        kwargs: dict[str, Any] = {
            "host": self.host,
            "key": private_key,
            "chain_id": POLYGON,
            "signature_type": self.signature_type,
        }
        if self.funder_address:
            kwargs["funder"] = self.funder_address
        try:
            client = ClobClient(**kwargs)
            client.set_api_creds(client.create_or_derive_api_creds())
            args = OrderArgs(
                token_id=request.asset_id,
                price=float(request.limit_price),
                size=float(request.size),
                side="BUY" if request.side == "BUY" else "SELL",
                expiration=request.expiration,
            )
            options = PartialCreateOrderOptions(neg_risk=request.neg_risk)
            # Tick size is fetched by the official client when omitted; only the
            # neg-risk flag must be explicit for multi-outcome temperature events.
            signed_order = client.create_order(args, options)
            response = client.post_order(signed_order, OrderType.GTD)
        except Exception as error:
            raise TradingUnavailable(f"Polymarket 实盘下单失败：{error}") from error
        if not isinstance(response, dict):
            raise TradingUnavailable("Polymarket 实盘下单返回格式无效")
        if response.get("success") is False or response.get("errorMsg"):
            raise TradingUnavailable(str(response.get("errorMsg") or "Polymarket 拒绝订单"))
        order_id = response.get("orderID") or response.get("orderId") or response.get("id")
        status = str(response.get("status") or "open").lower()
        normalized = {"live": "open", "matched": "filled"}.get(status, status)
        if order_id:
            try:
                return self._order_status_sync(str(order_id))
            except TradingUnavailable:
                pass
        return TradeResult(
            status=normalized,
            external_order_id=str(order_id) if order_id else None,
        )

    async def cancel(self, external_order_id: str) -> None:
        await asyncio.to_thread(self._cancel_sync, external_order_id)

    async def collateral_balance(self) -> Decimal:
        return await asyncio.to_thread(self._collateral_balance_sync)

    async def order_status(self, external_order_id: str) -> TradeResult:
        return await asyncio.to_thread(self._order_status_sync, external_order_id)

    async def redeem(
        self,
        *,
        condition_id: str,
        size: Decimal,
        outcome_index: int | None,
        neg_risk: bool,
    ) -> str:
        return await asyncio.to_thread(
            self._redeem_sync,
            condition_id,
            size,
            outcome_index,
            neg_risk,
        )

    def _client_sync(self) -> Any:
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is not None:
                return self._client
            self._client = self._build_client_sync()
            return self._client

    def _build_client_sync(self) -> Any:
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.constants import POLYGON
        except ImportError as error:
            raise TradingUnavailable("缺少官方 py-clob-client，实盘功能不可用") from error
        private_key = self.keychain.get_secret(self.key_reference)
        kwargs: dict[str, Any] = {
            "host": self.host,
            "key": private_key,
            "chain_id": POLYGON,
            "signature_type": self.signature_type,
        }
        if self.funder_address:
            kwargs["funder"] = self.funder_address
        client = ClobClient(**kwargs)
        client.set_api_creds(client.create_or_derive_api_creds())
        return client

    def _collateral_balance_sync(self) -> Decimal:
        try:
            from py_clob_client.clob_types import AssetType, BalanceAllowanceParams
        except ImportError as error:
            raise TradingUnavailable("官方客户端不支持余额查询") from error
        try:
            payload = self._client_sync().get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            raw = payload.get("balance") if isinstance(payload, dict) else None
            balance = Decimal(str(raw))
        except Exception as error:
            raise TradingUnavailable(f"无法验证执行钱包余额：{error}") from error
        # The CLOB endpoint currently returns collateral in six-decimal base units.
        return balance / Decimal("1000000")

    def _order_status_sync(self, external_order_id: str) -> TradeResult:
        try:
            payload = self._client_sync().get_order(external_order_id)
        except Exception as error:
            raise TradingUnavailable(f"无法同步实盘订单：{error}") from error
        if not isinstance(payload, dict):
            raise TradingUnavailable("实盘订单状态返回格式无效")
        matched = Decimal(str(payload.get("size_matched") or payload.get("sizeMatched") or 0))
        price = Decimal(str(payload.get("price") or 0))
        raw_status = str(payload.get("status") or "open").lower()
        status_map = {
            "live": "open",
            "matched": "filled",
            "canceled": "canceled",
            "cancelled": "canceled",
        }
        status = status_map.get(raw_status, raw_status)
        original = Decimal(str(payload.get("original_size") or payload.get("originalSize") or 0))
        if original > ZERO and matched >= original:
            status = "filled"
        elif matched > ZERO and original > matched:
            status = "partially_filled"
        elif matched > ZERO and status == "open":
            status = "partially_filled"
        return TradeResult(
            status=status,
            external_order_id=external_order_id,
            filled_size=matched,
            filled_usdc=matched * price,
            average_price=price if matched > ZERO else None,
        )

    @staticmethod
    def _redemption_call(
        condition_id: str,
        size: Decimal,
        outcome_index: int | None,
        neg_risk: bool,
    ) -> tuple[str, str]:
        try:
            from eth_abi import encode
            from eth_utils import keccak
        except ImportError as error:
            raise TradingUnavailable("缺少赎回交易编码依赖") from error
        normalized = condition_id.removeprefix("0x")
        if len(normalized) != 64:
            raise TradingUnavailable("赎回 condition id 无效")
        condition_bytes = bytes.fromhex(normalized)
        if neg_risk:
            raw_amount = int((size * Decimal("1000000")).to_integral_value())
            amounts = [0, 0]
            index = outcome_index if outcome_index in {0, 1} else 0
            amounts[index] = raw_amount
            signature = "redeemPositions(bytes32,uint256[])"
            arguments = encode(["bytes32", "uint256[]"], [condition_bytes, amounts])
            destination = NEG_RISK_ADAPTER
        else:
            signature = "redeemPositions(address,bytes32,bytes32,uint256[])"
            arguments = encode(
                ["address", "bytes32", "bytes32", "uint256[]"],
                [USDC_ADDRESS, bytes(32), condition_bytes, [1, 2]],
            )
            destination = CTF_ADDRESS
        data = "0x" + (keccak(text=signature)[:4] + arguments).hex()
        return destination, data

    def _redeem_sync(
        self,
        condition_id: str,
        size: Decimal,
        outcome_index: int | None,
        neg_risk: bool,
    ) -> str:
        destination, data = self._redemption_call(
            condition_id,
            size,
            outcome_index,
            neg_risk,
        )
        private_key = self.keychain.get_secret(self.key_reference)
        if self.signature_type == 0:
            return self._redeem_eoa(private_key, destination, data)
        try:
            from py_builder_relayer_client.client import RelayClient
            from py_builder_relayer_client.models import RelayerTxType, Transaction
            from py_builder_signing_sdk.config import BuilderApiKeyCreds, BuilderConfig
        except ImportError as error:
            raise TradingUnavailable("缺少官方 Builder Relayer 客户端") from error
        builder_reference = KeychainReference(
            service=f"{self.key_reference.service}.builder",
            account=self.key_reference.account,
        )
        try:
            values = json.loads(self.keychain.get_secret(builder_reference))
            credentials = BuilderApiKeyCreds(
                key=values["key"],
                secret=values["secret"],
                passphrase=values["passphrase"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise TradingUnavailable("Builder 凭证格式无效") from error
        relay_type = RelayerTxType.PROXY if self.signature_type == 1 else RelayerTxType.SAFE
        try:
            client = RelayClient(
                self.relayer_url,
                137,
                private_key=private_key,
                builder_config=BuilderConfig(local_builder_creds=credentials),
                relay_tx_type=relay_type,
                rpc_url=self.rpc_url,
            )
            response = client.execute(
                [Transaction(to=destination, data=data, value="0")],
                "自动赎回跟单持仓",
            )
            result = response.wait()
        except Exception as error:
            raise TradingUnavailable(f"自动赎回提交失败：{error}") from error
        if result is None:
            raise TradingUnavailable("自动赎回未在等待窗口内确认")
        transaction_hash = (
            result.get("transactionHash") if isinstance(result, dict) else None
        ) or response.transaction_hash
        if not transaction_hash:
            raise TradingUnavailable("自动赎回没有返回交易哈希")
        return str(transaction_hash)

    def _redeem_eoa(self, private_key: str, destination: str, data: str) -> str:
        try:
            import requests
            from eth_account import Account
        except ImportError as error:
            raise TradingUnavailable("缺少 EOA 赎回依赖") from error
        account = Account.from_key(private_key)
        if self.funder_address and account.address.lower() != self.funder_address.lower():
            raise TradingUnavailable("EOA 签名地址与资金地址不一致，拒绝赎回")

        def rpc(method: str, params: list[Any]) -> Any:
            response = requests.post(
                self.rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("error"):
                raise TradingUnavailable(f"Polygon RPC 拒绝赎回：{payload['error']}")
            return payload.get("result")

        try:
            nonce = int(rpc("eth_getTransactionCount", [account.address, "pending"]), 16)
            gas_price = int(rpc("eth_gasPrice", []), 16)
            transaction = {
                "chainId": 137,
                "from": account.address,
                "to": destination,
                "nonce": nonce,
                "gasPrice": gas_price,
                "value": 0,
                "data": data,
            }
            estimate = int(rpc("eth_estimateGas", [transaction]), 16)
            transaction["gas"] = max(100_000, estimate * 12 // 10)
            signed = Account.sign_transaction(transaction, private_key)
            raw = getattr(signed, "raw_transaction", None) or getattr(
                signed, "rawTransaction", None
            )
            if raw is None:
                raise TradingUnavailable("签名库没有返回原始交易")
            raw_hex = raw.hex().removeprefix("0x")
            transaction_hash = rpc("eth_sendRawTransaction", ["0x" + raw_hex])
            for _ in range(30):
                receipt = rpc("eth_getTransactionReceipt", [transaction_hash])
                if receipt is not None:
                    if int(receipt.get("status", "0x0"), 16) != 1:
                        raise TradingUnavailable("自动赎回链上执行失败")
                    return str(transaction_hash)
                time.sleep(2)
        except TradingUnavailable:
            raise
        except Exception as error:
            raise TradingUnavailable(f"EOA 自动赎回失败：{error}") from error
        raise TradingUnavailable("EOA 自动赎回未在等待窗口内确认")

    def _cancel_sync(self, external_order_id: str) -> None:
        try:
            client = self._client_sync()
            client.cancel(external_order_id)
        except Exception as error:
            raise TradingUnavailable(f"Polymarket 实盘撤单失败：{error}") from error
