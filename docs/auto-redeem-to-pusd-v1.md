# 自动赎回并直接获得 pUSD｜需求文档 V1

## 1. 背景

当前自动赎回通过 Conditional Tokens Framework（CTF）直接赎回 outcome token，赎回所得为 Polygon USDC.e。Polymarket V2 使用 pUSD 作为交易抵押品，因此用户仍会在官方 WebUI 看到 `Confirm pending deposit / Activate Funds`，需要再次确认后才能把 USDC.e 转为可交易的 pUSD。

Polymarket 官方提供 `CtfCollateralAdapter` 与 `NegRiskCtfCollateralAdapter`。适配器可以在同一条链上流程中完成：

1. 销毁已结算的 outcome token；
2. 接收 CTF 释放的 USDC.e；
3. 将 USDC.e 封装为 pUSD；
4. 把 pUSD 返回执行钱包。

本需求将自动赎回升级为端到端自动结算，避免未来再次出现人工激活资金提示。

## 2. 当前资金处理说明

- 当前已经赎回的约 20.24 USDC.e 不在本次改造的自动追溯范围内。
- 用户本次通过 Polymarket WebUI 手动点击“继续”完成资金激活。
- 新功能上线后，只处理上线时仍持有 outcome token 的仓位及未来新结算仓位。
- 系统不得因本次升级重复赎回、重复入账或再次处理已完成的历史赎回记录。

## 3. 目标

- 市场完成链上结算后，自动将获胜 outcome token 兑换为 pUSD。
- 自动赎回后无需用户前往 Polymarket WebUI 确认资金激活。
- 只有在 outcome token 余额减少且 pUSD 余额增加得到验证后，才将任务标记为完成。
- 保留幂等、失败重试、未知提交结果对账和历史审计能力。
- 同时支持普通市场与 Neg Risk 市场。

## 4. 非目标

- 不自动处理用户手工交易形成、无法归因到策略的仓位。
- 不自动包装升级前已经存在的普通 USDC.e 余额。
- 不修改跟单比例、风控额度、开仓或平仓策略。
- 不改变用户手动关闭“自动赎回”后的行为。

## 5. 用户故事

作为已开启自动赎回的用户，我希望策略仓位结算后自动变成 pUSD，使资金可以直接进入下一次自动交易，无需在 Polymarket WebUI 再次点击确认。

作为系统维护者，我希望每次自动赎回都有明确的提交记录、交易哈希、余额变化和失败原因，避免外层交易成功但内部赎回无效时错误清零仓位。

## 6. 功能需求

### 6.1 触发条件

满足以下条件时进入自动赎回候选队列：

- 执行账户 `auto_redeem=true`；
- 归因仓位数量大于零；
- Data API 将执行钱包对应 asset 标记为 `redeemable=true`，或官方结算结果已确定；
- 链上 payout denominator 大于零；
- 对应 outcome 的链上 payout numerator 大于零；
- 尚无已完成的同仓位赎回任务。

不得要求来源钱包先执行赎回，也不得仅依赖 Gamma 的 `closed/finalized` 状态。

### 6.2 普通市场赎回

- 使用官方 `CtfCollateralAdapter`。
- 适配器地址以 Polymarket 官方合约清单为准，当前 Polygon 主网地址为 `0xAdA100Db00Ca00073811820692005400218FcE1f`。
- outcome token、condition ID、outcome index、CTF collateral 和接收钱包必须相互匹配。
- 最终接收资产必须为 pUSD，而不是遗留在钱包中的 USDC.e。

### 6.3 Neg Risk 市场赎回

- 使用官方 `NegRiskCtfCollateralAdapter`。
- 当前 Polygon 主网地址为 `0xadA2005600Dec949baf300f4C6120000bDB6eAab`。
- 使用 Neg Risk 对应的赎回参数和 outcome index，不得复用普通 CTF 调用编码。

### 6.4 Deposit Wallet 提交

- `signature_type=3` 必须使用 Deposit Wallet 的 Wallet Batch Relayer 接口。
- 不得将 Deposit Wallet 映射为 Safe Relayer 类型。
- 提交前校验签名地址推导出的 Deposit Wallet 与资金地址一致。
- 提交前确认 Deposit Wallet 已部署，并获取 `WALLET` 类型 nonce。
- Relayer 凭证继续从 macOS Keychain 读取，不进入数据库或日志。

### 6.5 成功判定

Relayer 返回交易哈希或外层交易状态成功，不足以判定赎回完成。必须同时满足：

