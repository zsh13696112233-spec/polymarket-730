# 仓位观察｜Polymarket 钱包持仓监控

一个本机运行、只读的 Polymarket 钱包持仓监控器。它只关注实际持仓：

- 多钱包标签切换
- 持仓按当前市值从高到低排列
- 展示均价、现价、份额、成本、市值和盈亏
- 回填公开成交，并按北京时间选择 `All` 或具体购买日期查看剩余买入批次
- 同一仓位分日买入时独立计算份额、成本、市值和盈亏；减仓按 FIFO 抵扣
- 15 秒持续检测，连续部分成交合并为一条加仓/减仓记录
- 展开变化记录可查看对应逐笔成交
- 一键跳转到 Polymarket 市场页面

工具不会连接钱包、保存私钥、自动交易或读取目标钱包的未成交挂单。

## 环境要求

- Node.js `>=22.13.0`
- [uv](https://docs.astral.sh/uv/)

## 首次安装

```bash
npm ci
UV_CACHE_DIR=.uv-cache uv sync
```

如需修改默认检测参数，可复制 `.env.example` 为 `.env` 后调整。

## 本地运行

开发模式：

```bash
npm run dev:all
```

启动脚本会先正常停止占用 `3000` 或 `8730` 端口的旧服务，再启动新进程。

打开 [http://localhost:3000](http://localhost:3000)，然后在页面中添加钱包地址或 Polymarket 个人页链接。

稳定运行：

```bash
npm run build
npm run start:local
```

两个命令都只监听本机地址。关闭终端后，监控停止。

## 数据

SQLite 数据库默认保存在：

```text
data/polymarket-watch.db
```

钱包、当前持仓、公开成交、等待合并的变化以及历史明细都由后端持久化。停用钱包不会删除历史记录。

## 验证

```bash
npm test
npm run lint
```

后端 API 文档在服务启动后可通过 [http://127.0.0.1:8730/docs](http://127.0.0.1:8730/docs) 查看。
