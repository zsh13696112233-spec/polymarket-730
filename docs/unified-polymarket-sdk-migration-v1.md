# Polymarket 统一 Python SDK 迁移方案 V1

## 1. 文档目的

本文档供新的实现窗口独立评估和执行，目标是将本项目的实盘交易与自动赎回底层从以下旧客户端和手写链路：

- `py-clob-client-v2==1.1.0`；
- `py-builder-relayer-client`；
- 手写 Adapter ABI、Deposit Wallet Batch、nonce 和 Relayer 等待逻辑；

迁移到 Polymarket 官方统一 Python SDK：

```text
polymarket-client==0.5.0
```

迁移只替换 Polymarket 接入层。现有跟单策略、风控、归因仓位、数据库幂等、失败恢复和 Dashboard 产品行为必须保留。

> 重要：当前工作区已有未提交的自动赎回相关改动，涉及
> `backend/trading.py`、`backend/copy_trading.py`、`backend/polymarket.py` 及对应测试。
> 实现者必须先执行 `git diff`，把这些改动视为已有工作，不得覆盖或回退。

## 2. 结论

迁移可行，建议交易和赎回一起迁移，而不是长期维护两套 SDK。

统一 SDK 已提供本项目需要的关键能力：

- `AsyncSecureClient` 原生异步调用；
- FAK/FOK 市价单和限价保护；
- 普通市场与 Neg Risk 市场识别；
- Deposit Wallet、Proxy、Safe 和 EOA 钱包识别；
- pUSD 与 Conditional Token 余额、授权及授权恢复；
- 订单创建与提交分离；
- typed 订单、成交、错误和 Relayer 结果；
- fill 链上结算等待；
- `redeem_positions()` 自动选择 pUSD Collateral Adapter；
- Deposit Wallet 部署、WALLET nonce、批量签名和 nonce 冲突恢复。

SDK 只能替代协议接入层，不能替代本项目的业务完成判定。以下能力仍由本项目负责：

- 下单和赎回的数据库幂等；
- 签名后、提交前持久化；
- 结果未知时禁止盲目重发；
- 归因仓位和非归因仓位隔离；
- outcome token、pUSD 和链上 receipt 二次验证；
- 实际成交、费用、成本和收益入账；
- 重启恢复、审计和人工检查状态。

## 3. 当前实现基线

### 3.1 主要代码位置

- `backend/trading.py`
  - `OfficialClobTrader` 封装旧 V2 CLOB 客户端；
  - 手工准备、提交和归一化 FAK 订单；
  - 查询余额、授权、订单、成交费用；
  - 手工编码并提交 CTF/Neg Risk 赎回。
- `backend/copy_trading.py`
  - `CopyTradingEngine` 负责事件消费、风控、订单状态机、仓位归因、Ledger 和赎回任务；
  - `_execute_order()` 已实现先签名落库、后提交；
  - `_execute_redemption()` 和 `process_redemptions()` 已实现部分重试和对账。
- `backend/models.py`
  - `CopyOrder` 保存 `idempotency_key`、`signed_order_hash`、外部订单/成交 ID 和成交结果；
  - `CopyRedemption` 对 `copy_position_id` 有唯一约束。
- `backend/keychain.py`、`backend/copy_cli.py`
  - 私钥和 Builder 凭证只存在 macOS Keychain。

### 3.2 当前必须保留的产品语义

- BUY 按 pUSD 总预算执行，SELL 按归因份额执行；
- 默认使用 FAK，未成交部分取消，不追单、不重挂；
- 每次订单只允许一次不确定性边界内的提交；
- FAK 少量不可交易尾差继续使用现有产品归一化规则；
- 只卖出或赎回系统归因仓位；
- `auto_redeem=false` 时不创建或重试自动赎回；
- 私钥、API secret、passphrase 和完整签名不得进入数据库或日志。

### 3.3 当前验证基线

在本文档编写时：

- 后端测试：`126 passed`；
- 前端 TypeScript：`tsc --noEmit` 通过。

迁移实施前应重新运行并记录基线，不能假定工作区状态未变化。

## 4. 目标架构

```text
CopyTradingEngine
    |
    v
UnifiedPolymarketTrader
    |-- AsyncSecureClient
    |     |-- create_market_order / post_order
    |     |-- list_account_trades / wait_for_order_fill_settlement
    |     |-- get_balance_allowance / targeted approvals
    |     `-- redeem_positions
    |
    `-- CriticalChainVerifier
          |-- eth_getTransactionReceipt
          |-- ERC-20 pUSD balanceOf
          |-- ERC-1155 outcome balanceOf
          `-- CTF payout numerator / denominator
