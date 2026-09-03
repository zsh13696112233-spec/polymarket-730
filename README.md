# PolyCopy｜Polymarket 链上资金监测与跟单控制台

PolyCopy 是一个本地运行的 Polymarket 链上大额资金监测、策略决策和交易管理控制台。
系统持续采集公开成交与市场数据，识别值得关注的巨鲸信号，并在同一套账本中管理手动跟单、
自动跟单、卖出、对账和结算赎回。

> 本项目包含真实资金交易能力。首次运行或只做界面、数据验证时，请先将
> `POLYMARKET_TRADING_ENABLED` 设为 `0`。不要将 API 直接暴露到公网，也不要使用存有主要资产的
> 钱包作为执行钱包。

## 当前能力

### 链上信号监测

- 在固定的 24 小时成交窗口内运行两套规则：

  - **新号大额**：仅关注注册时间处于配置窗口内的新账户。
  - **全量超大额**：不限制账户年龄，按钱包、市场和 outcome 累计买入金额。
- 结合公开档案、市场标签、当前持仓与买卖流水，区分仍在持有、已退出和对冲状态。
- 按市场聚合多钱包信号，展示相同方向与相反方向的资金分歧。
- 永久保留规则触发历史；账户加入排除名单后停止监测和统计，但不删除底层历史或真实交易账本。
- 提供手动扫描、扫描状态、失败信息和实时请求监控。

### 信号统计与通知

- 按规则、市场分类、金额层级和时间范围统计已结算信号的命中率与理论收益。
- 展示整体表现、分类表现、趋势、资金加权盈亏平衡线和逐条结算明细。
- 支持新信号、同市场方向分歧和每周命中率汇总邮件。
- 邮件记录按收件人展示排队、发送、重试和失败状态；SMTP 授权码可保存在 macOS 钥匙串中。

### 真实跟单与持仓管理

- 从链上信号生成盘口预览，并通过一次性确认信息执行真实买入或卖出。
- 为“新号大额”和“全量超大额”分别配置自动跟单开关、基础单笔金额、实际买价区间、可选低价小额档和允许的市场分类。
- 两类策略按同一市场共享最大购买次数与累计投入上限；待确认订单预占额度，未成交释放，卖出后不重置生命周期累计值。
- 自动跟单记录每次策略决策、跳过原因、成交结果和风控退出结果。
- 执行侧保留现金储备、总敞口、每日买入和每日亏损限制，并在凭证、授权、余额或市场状态异常时失败关闭。
- “我的跟单”统一展示当前持仓、买卖流水、费用、已实现盈亏、卖出和赎回结果。
- “系统设置”提供链上环境测试工具，可从官方市场链接识别 outcome，以小额真实买入并在成交后预览、确认卖出本次成交份额。
- 已有跟单周期中的 Polymarket 外部手动买卖会自动归集并对账；重复扫描通过事件指纹保持幂等。
- 已结算持仓支持自动赎回到 pUSD。存在无法归因的同市场 outcome token 或提交状态不确定时，
  系统会停止重复提交并转为人工检查。

### 首页总览

- 展示 API、数据库、扫描器和两套监测规则的运行状态。
- 汇总跟单投入、回收、费用、已实现盈亏和近 7/15/30 日趋势。
- 展示执行钱包余额、风险状态、最近自动跟单决策和实时请求流。

## 当前产品边界

项目已经移除手工添加固定钱包的轮询、持仓分析和固定钱包自动跟单接口。当前监测与执行链路是：

```text
Polymarket 公开成交与市场数据
  → 新号大额 / 全量超大额规则
  → 当前持仓核验与信号历史
  → 手动预览确认或自动策略决策
  → 统一交易执行器
  → 持仓、流水、对账与赎回
```

后续自动化应继续消费链上监测信号并复用现有交易执行器，不应重新引入固定钱包轮询链路。

## 技术架构

- **Web**：Next.js 16、React 19、TypeScript、vinext、Vite、Vitest。
- **Worker 构建**：Cloudflare Worker 入口位于 `worker/index.ts`，项目包含 Sites 托管配置。
- **API**：Python 3.12、FastAPI、SQLAlchemy Async、HTTPX。
- **数据**：SQLite（默认）与 Alembic；API 启动时自动升级到最新数据库版本。
- **交易**：`polymarket-client==0.7.1`，当前仅支持 Deposit Wallet（`signature_type=3`）。
- **凭证**：执行私钥、Builder 凭证和界面保存的 SMTP 授权码使用 macOS Keychain。

项目主要目录：

```text
app/                         Next.js 页面与 React 工作台组件
backend/main.py              FastAPI 应用、生命周期与 HTTP 接口
backend/polymarket.py        Polymarket 公开数据与市场客户端
backend/whale.py             扫描、规则、跟单、对账与赎回逻辑
backend/trading.py           统一 SDK 交易与链上执行边界
backend/models.py            SQLAlchemy 数据模型
backend/schemas.py           API 请求与响应模型
backend/alembic/versions/    数据库迁移
backend/tests/               Python 后端测试
tests/                       Web 单元测试与渲染测试
docs/                        设计与实施记录
worker/index.ts              vinext Cloudflare Worker 入口
```

## 运行要求

