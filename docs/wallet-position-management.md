# 执行钱包持仓管理（已退役）

“持仓管理”页面、导航入口及“我的跟单”中的跳转已删除，`/positions` 返回 404。
以下专属 API 同时移除：

- `GET /api/execution-account/positions`
- `GET /api/execution-account/orders`
- `POST /api/execution-account/positions/{asset_id}/sell/preview`
- `POST /api/execution-account/positions/{asset_id}/sell/execute`
- `POST /api/execution-account/orders/{order_id}/cancel/preview`
- `POST /api/execution-account/orders/{order_id}/cancel/execute`

保留历史订单、跟单账本及迁移 `0043_wallet_position_orders`，不删除用户数据。
已有订单继续由后台对账处理，不因功能移除自动撤销或释放占用。
底层订单执行与核验代码继续保留，以兼容历史订单及共用交易边界。
“我的跟单”继续展示 `wallet_manual` 来源的历史流水，文案为“钱包手动卖出”。
自动跟单、来源持仓核验与自动赎回沿用既有行为。

验证使用模拟客户端和临时数据库，不触发真实交易或授权。
