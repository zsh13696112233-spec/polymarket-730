"""Local category subscriptions. Only public category names and cursors cross the wire."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
from sqlalchemy import DateTime, Numeric, delete, select
from sqlalchemy.dialects.sqlite import insert

from backend.collection_store import dumps, parse_time
from backend.models import (
    CollectionCache,
    CollectionSyncState,
    WhaleAutoFollowDecision,
    WhaleBackfillSignalState,
    WhaleEntry,
    WhaleMarket,
    WhaleSettings,
    WhaleTrade,
    WhaleWallet,
)
from backend.time_utils import utcnow
from backend.whale import WHALE_STATISTICS_CATEGORY_LABELS, WhaleDiscoveryScanner
from backend.whale_requests import current_whale_request_capture

MAX_AGE = timedelta(seconds=180)


async def request_collection(http, base, path, *, _validate=None, **params):
    url = f"{base}/api/collection/v1/{path}"
    capture = current_whale_request_capture()
    record_id = None
    if capture is not None:
        record_id = await capture.monitor.begin(
            scan_id=capture.scan_id,
            method="GET",
            url=url,
            query_params={key: str(value) for key, value in params.items()},
            source="collection",
        )
    response = None
    try:
        response = await http.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("protocol_version") != 1:
            raise ValueError("采集协议版本不兼容，请升级客户端或服务器")
        if _validate is not None:
            _validate(payload)
    except (Exception, asyncio.CancelledError) as error:
        if isinstance(error, httpx.TimeoutException):
            message = "请求采集服务器超时，请检查网络或服务器负载"
        elif isinstance(error, httpx.HTTPStatusError):
            message = f"采集服务器返回 HTTP {error.response.status_code}"
        elif isinstance(error, httpx.RequestError):
            message = "无法连接采集服务器，请检查 IP、端口和网络"
        elif isinstance(error, asyncio.CancelledError):
            message = "同步请求已取消"
        else:
            message = "采集服务器响应格式或协议异常，请检查客户端与服务器版本"
        if capture is not None and record_id is not None:
            await capture.monitor.complete(
                record_id,
                status="failed",
                http_status=response.status_code if response is not None else None,
                error_type=type(error).__name__,
                error_message=message,
            )
        raise
    if capture is not None and record_id is not None:
        await capture.monitor.complete(
            record_id, status="success", http_status=response.status_code
        )
    return payload


def sync_pause_reason(coverage, completed_at, *, caught_up=True):
    reasons = []
    if utcnow() - parse_time(completed_at) > MAX_AGE:
        reasons.append("最新采集批次已超过 180 秒未更新，请检查服务器采集状态")
    if not coverage:
        reasons.append("数据覆盖尚未完整，可继续同步查看")
    if not caught_up:
        reasons.append("正在追平服务器数据")
    return "；".join(reasons) + "，自动买入暂停" if reasons else None


def decode_row(model, payload):
    result = {}
    for column in model.__table__.columns:
        if column.name == "id" or column.name not in payload:
            continue
        value = payload[column.name]
        if value is not None and isinstance(column.type, DateTime):
            value = parse_time(value)
        elif value is not None and isinstance(column.type, Numeric):
            if not isinstance(value, str):
                raise ValueError("同步金额必须使用十进制字符串")
            value = Decimal(value)
            if not value.is_finite():
                raise ValueError("同步金额无效")
        result[column.name] = value
    return result


def position(payload):
    numeric = {
        "size",
        "avg_price",
        "current_price",
        "initial_value",
        "current_value",
        "cash_pnl",
        "percent_pnl",
        "total_bought",
        "realized_pnl",
    }
    return SimpleNamespace(
        **{key: Decimal(value) if key in numeric else value for key, value in payload.items()}
    )


class CollectionSyncScanner(WhaleDiscoveryScanner):
    def __init__(self, *, sync_http=None, **kwargs):
        super().__init__(**kwargs)
        self.personal_tasks_with_scan = False
        self.http = sync_http or httpx.AsyncClient(timeout=20, trust_env=False)
        self.owns_http = sync_http is None
        self.subscription_lock = asyncio.Lock()
        self.recovering = True
        self.positions = {}
        self.failed_positions = set()
        self._rule_batch = 0
        self._ready_conditions = set()

    async def stop(self):
        await super().stop()
        if self.owns_http:
            await self.http.aclose()

    async def _run(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A transient database failure must not terminate synchronization.
                pass
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=5)
            except TimeoutError:
                pass

    async def _request(self, base, path, *, _validate=None, **params):
        return await request_collection(self.http, base, path, _validate=_validate, **params)

    async def _pages(self, base, status, category, cursor, snapshot):
        events = []
        after = 0
        batch = None
        for _ in range(10000):

            def validate(page, *, after=after, batch=batch):
                if (
                    page["source_id"] != status["source_id"]
                    or page["scope_version"] != status["scope_version"]
                    or page["watermark"] != status["latest_batch"]
                ):
                    raise ValueError("采集响应水位不一致，请重新同步")
                if batch is not None and batch != page["batch"]:
                    raise ValueError("分页跨越批次，已拒绝写入")
                for event in page["events"]:
                    if event["category"] != category or event["sequence"] <= after:
                        raise ValueError("采集响应分类或序号不一致")
                    if (
                        not event["deleted"]
                        and event["payload"]["market"]["condition_id"] != event["condition_id"]
                    ):
                        raise ValueError("采集响应市场不一致")
                if not page["complete"] and page["next_after"] <= after:
                    raise ValueError("同步分页没有前进")

            page = await self._request(
                base,
                "snapshot" if snapshot else "changes",
                _validate=validate,
                categories=category,
                source_id=status["source_id"],
                watermark=status["latest_batch"],
                **({} if snapshot else {"cursor": cursor}),
                after=after,
                limit=100,
            )
            batch = page["batch"]
            events.extend(page["events"])
            if page["complete"]:
                return page, events
            if page["next_after"] <= after:
                raise ValueError("同步分页没有前进")
            after = page["next_after"]
        raise ValueError("同步批次过大，请缩小服务器采集范围")

    async def _scan(self, config):
        async with self.database.sessions() as session:
            state = await session.get(CollectionSyncState, 1)
            if state is None or not state.host:
                raise ValueError("请先在系统设置中填写采集服务器 IP 和端口")
            generation = state.subscription_version
            host = f"[{state.host}]" if ":" in state.host else state.host
            base = f"http://{host}:{state.port}"
            old_source = state.source_id
            subscriptions = json.loads(state.categories_json)
        try:
            status = await self._request(base, "status")
        except Exception:
            self.recovering = True
            async with self.subscription_lock, self.database.sessions() as session:
                state = await session.get(CollectionSyncState, 1)
                if state.subscription_version == generation:
                    for item in subscriptions.values():
                        item.update(ready=False, reason="采集服务器断线，等待重新追平")
                    state.categories_json = dumps(subscriptions)
                    state.last_error = "采集服务器断线，请检查 IP、端口和网络"
                    await session.commit()
            raise ValueError("采集服务器连接失败，自动交易暂停；请检查 IP、端口和网络") from None
        categories = json.loads(config["monitor_categories_json"])
        supported = set(status["supported_categories"])
        changed = False
        errors = []
        for category in categories:
            if category not in supported:
                subscriptions[category] = dict(ready=False, reason="服务器不支持此分类", cursor=0)
                continue
            previous = subscriptions.get(category, {})
            snapshot = (
                old_source != status["source_id"]
                or previous.get("scope_version") != status["scope_version"]
                or not previous.get("cursor")
                or previous.get("cursor", 0) > status["latest_batch"]
            )
            baseline = self.recovering or snapshot or not previous.get("ready", False)
            cursor = previous.get("cursor", 0)
            if not status["latest_batch"]:
                subscriptions[category] = dict(
                    ready=False, reason="等待服务器完成首批采集", cursor=0
                )
                continue
            if not snapshot and cursor == status["latest_batch"] and not self.recovering:
                previous["ready"] = (
                    bool(status["coverage"].get(category))
                    and utcnow() - parse_time(status["completed_at"]) <= MAX_AGE
                )
                previous["reason"] = sync_pause_reason(
                    status["coverage"].get(category), status["completed_at"]
                )
                continue
            try:
                while (
                    snapshot
                    or cursor < status["latest_batch"]
                    or (self.recovering and cursor == status["latest_batch"])
                ):
                    try:
                        page, events = await self._pages(base, status, category, cursor, snapshot)
                    except httpx.HTTPStatusError as error:
                        if error.response.status_code != 409 or snapshot:
                            raise
                        snapshot = baseline = True
                        cursor = 0
                        continue
                    async with self.subscription_lock:
                        async with self.database.sessions() as session:
                            state = await session.get(CollectionSyncState, 1)
                            current = await session.get(WhaleSettings, 1)
                            if (
                                state.subscription_version != generation
                                or current.monitor_categories_json
                                != config["monitor_categories_json"]
                                or current.updated_at != config["updated_at"]
                            ):
                                return "订阅已变更，已丢弃旧同步响应"
                            if state.source_id != status["source_id"]:
                                # Legacy/raw public cache can never become a new-source signal.
                                await session.execute(delete(WhaleTrade))
                                await session.execute(delete(CollectionCache))
                            if snapshot:
                                cached = list(
                                    (
                                        await session.scalars(
                                            select(CollectionCache).where(
                                                CollectionCache.category == category
                                            )
                                        )
                                    ).all()
                                )
                                for item in cached:
                                    await session.execute(
                                        delete(WhaleTrade).where(
                                            WhaleTrade.condition_id == item.condition_id
                                        )
                                    )
                                    await session.delete(item)
                            for event in events:
                                await self._apply_event(
                                    session,
                                    event,
                                    baseline=baseline,
                                    cutoff=parse_time(page["completed_at"]),
                                )
                            if baseline:
                                # Recovery also gates cached fills when there are no events.
                                keys = select(CollectionCache.condition_id).where(
                                    CollectionCache.category == category
                                )
                                for trade in (
                                    await session.scalars(
                                        select(WhaleTrade).where(WhaleTrade.condition_id.in_(keys))
                                    )
                                ).all():
                                    await self._gate(
                                        session,
                                        trade.proxy_wallet,
                                        trade.asset_id,
                                        trade.condition_id,
                                        parse_time(page["completed_at"]),
                                    )
                            cursor = page["scanned_through"]
                            subscriptions[category] = dict(
                                cursor=cursor,
                                scope_version=status["scope_version"],
                                ready=cursor == status["latest_batch"]
                                and bool(page["coverage"].get(category))
                                and utcnow() - parse_time(page["completed_at"]) <= MAX_AGE,
                                completed_at=page["completed_at"],
                                reason=sync_pause_reason(
                                    page["coverage"].get(category),
                                    page["completed_at"],
                                    caught_up=cursor == status["latest_batch"],
                                ),
                            )
                            state.source_id = status["source_id"]
                            state.categories_json = dumps(
                                {
                                    key: value
                                    for key, value in subscriptions.items()
                                    if key in categories
                                }
                            )
                            state.status_json = dumps(status)
                            await session.commit()
                    changed = True
                    snapshot = False
                    if cursor >= status["latest_batch"]:
                        break
            except Exception:
                subscriptions[category] = dict(
                    previous, ready=False, reason="同步中断，等待重新追平"
                )
                errors.append(f"{category} 同步失败，请检查服务器连接")
        async with self.subscription_lock:
            async with self.database.sessions() as session:
                state = await session.get(CollectionSyncState, 1)
                current = await session.get(WhaleSettings, 1)
                if (
                    state.subscription_version != generation
                    or current.monitor_categories_json != config["monitor_categories_json"]
                    or current.updated_at != config["updated_at"]
                ):
                    return "订阅已变更，已丢弃旧同步响应"
                parameters = status.get("parameters", {})
                if int(config["window_hours"]) > int(parameters.get("window_hours", 24)):
                    for item in subscriptions.values():
                        item.update(ready=False, reason="本地分析窗口超过服务器覆盖窗口")
                minimum = Decimal(parameters.get("filter_amount_usdc", "1000"))
                if (
                    min(config["new_account_threshold_usdc"], config["large_amount_threshold_usdc"])
                    < minimum
                ):
                    for item in subscriptions.values():
                        item.update(ready=False, reason="本地规则门槛低于服务器成交采集下限")
                state.categories_json = dumps(
                    {key: value for key, value in subscriptions.items() if key in categories}
                )
                state.status_json = dumps(status)
                state.last_error = "；".join(errors) or None
                await session.commit()
            self.recovering = False
            if changed:
                self._rule_batch = status["latest_batch"]
                await self._evaluate_cache(config, subscriptions)
        return (
            "；".join(
                errors
                + [
                    f"{WHALE_STATISTICS_CATEGORY_LABELS.get(key, key)}："
                    f"{value.get('reason') or '等待同步就绪，自动买入暂停'}"
                    for key, value in subscriptions.items()
                    if key in categories and not value.get("ready")
                ]
            )
            or None
        )

    async def _gate(self, session, wallet, asset, condition, cutoff):
        gate = await session.get(WhaleBackfillSignalState, (wallet, asset))
        if gate is None:
            session.add(
                WhaleBackfillSignalState(
                    proxy_wallet=wallet,
                    asset_id=asset,
                    condition_id=condition,
                    auto_follow_after=cutoff,
                    awaiting_new_buy=True,
                )
            )
            await session.flush()
        else:
            gate.auto_follow_after = max(gate.auto_follow_after, cutoff)
            gate.awaiting_new_buy = True

    async def _apply_event(self, session, event, *, baseline, cutoff):
        key = event["condition_id"]
        cached = await session.get(CollectionCache, key)
        if event["deleted"]:
            if cached is not None and cached.category == event["category"]:
                await session.delete(cached)
                await session.execute(delete(WhaleTrade).where(WhaleTrade.condition_id == key))
            return
        payload = event["payload"]
        incoming_trades = payload.get("trades", [])
        if payload.get("partial"):
            if cached is None:
                raise ValueError("增量缺少公共缓存基线，请重新获取快照")
            previous = json.loads(cached.payload_json)
            combined = {item["fingerprint"]: item for item in previous["trades"]}
            combined.update({item["fingerprint"]: item for item in incoming_trades})
            payload = {**previous, **payload}
            payload["trades"] = [combined[key] for key in payload.pop("trade_keys")]
            payload.pop("partial")
        if cached is None:
            cached = CollectionCache(
                condition_id=key, category=event["category"], payload_json="{}"
            )
            session.add(cached)
        elif cached.category != event["category"]:
            baseline = True
        cached.category = event["category"]
        cached.payload_json = dumps(payload)
        await session.merge(WhaleMarket(**decode_row(WhaleMarket, payload["market"])))
        for wallet in payload["wallets"]:
            await session.merge(WhaleWallet(**decode_row(WhaleWallet, wallet)))
        for trade in incoming_trades:
            if trade["condition_id"] != key:
                raise ValueError("成交市场与同步范围不匹配")
            decoded = decode_row(WhaleTrade, trade)
            await session.execute(
                insert(WhaleTrade)
                .values(**decoded)
                .on_conflict_do_nothing(index_elements=["fingerprint"])
            )
            if baseline or not payload["complete"] or cutoff - decoded["timestamp"] > MAX_AGE:
                await self._gate(
                    session,
                    trade["proxy_wallet"],
                    trade["asset_id"],
                    key,
                    cutoff if baseline or not payload["complete"] else decoded["timestamp"],
                )
        if payload.get("historical_until"):
            for trade in payload["trades"]:
                await self._gate(
                    session,
                    trade["proxy_wallet"],
                    trade["asset_id"],
                    key,
                    parse_time(payload["historical_until"]),
                )
        for gate in payload["gates"]:
            await self._gate(
                session,
                gate["proxy_wallet"],
                gate["asset_id"],
                key,
                parse_time(gate["auto_follow_after"]),
            )

    async def _evaluate_cache(self, config, subscriptions):
        now = utcnow()
        categories = set(json.loads(config["monitor_categories_json"]))
        self.positions = {}
        self.failed_positions = set()
        self._position_checked_at = {}
        self._ready_conditions = set()
        discovered = []
        deferred = set()
        async with self.database.sessions() as session:
            caches = list(
                (
                    await session.scalars(
                        select(CollectionCache).where(CollectionCache.category.in_(categories))
                    )
                ).all()
            )
            for cached in caches:
                payload = json.loads(cached.payload_json)
                ready = (
                    subscriptions.get(cached.category, {}).get("ready", False)
                    and payload["complete"]
                )
                if ready:
                    self._ready_conditions.add(cached.condition_id)
                for wallet, result in payload["positions"].items():
                    if not result["ok"] or now - parse_time(result["checked_at"]) > MAX_AGE:
                        self.failed_positions.add(wallet)
                        deferred.add((wallet, cached.condition_id))
                    else:
                        self.positions.setdefault(wallet, {}).update(
                            {item["asset_id"]: position(item) for item in result["positions"]}
                        )
                        self._position_checked_at[(wallet, cached.condition_id)] = parse_time(
                            result["checked_at"]
                        )
                discovered.extend(position(item) for item in payload["discoveries"])
                latest = {}
                for trade in payload["trades"]:
                    if trade["side"] == "BUY":
                        key = (trade["proxy_wallet"], trade["asset_id"])
                        latest[key] = max(
                            latest.get(key, datetime.min), parse_time(trade["timestamp"])
                        )
                for (wallet, asset), bought_at in latest.items():
                    if (
                        (wallet, cached.condition_id) in deferred
                        and ready
                        and now - bought_at <= MAX_AGE
                    ):
                        # Preserve an already established recovery cutoff, but don't consume
                        # a new live signal while its public verification is queued.
                        await self._gate(session, wallet, asset, cached.condition_id, datetime.min)
                    if not ready or now - bought_at > MAX_AGE:
                        await self._gate(
                            session,
                            wallet,
                            asset,
                            cached.condition_id,
                            now if not ready else bought_at,
                        )
            # Missing snapshots (including cancelled categories) are unknown, never empty positions.
            for entry in (await session.scalars(select(WhaleEntry))).all():
                if (entry.proxy_wallet, entry.condition_id) not in self._position_checked_at:
                    self.failed_positions.add(entry.proxy_wallet)
            await session.commit()
        await self._evaluate_rules(
            dict(config, deferred_source_positions=deferred),
            now=now,
            window_start=now - timedelta(hours=int(config["window_hours"])),
            discovered_positions=discovered,
            discovery_warnings=[],
            auto_follow_block_reason=None,
        )

    async def _refresh_markets(self, condition_ids, *, now):
        # Discovery uses the synchronized cache. Personal settlement refresh is independent.
        return None

    async def _refresh_wallets(self, wallets, *, now, cache_hours):
        return None

    async def _fetch_current_positions(self, aggregates, *, additional_targets=None):
        return self.positions, self.failed_positions

    async def _commit_rule_progress(self, session):
        state = await session.get(CollectionSyncState, 1)
        if state is not None:
            state.rule_batch = self._rule_batch

    async def _process_auto_decisions(self, decision_ids):
        for decision_id in decision_ids:
            async with self.database.sessions() as session:
                decision = await session.get(WhaleAutoFollowDecision, decision_id)
                entry = await session.get(WhaleEntry, decision.entry_id) if decision else None
                if decision is None or entry is None:
                    continue
                if (
                    decision.condition_id not in self._ready_conditions
                    or utcnow() - entry.last_buy_at > MAX_AGE
                ):
                    decision.status = "failed"
                    decision.reason = "公共信号已过期或分类未追平，不补买"
                    decision.processed_at = utcnow()
                    await session.commit()
                    continue
            await super()._process_auto_decisions([decision_id])

    async def refresh_personal_history(self):
        from backend.models import WhaleFollowPosition
        from backend.whale import PublicDataCollector

        async with self.database.sessions() as session:
            entries = list(
                await session.scalars(
                    select(WhaleEntry.condition_id)
                    .where(WhaleEntry.settlement_price.is_(None))
                    .distinct()
                )
            )
            positions = list(
                await session.scalars(
                    select(WhaleFollowPosition.condition_id)
                    .where(WhaleFollowPosition.size > 0)
                    .distinct()
                )
            )
        await PublicDataCollector._refresh_markets(
            self, list(set(entries + positions)), now=utcnow()
        )
        await self._update_entry_settlements(now=utcnow())
