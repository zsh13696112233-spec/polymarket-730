# PolyCopy｜Polymarket 链上资金监测

本地运行的 Polymarket 链上大额资金监测与交易控制台。

## 当前能力

- 分别监测新号大额买入与全量超大额买入，并永久保留信号历史。
- 按钱包、市场和 outcome 查看当前链上持仓与资金分歧。
- 对监测信号进行真实买入预览和二次确认。
- 查看、卖出和赎回自己的链上跟单仓位，记录费用与盈亏。
- 使用独立执行钱包；私钥仅保存在 macOS 钥匙串中。

项目不再轮询手工添加的固定钱包，也不再运行固定钱包自动跟单策略。未来的自动执行应直接消费链上监测信号，并复用现有交易执行器。

## 配置

复制 `.env.example` 为 `.env`。`POLYMARKET_TRADING_ENABLED=0` 可紧急禁止新的真实买入与卖出。

导入执行钱包私钥：

```bash
uv run python -m backend.trading_cli set-key --account 0xYourSigningWalletAddress
```

私钥输入不会回显，也不会写入数据库、API 或日志。

## 本地运行

```bash
npm ci
UV_CACHE_DIR=.uv-cache uv sync
npm run dev:all
```

打开 [http://localhost:3000](http://localhost:3000)。

## 验证

```bash
npm test
npm run lint
```