- macOS（真实交易和界面管理的 SMTP 凭证依赖系统钥匙串）
- Node.js 22.13 或更高版本
- Python 3.12 或更高版本
- `uv`
- 可访问 Polymarket Data API、Gamma API、CLOB API、Polygon RPC；真实钱包操作还会使用 Relayer API

依赖分别由 `package-lock.json` 和 `uv.lock` 锁定，请使用 `npm` 与 `uv`，不要混用其他包管理器。

## 安装与配置

安装依赖：

```bash
npm ci
UV_CACHE_DIR=.uv-cache uv sync
```

如果项目根目录还没有 `.env`，复制示例配置：

```bash
cp .env.example .env
```

首次启动前，建议先在 `.env` 中设置：

```dotenv
POLYMARKET_TRADING_ENABLED=0
```

常用环境变量：

| 变量 | 用途 |
| --- | --- |
| `NEXT_PUBLIC_API_BASE` | Web 访问 FastAPI 的基础地址，默认 `http://127.0.0.1:8730` |
| `POLYMARKET_DATABASE_URL` | SQLAlchemy 数据库地址，默认写入 `data/polymarket-watch.db` |
| `POLYMARKET_TRADING_ENABLED` | 真实买入、卖出与自动实盘总开关；`0` 为紧急停用 |
| `POLYMARKET_WHALE_ENABLED` | 链上巨鲸扫描模块总开关 |
| `POLYMARKET_WHALE_SCAN_INTERVAL_SECONDS` | 后台扫描间隔 |
| `POLYMARKET_WHALE_MAX_SCAN_PAGES` | 每轮扫描最多读取的成交分页数 |
| `POLYMARKET_*_API_CONCURRENCY` | Data、Gamma 与 CLOB 客户端并发上限 |
| `POLYMARKET_CLOB_API_URL` | CLOB API 地址 |
| `POLYMARKET_RELAYER_API_URL` | 钱包操作使用的 Relayer API 地址 |
| `POLYMARKET_POLYGON_RPC_URL` | Polygon RPC 地址 |
| `POLYMARKET_SMTP_*` | SMTP 的主机、端口、账号、发件人和安全模式 |

`POLYMARKET_START_MONITOR=0` 也可用于本地调试：它会阻止后台扫描器和邮件发送器随 API 启动。

## 本地运行

同时启动 Web 和 API：

```bash
npm run dev:all
```

打开 [http://localhost:3000](http://localhost:3000)。API 健康检查地址是
[http://127.0.0.1:8730/healthz](http://127.0.0.1:8730/healthz)。

只启动单个服务：

```bash
npm run dev
npm run dev:api
```

如果只需要安全地检查界面和本地服务，不希望后台扫描或实盘执行：

```bash
POLYMARKET_TRADING_ENABLED=0 POLYMARKET_START_MONITOR=0 npm run dev:all
```

默认 SQLite 文件存放在 `data/`。巨鲸请求或持仓核验失败会追加到同目录的
`whale-failures.jsonl`，单文件上限 5 MiB，并保留 3 个轮转备份。该目录、`.env`、缓存和
构建产物均已被 Git 忽略。

## 配置执行钱包

当前执行层仅支持 Deposit Wallet。先在“系统设置”页面填写签名钱包和资金钱包地址，再将签名钱包私钥
写入 macOS 钥匙串：

```bash
uv run python -m backend.trading_cli set-key --account 0xYourSigningWalletAddress
```

命令会要求输入两次私钥，输入内容不会回显。私钥不会写入 `.env`、数据库、API 响应或日志。

如需 Builder 凭证支持的 gasless 钱包操作，可单独写入同一签名账户对应的钥匙串记录：

```bash
uv run python -m backend.trading_cli set-builder-creds --account 0xYourSigningWalletAddress
```

配置完成后，在“系统设置”中执行“验证密钥与授权”和“刷新余额”。确认签名地址、Deposit Wallet 类型、
pUSD 余额及 V2 Exchange 授权都正确，再按需开启 `POLYMARKET_TRADING_ENABLED=1`。自动跟单策略仍需在
“自动跟单”页面单独开启。

## 验证与构建

运行完整测试：

```bash
npm test
```

该命令依次覆盖 TypeScript 类型检查、Vitest、生产构建、渲染 HTML 测试和后端 Pytest。

运行全部 lint 与格式检查：

```bash
npm run lint
```

常用的局部检查：

```bash
npm run typecheck
npm run test:web:unit
UV_CACHE_DIR=.uv-cache uv run pytest backend/tests
```

构建 Web：

```bash
npm run build
```

## 安全说明

- 本项目设计为本机控制台，不提供面向公网部署所需的用户认证和多租户隔离。
- 私钥、Builder secret、passphrase、完整签名内容和 SMTP 授权码不得进入源码、数据库或日志。
- `POLYMARKET_TRADING_ENABLED=0` 是新的真实买入与卖出的紧急开关，不替代钱包隔离和资金限额。
- 自动跟单是真实资金功能；价格分档、单市场次数与金额上限、分类过滤、敞口限制和每日限额不应被绕过。
- 交易提交、链上确认或赎回结果不确定时，不要盲目重试；系统会保留状态用于对账或人工检查。
- 不要通过删除 `data/` 数据库来处理扫描或迁移问题，否则会丢失信号历史、执行账本和幂等状态。