```

`CopyTradingEngine` 不直接依赖 SDK 类型。SDK 返回值必须在接入层转换为本项目已有的 `PreparedMarketOrder`、`TradeResult` 和新的赎回提交结果类型。

### 4.1 客户端生命周期

- 为单例执行账户缓存一个 `AsyncSecureClient`；
- 缓存键至少包含 signer、funder、Keychain 引用和凭证类型；
- 配置变化后关闭旧客户端并重建；
- 应用停止时显式关闭客户端；
- 不得每次轮询重新派生 L2 凭证或创建 HTTP 连接池。

### 4.2 钱包类型

SDK 检测到的钱包类型作为协议行为来源，数据库中的 `signature_type` 作为兼容性校验：

| 当前配置 | SDK 钱包类型 | 行为 |
| --- | --- | --- |
| `1` | `POLY_PROXY` | 允许 |
| `3` | `DEPOSIT_WALLET` | 允许 |
| 其他或不匹配 | 任意 | 拒绝进入 ready 状态 |

Safe 和 EOA 暂不扩大为产品支持范围，即使 SDK 本身支持；如未来需要，应单独修改 API schema、设置页和验收测试。

## 5. 依赖与凭证迁移

### 5.1 依赖变更

先加入并锁定：

```toml
"polymarket-client==0.5.0"
```

完成切换和全量验证后再移除：

```text
py-clob-client-v2
py-builder-relayer-client
```

不要在同一个提交中先删除旧依赖再开始迁移，以便测试期间保留可比较实现；最终产物中不应长期存在双客户端路径。

### 5.2 Keychain 映射

- 执行私钥继续从当前 `KeychainReference` 读取；
- 当前 Builder JSON 映射为 SDK `BuilderApiKey(key, secret, passphrase)`；
- 如果以后使用 Relayer API Key，则使用独立 Keychain service，并映射为 `RelayerApiKey(key, address)`；
- Builder 凭证用于 gasless wallet action，不自动等同于订单的 `builder_code`；
- 本次迁移默认不设置 `builder_code`，避免无意改变订单归因和 Builder fee。

### 5.3 Secure Client 创建

使用显式资金钱包，禁止仅依赖 SDK 默认推导：

```python
client = await AsyncSecureClient.create(
    private_key=private_key,
    wallet=funder_address,
    api_key=BuilderApiKey(...),
)
```

创建后校验：

- `client.signer` 等于配置 signer；
- `client.wallet` 等于配置 funder；
- `client.wallet_type` 与 `signature_type` 映射一致；
- Deposit Wallet 已部署；SDK 可自动部署新默认钱包，但绑定既有钱包时不允许静默切换地址。

## 6. 交易迁移设计

### 6.1 保留签名与提交边界

禁止直接用 `place_market_order()` 替换当前 `_execute_order()`，因为该方法会把创建、签名、授权恢复和提交合并，削弱本地幂等边界。

必须采用：

```python
signed_order = await client.create_market_order(...)
# 保存签名订单指纹，CopyOrder: planned -> signed
response = await client.post_order(signed_order)
```

签名订单指纹使用稳定、无秘密的字段生成，例如对以下 canonical JSON 做 SHA-256：

- builder；
- expiration；
- maker；
- maker_amount；
- metadata；
- order_type；
- salt；
- side；
- signature_type；
- signer；
- taker_amount；
- timestamp；
- token_id；
- post_only。

完整签名不得写数据库或日志。指纹不是链上 EIP-712 order hash，字段名继续使用现有 `signed_order_hash` 以兼容数据库，但代码注释必须说明其语义是本地签名请求指纹。

### 6.2 参数映射

BUY：

```python
signed = await client.create_market_order(
    token_id=request.asset_id,
    side="BUY",
    amount=request.amount,
    max_spend=request.amount,
    max_price=request.worst_price,
    order_type="FAK",
)
```

SELL：

```python
signed = await client.create_market_order(
    token_id=request.asset_id,
    side="SELL",
    shares=request.amount,
    min_price=request.worst_price,
    order_type="FAK",
)
```

关键语义：

- `max_spend` 等于风控批准的 BUY 总预算，使平台费也受预算上限约束；
- SDK 根据 token 自动解析 tick size、费率和 Neg Risk Exchange；
- `max_price/min_price` 保留现有盘口滑点保护；
- 不再把 `neg_risk` 作为签名选项传给 SDK，但接入层应对比 SDK 市场元数据和本地 `CopyPosition.neg_risk`，不一致时拒绝提交。

### 6.3 授权处理

账户 ready 校验阶段使用 targeted approval，只批准当前 V2 Exchange 所需的 pUSD/ERC-1155 operator。

不要无条件调用 `setup_trading_approvals()`。该 helper 可能批准 Router、Perps、Auto Redeem Operator 等本需求不需要的合约，扩大权限范围。

正常订单路径使用 `post_order()`，不依赖 SDK 隐式授权重试。若返回明确的 allowance 错误：

1. 查询实际 allowance；
2. 仅补充缺失的目标 Exchange 授权；
3. 等待授权交易确认并刷新 allowance；
4. 使用同一个已签名订单最多重试一次；
5. 任何网络结果未知均停止重发并转对账。

### 6.4 订单响应映射

`RejectedOrder`：

- `fak_not_filled`、`unmatched` -> `unfilled`；
- `not_enough_balance` -> `blocked` 或明确余额错误；
- `market_not_ready` -> 可重试的业务失败；
- `unknown` -> 保留原始 message，禁止自动猜测。

`AcceptedOrder`：

- 保存 `order_id`；
- 保存全部 `trade_ids`，不能只保留第一条；
- BUY：`making_amount` 为 pUSD，`taking_amount` 为 shares；
- SELL：`making_amount` 为 shares，`taking_amount` 为 pUSD；
- FAK 有成交但未达到请求量 -> `partially_filled`；
- 继续调用现有 `normalize_fak_result()` 处理产品定义的不可交易尾差；
- `status="delayed"` 或提交后无法确认成交时进入 `submitted/reconciliation_pending`，不得重下。

当前 `CopyOrder.external_trade_id` 只能保存一条 trade ID。迁移统一扩展并复用 `CopyFill`：每个 `trade_id` 保存一条独立记录，不新增功能重叠的订单成交关联表，也不能丢弃多 fill 信息。

### 6.5 成交确认

对带 `trade_ids` 的 `AcceptedOrder` 调用：

```python
hashes = await client.wait_for_order_fill_settlement(response)
```

完成业务入账前还需要：

- 所有立即产生的 trade 到达 `CONFIRMED` 或 `FAILED`；
- 至少一个 fill 成功；
- 从 `list_account_trades(id=...)` 获取最终 price、size、fee rate、bucket 和 transaction hash；
- BUY 校验 pUSD 减少与 outcome token 增加；
- SELL 校验 outcome token 减少与 pUSD 增加；
- 余额容差继续使用链上 6 位原始单位，而非浮点数。

如果 `wait_for_order_fill_settlement()` 超时，订单仍保持 submitted/reconciliation 状态；超时不代表订单失败。

### 6.6 手续费

SDK 负责签名时的 fee-aware amount 和 `max_spend`，但本地 Ledger 仍需记录实际费用。

每个已确认 taker fill 按当时市场 fee 参数计算：

```text
effective_rate = fee_rate * (price * (1 - price)) ** exponent
platform_fee   = shares * effective_rate
builder_fee    = notional * builder_fee_rate
```

- 当前默认无 `builder_code`，所以 builder fee 为 0；
- maker fill 的 platform fee 为 0；
- 使用 SDK/市场返回的 rate 和 exponent，禁止继续使用 `size * price * bps / 10000` 的旧回退公式；
- 按官方规则保留 5 位 pUSD 精度，小于最小费用的结果为 0；
- 多 fill 分别计算后求和；
- `$1` 演练继续通过 pUSD/outcome token 前后余额验证 all-in spend。

## 7. 自动赎回迁移设计

### 7.1 SDK 调用

提交采用：

```python
handle = await client.redeem_positions(condition_id=condition_id)
```

SDK负责：

- 查询 closed market；
- 识别普通或 Neg Risk；
- 选择官方 `CtfCollateralAdapter` 或 `NegRiskCtfCollateralAdapter`；
- 使用 pUSD collateral 参数编码 `redeemPositions`；
- 按钱包类型构造 EOA、Proxy、Safe 或 Deposit Wallet 交易；
- 返回 Relayer transaction ID/hash；
- `handle.wait()` 等待 Relayer 终态。

### 7.2 提交前条件

业务层仍必须先验证：

- `auto_redeem=true`；
- 策略归因 token 大于零；
- Data API `redeemable=true` 或正式结算证据存在；
- CTF payout denominator 大于零；
- 对应 outcome numerator 大于零；
- 本 condition 不含无法归因的额外执行钱包 token；
- 尚无已完成或结果未知的同 condition 链上执行。

Adapter 会赎回同 condition 的完整钱包余额，没有 amount 参数。相同 condition 被多个策略归因时，只能提交一次，然后按每个 `copy_position` 的归因份额和链上 payout 分账。

### 7.3 Targeted approval

提交前检查 CTF ERC-1155 `isApprovedForAll(wallet, selected_adapter)`：

- 缺少时调用 SDK `approve_erc1155_for_all()`；
- 只批准本次市场类型对应 Adapter；
- 等待授权确认后再创建赎回 handle；
- 不使用全量 `setup_trading_approvals()`。

### 7.4 持久化顺序

1. 保存 outcome token 与 pUSD 提交前余额；
2. 状态写为 `submitting`；
3. 调用 `redeem_positions()`；
4. 一旦取得 handle，立即保存 `transaction_id`、已有 hash 和 `submitted_at`；
5. 状态写为 `submitted` 后再调用 `handle.wait()`；
6. wait 超时或进程中断时，只按 transaction ID、hash 和余额对账，不重新提交。

如果请求在收到 handle 前发生网络错误，视为结果未知：查询 Relayer 最近交易、钱包 activity 和链上余额后再决定，禁止立即重发。

### 7.5 完成条件

SDK 的 `STATE_CONFIRMED` 不是本地完成条件。必须同时满足：

1. Polygon receipt `status=1`；
2. 对应策略 outcome token 余额归零或减少至 1 个最小单位以内；
3. pUSD 增量与按 numerator/denominator 计算的 payout 相差不超过 1 个最小单位；
4. Data API 不再返回对应可赎回策略 token；
5. 所有关联 `copy_position` 只写入一次最终 Ledger。

任一条件不满足时不得清零归因仓位。

## 8. 数据模型调整

### 8.1 CopyOrder

保留现有字段，并明确：

- `signed_order_hash`：本地签名请求指纹；
- `external_order_id`：SDK `order_id`；
- `external_trade_id`：仅作为兼容字段，不再作为完整 trade 列表来源。
- `execution_provider`：记录订单由 `legacy` 或 `unified_sdk` 创建；签名后不可更改。

为多个 trade ID 扩展 `CopyFill`：

- `external_trade_id` 必须可唯一识别一个已确认 fill；
- 增加 transaction hash、bucket index、settlement status 和 fee；
- 指纹由 order、trade、size、price 和 bucket 组成；
- 唯一约束阻止重启后重复入账。

### 8.2 CopyRedemption

扩展至少包含：

- SDK/Adapter method；
- condition 级执行关联；
- estimated payout；
- actual pUSD delta；
- before/after outcome balance；
- before/after pUSD balance；
- Relayer transaction ID；
- transaction hash；
- execution provider；
- attempts、last error；
- next retry、submitted、completed 时间。

新增 condition 级赎回执行记录，按执行钱包和 condition 唯一。多个 `CopyRedemption` 可以引用同一执行记录。

### 8.3 历史兼容

- 不删除或重算历史订单、成交和 Ledger；
- 已完成赎回标记为 legacy，不自动包装历史 USDC.e；
- 已有 hash 的 pending 赎回只进入对账；
- 尚无 hash 且 outcome token 仍存在的 pending 任务可切换到 SDK Adapter 流程；
- migration 新字段尽量 nullable，避免伪造历史值。

## 9. 错误、重试与恢复

### 9.1 可以重试

- 明确发生在签名或提交前的配置/RPC临时失败；
- Relayer 明确拒绝且未创建 transaction ID；
- allowance 明确不足，补授权后使用同一 signed order 重试一次；
- 链上 receipt 明确 reverted；
- SDK RateLimitError，遵循 `retry_after`。

### 9.2 禁止自动重试提交

- 已保存 order ID、trade ID、Relayer transaction ID 或 tx hash；
- POST 网络断开，无法确认服务端是否接收；
- SDK wait 超时；
- Relayer 状态为 NEW、EXECUTED、MINED 等非终态；
- 余额已经发生预期方向变化但记录不完整。

### 9.3 人工检查

- receipt 成功但资产余额未变化；
- token 已减少但 pUSD 增量不一致；
- 检测到非归因 token 会被全量赎回；
- SDK 钱包类型与配置不一致；
- 同一 signed fingerprint 对应多个外部订单；
- transaction confirmed 但所有 fill 均 FAILED 或数据相互矛盾。

## 10. 测试计划

### 10.1 SDK 接入单元测试

- Keychain 私钥与 BuilderApiKey 映射，不泄露 secret；
- signature type 1/3 与 SDK wallet type 映射；
- Secure Client 缓存、配置变化重建和关闭；
- BUY/SELL FAK 参数映射；
- `max_spend` 包含费用且不超过风控额度；
- `max_price/min_price` 保留现有滑点保护；
- 普通和 Neg Risk 自动选择正确 Exchange；
- targeted approval 只批准目标合约。

### 10.2 订单测试

- create -> persist signed -> post 的顺序；
- RejectedOrder 各 error code 映射；
- 完全成交、部分成交、未成交和 delayed；
- 一个订单多个 trade ID；
- fill settlement CONFIRMED/FAILED/RETRYING/超时；
- 网络在 post 前、post 中、返回 order ID 后中断；
- 重启后按 order/trade 对账，不重复提交；
- 动态平台费、maker 零费、五位精度和多 fill 求和；
- FAK 不可交易尾差回归；
- `$1` 演练 all-in spend 和两种余额验证。

### 10.3 赎回测试

- `redeem_positions(condition_id=...)` 普通/Neg Risk；
- Adapter ERC-1155 targeted approval；
- denominator 为零不提交；
- 部分 payout；
- Deposit Wallet、Proxy 路径；
- handle transaction ID/hash 持久化顺序；
- STATE_CONFIRMED 但 token 未减少；
- token 归零但缺 hash；
- pUSD 增量不一致；
- 多策略同 condition 只提交一次并正确分账；
- 非归因余额触发人工检查；
- 服务在各阶段重启。

### 10.4 全量验证

```bash
npm run test:api
npm run typecheck
npm run test:web:unit
npm run build
npm run lint
```

## 11. 实施顺序

1. 记录当前 diff 和测试基线，保护未提交改动；
2. 加入 `polymarket-client==0.5.0`；
3. 实现 Secure Client 工厂、Keychain 凭证映射和钱包类型校验；
4. 用兼容接口实现 SDK 交易适配器；
5. 切换 create/persist/post 和多 fill 对账；
6. 补齐正确 fee、max_spend 和结算确认；
7. 用 SDK `redeem_positions` 替换手写 Adapter/Relayer 提交；
8. 完成 condition 级赎回幂等及 pUSD 二次验证；
9. 完成数据库 migration、API 和 Dashboard 状态；
10. 全量自动测试；
11. 使用最小金额 Deposit Wallet 做普通市场交易和赎回验证；
12. 验证 Neg Risk；
13. 删除旧 SDK 和不可达手写代码；
14. 观察首批真实交易和赎回，确认后移除临时兼容开关。

## 12. 上线与回退

- 使用受控配置在 SDK trader 与旧 trader 间切换，但同一订单创建后必须固定 provider，禁止中途换实现重提；
- 首先只对 `$1` 演练启用 SDK；
- 再对单个策略启用实盘 BUY/SELL；
- 然后启用普通赎回，最后启用 Neg Risk；
- 回退只影响尚未签名、尚未提交的新任务；
- 已 signed/submitted 的订单和赎回必须由创建它的 provider 完成对账；
- 不允许通过回退开关重新提交结果未知的动作。

## 13. 验收标准

- 交易和赎回均由统一 SDK 提交；
- 项目不再直接依赖旧 CLOB/Builder Relayer 客户端；
- FAK、滑点、风控、归因和 Dashboard 行为无回归；
- BUY 总支出含手续费且不超过批准预算；
- 多 fill 全部可审计且费用正确；
- fill 未确认前不完成最终资产入账；
- 赎回后 outcome token 减少且 pUSD 增加才完成；
- 普通与 Neg Risk 都可自动处理；
- Deposit Wallet 不被当作 Safe；
- 网络错误和服务重启不会重复下单、重复赎回或重复 Ledger；
- 所有凭证继续仅存在 Keychain；
- 全部现有与新增测试通过。

## 14. 官方参考

- Predictions API 总览：<https://docs.polymarket.com/api-reference/predictions/overview>
- Python 统一 SDK：<https://docs.polymarket.com/getting-started/python>
- 旧 SDK 迁移指南：<https://docs.polymarket.com/getting-started/migrate-from-previous-sdks>
- 交易 Quickstart：<https://docs.polymarket.com/trading/quickstart>
- 钱包与认证：<https://docs.polymarket.com/trading/wallets-auth>
- 仓位管理与赎回：<https://docs.polymarket.com/trading/positions/manage>
- 订单与成交状态：<https://docs.polymarket.com/trading/orders/overview>
- 交易费用：<https://docs.polymarket.com/trading/fees>
- SDK Changelog：<https://docs.polymarket.com/changelog/sdks>
- Python SDK PyPI：<https://pypi.org/project/polymarket-client/>
- Python SDK 源码：<https://github.com/Polymarket/py-sdk>