1. 链上交易回执成功；
2. 对应 outcome token 余额归零，或减少到允许的精度误差以内；
3. pUSD 余额增加量与链上 payout 基本一致；
4. 不再存在对应的可赎回策略 token；
5. 数据库仅写入一条最终赎回入账记录。

任一条件不满足时，任务保持 `pending` 或进入待对账状态，不得清零归因仓位。

### 6.6 失败与重试

- 链上 payout 尚未生效：保持待处理，只轮询，不提交空交易。
- RPC、Relayer 或 Builder 凭证临时失败：记录原始错误并按退避策略重试。
- 已取得交易哈希但结果未知：停止盲目重发，优先查询交易回执及两种资产余额。
- 外层交易成功但 token 未减少：保持待处理，记录交易哈希与内部调用异常，不得入账。
- token 已归零但缺少交易哈希：允许通过链上余额和执行钱包活动完成对账，但必须保留审计说明。
- 成功重试后清除对应任务和策略上的历史错误。

### 6.7 幂等要求

- 每个 `copy_position_id` 最多存在一个赎回任务。
- 已完成或已安全对账的任务不得再次提交。
- 服务重启、重复事件、Data API 延迟和 Gamma 状态反复不得造成重复赎回或重复收益入账。
- 同一 asset 被多个策略归因时，链上只执行必要次数，并按归因仓位正确分配 payout 和收益。

## 7. 数据与展示需求

赎回记录应至少保留：

- 归因仓位 ID；
- 赎回方式：普通 Adapter 或 Neg Risk Adapter；
- outcome token 数量；
- 预计 payout；
- 实际 pUSD 增量；
- 交易哈希；
- 尝试次数；
- 原始失败原因；
- 创建、更新和完成时间。

Dashboard 应区分以下状态：

- 等待链上结算；
- 正在自动赎回；
- 交易已提交，等待链上确认；
- 赎回成功，pUSD 已到账；
- 赎回失败，等待重试；
- 需要人工检查。

成功记录应显示“pUSD 已到账”，不得仅显示“赎回交易成功”。

## 8. 安全要求

- 不在日志中输出私钥、Builder secret、passphrase 或完整签名内容。
- 合约地址必须来自受控常量，并与官方 Polygon 主网地址进行测试校验。
- 禁止仅依据 Data API 的 `redeemable=true` 提交交易；必须同时验证链上 payout。
- 禁止仅依据 Relayer 外层状态更新本地资产。
- RPC 请求必须设置兼容的 User-Agent、超时和错误解析。
- 自动赎回关闭时，不得新建或重试自动赎回交易。

## 9. 验收标准

### 9.1 正常流程

- 普通获胜仓位完成结算后，系统自动调用普通 Adapter。
- outcome token 余额归零。
- pUSD 余额按 payout 增加。
- Polymarket WebUI 不再出现该笔资金的 `Activate Funds` 提示。
- 本地仓位变为 `redeemed`，赎回任务变为 `completed`。

### 9.2 未完成结算

- Data API 已显示 `redeemable=true`，但链上 payout denominator 仍为零时，不提交交易。
- 任务保持待处理，链上结算完成后自动继续。

### 9.3 无效外层交易

- 模拟 Relayer 外层交易成功但 token 未减少。
- 系统不得清零仓位或确认收益。
- 任务保留交易信息并进入重试或人工检查状态。

### 9.4 部分兑付

- 50/50 或其他部分 payout 市场按链上 numerator/denominator 计算实际 pUSD。
- 不得强制按每份 1 pUSD 入账。

### 9.5 重启恢复

- 在计划、提交、确认和余额对账任一阶段重启服务，恢复后不得重复提交或重复入账。

### 9.6 回归测试

- 全部现有后端测试通过。
- 新增普通 Adapter、Neg Risk Adapter、Deposit Wallet Batch、链上 payout、余额校验、未知结果和幂等测试。

## 10. 上线方案

1. 完成 Adapter 调用编码及单元测试；
2. 在测试钱包用最小仓位验证普通市场；
3. 验证 pUSD 增量与 CTF payout 一致；
4. 验证 WebUI 不出现待激活资金；
5. 再验证 Neg Risk 市场；
6. 发布后监控首批真实赎回交易；
7. 保留直接 CTF 赎回代码作为受控回退方案，但默认不得启用。

## 11. 官方参考

- Polymarket pUSD：<https://docs.polymarket.com/concepts/pusd>
- Polymarket 合约地址：<https://docs.polymarket.com/resources/contracts>
- Polymarket 存款与自动封装：<https://docs.polymarket.com/cn/trading/bridge/deposit>
- Polymarket 钱包与身份验证：<https://docs.polymarket.com/cn/trading/wallets-auth>
