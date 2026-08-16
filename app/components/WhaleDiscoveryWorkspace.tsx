"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { PolyCopyShell } from "./PolyCopyShell";
import {
  ApiError,
  ModalShell,
  WhaleEntry,
  WhaleFollowPreview,
  WhaleMarket,
  WhaleMarketList,
  WhaleMarketSide,
  WhaleOrder,
  WhaleSettings,
  WhaleTag,
  WhaleTrade,
  formatBeijing,
  formatCompactUsdc,
  formatPercent,
  formatPrice,
  formatSigned,
  formatUsdc,
  marketUrl,
  numeric,
  profileUrl,
  remainingLabel,
  shortAddress,
  whaleApi,
} from "./WhaleShared";

type SortMode = "default" | "ending_soon" | "max_entry" | "least_delta";

function queryString(values: Record<string, string | number | boolean | null>) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== null && value !== "") params.set(key, String(value));
  }
  return params.toString();
}

function entryKey(market: WhaleMarket, side: WhaleMarketSide, entry: WhaleEntry) {
  return `${market.condition_id}:${side.asset_id}:${entry.entry_id}`;
}

function findDetailEntry(
  detail: WhaleMarket | undefined,
  side: WhaleMarketSide,
  entry: WhaleEntry,
) {
  return detail?.sides
    .find((candidate) => candidate.asset_id === side.asset_id)
    ?.entries.find((candidate) => candidate.entry_id === entry.entry_id);
}

export default function WhaleDiscoveryWorkspace() {
  const [settings, setSettings] = useState<WhaleSettings | null>(null);
  const [tags, setTags] = useState<WhaleTag[]>([]);
  const [markets, setMarkets] = useState<WhaleMarketList | null>(null);
  const [tagSlug, setTagSlug] = useState("");
  const [amountThreshold, setAmountThreshold] = useState("10000");
  const [remainingMinutes, setRemainingMinutes] = useState("30");
  const [maxDelta, setMaxDelta] = useState("");
  const [sort, setSort] = useState<SortMode>("default");
  const [includeExited, setIncludeExited] = useState(false);
  const [includeHedged, setIncludeHedged] = useState(true);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [savingThreshold, setSavingThreshold] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [details, setDetails] = useState<Record<string, WhaleMarket>>({});
  const [detailLoading, setDetailLoading] = useState<Set<string>>(new Set());
  const [followTarget, setFollowTarget] = useState<{
    market: WhaleMarket;
    side: WhaleMarketSide;
    entry: WhaleEntry;
  } | null>(null);

  const loadSettingsAndTags = useCallback(async () => {
    try {
      const [nextSettings, nextTags] = await Promise.all([
        whaleApi<WhaleSettings>("/api/whales/settings"),
        whaleApi<WhaleTag[]>("/api/whales/tags"),
      ]);
      setSettings(nextSettings);
      setTags(nextTags);
      setAmountThreshold(String(nextSettings.cumulative_threshold_usdc));
      setRemainingMinutes(String(nextSettings.min_remaining_minutes));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "巨鲸配置加载失败");
    }
  }, []);

  const loadMarkets = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const query = queryString({
        tag_slug: tagSlug,
        min_amount_usdc: amountThreshold,
        min_remaining_minutes: remainingMinutes,
        max_price_delta_cents: maxDelta,
        include_exited: includeExited,
        include_hedged: includeHedged,
        sort,
        limit: 100,
        offset: 0,
      });
      setMarkets(await whaleApi<WhaleMarketList>(`/api/whales/markets?${query}`));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "巨鲸市场加载失败");
    } finally {
      setLoading(false);
    }
  }, [amountThreshold, includeExited, includeHedged, maxDelta, remainingMinutes, sort, tagSlug]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadSettingsAndTags(), 0);
    return () => window.clearTimeout(timer);
  }, [loadSettingsAndTags]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadMarkets(), 180);
    return () => window.clearTimeout(timer);
  }, [loadMarkets]);

  const scanNow = async () => {
    setRefreshing(true);
    setError(null);
    try {
      await whaleApi<{ status: string }>("/api/whales/scan", { method: "POST" });
      await Promise.all([loadSettingsAndTags(), loadMarkets()]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "手动扫描失败");
    } finally {
      setRefreshing(false);
    }
  };

  const saveThreshold = async () => {
    const value = Number(amountThreshold);
    if (!Number.isFinite(value) || value <= 0) {
      setError("请输入有效的重仓金额阈值");
      return;
    }
    setSavingThreshold(true);
    setError(null);
    try {
      const next = await whaleApi<WhaleSettings>("/api/whales/settings", {
        method: "PUT",
        body: JSON.stringify({ cumulative_threshold_usdc: value }),
      });
      setSettings(next);
      await whaleApi<{ status: string }>("/api/whales/scan", { method: "POST" });
      await loadMarkets();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "阈值保存失败");
    } finally {
      setSavingThreshold(false);
    }
  };

  const toggleEntry = async (
    market: WhaleMarket,
    side: WhaleMarketSide,
    entry: WhaleEntry,
  ) => {
    const key = entryKey(market, side, entry);
    const willOpen = !expanded.has(key);
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    if (!willOpen || details[market.condition_id]) return;
    setDetailLoading((current) => new Set(current).add(market.condition_id));
    try {
      const detail = await whaleApi<WhaleMarket>(
        `/api/whales/markets/${encodeURIComponent(market.condition_id)}`,
      );
      setDetails((current) => ({ ...current, [market.condition_id]: detail }));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "成交明细加载失败");
    } finally {
      setDetailLoading((current) => {
        const next = new Set(current);
        next.delete(market.condition_id);
        return next;
      });
    }
  };

  const visibleEntryCount = useMemo(
    () =>
      markets?.items.reduce(
        (total, market) =>
          total + market.sides.reduce((sideTotal, side) => sideTotal + side.entries.length, 0),
        0,
      ) ?? 0,
    [markets],
  );

  const stale = markets?.stale ?? false;
  const healthTone = settings?.consecutive_failures
    ? settings.consecutive_failures >= 3
      ? "danger"
      : "warning"
    : stale
      ? "warning"
      : "success";

  return (
    <PolyCopyShell
      active="whales"
      title="巨鲸发现"
      subtitle="扫描最近 24 小时公开成交，识别仍可交易市场中的大额方向性投入。"
      actions={
        <>
          <span className={`pcBadge ${healthTone}`}>
            {healthTone === "success"
              ? "扫描正常"
              : healthTone === "danger"
                ? "扫描异常"
                : "数据可能过期"}
          </span>
          <Link className="pcButton ghost" href="/whales/records">
            跟单记录
          </Link>
          <button className="pcButton primary" type="button" onClick={scanNow} disabled={refreshing}>
            <span className={refreshing ? "spinning" : ""}>↻</span>
            {refreshing ? "扫描中" : "立即扫描"}
          </button>
        </>
      }
    >
      {(stale || settings?.last_scan_error) && (
        <div className={`whaleHealthBanner ${settings?.consecutive_failures ? "danger" : ""}`}>
          <span aria-hidden="true">!</span>
          <div>
            <strong>{settings?.consecutive_failures ? "最近扫描未完成" : "数据可能过期"}</strong>
            <p>
              {settings?.last_scan_error || "最后成功扫描已超过预期间隔，列表可能不是最新状态。"}
            </p>
          </div>
          <button type="button" onClick={scanNow} disabled={refreshing}>重试扫描</button>
        </div>
      )}

      <section className="pcPanel whaleFilterPanel" aria-label="巨鲸筛选">
        <div className="whaleCategoryBar" role="group" aria-label="市场分类">
          <button
            type="button"
            className={tagSlug === "" ? "active" : ""}
            onClick={() => setTagSlug("")}
          >
            全部 <span>{markets?.total ?? 0}</span>
          </button>
          {tags.map((tag) => (
            <button
              key={tag.id || tag.slug}
              type="button"
              className={tagSlug === tag.slug ? "active" : ""}
              onClick={() => setTagSlug(tag.slug)}
            >
              {tag.label} <span>{tag.market_count}</span>
            </button>
          ))}
        </div>
        <div className="whaleFilterGrid">
          <label className="whaleFilterField whaleAmountFilter">
            <span>重仓累计阈值</span>
            <div>
              <input
                aria-label="重仓累计阈值"
                type="number"
                min="1"
                step="100"
                value={amountThreshold}
                onChange={(event) => setAmountThreshold(event.target.value)}
              />
              <b>USDC</b>
              <button type="button" onClick={saveThreshold} disabled={savingThreshold}>
                {savingThreshold ? "保存中" : "保存"}
              </button>
            </div>
          </label>
          <label className="whaleFilterField">
            <span>最小剩余时间</span>
            <div>
              <input
                aria-label="最小剩余时间"
                type="number"
                min="0"
                value={remainingMinutes}
                onChange={(event) => setRemainingMinutes(event.target.value)}
              />
              <b>分钟</b>
            </div>
          </label>
          <label className="whaleFilterField">
            <span>价格劣化上限</span>
            <div>
              <input
                aria-label="价格劣化上限"
                type="number"
                min="0"
                step="0.1"
                placeholder="不限"
                value={maxDelta}
                onChange={(event) => setMaxDelta(event.target.value)}
              />
              <b>美分</b>
            </div>
          </label>
          <label className="whaleFilterField">
            <span>默认排序</span>
            <select aria-label="巨鲸市场排序" value={sort} onChange={(event) => setSort(event.target.value as SortMode)}>
              <option value="default">综合参考价值</option>
              <option value="ending_soon">距结束时间</option>
              <option value="max_entry">单钱包最大投入</option>
              <option value="least_delta">价格劣化最小</option>
            </select>
          </label>
          <label className="whaleToggleField">
            <input
              type="checkbox"
              checked={includeExited}
              onChange={(event) => setIncludeExited(event.target.checked)}
            />
            <span>显示已退出</span>
          </label>
          <label className="whaleToggleField">
            <input
              type="checkbox"
              checked={includeHedged}
              onChange={(event) => setIncludeHedged(event.target.checked)}
            />
            <span>显示疑似做市</span>
          </label>
        </div>
        <div className="whaleFilterMeta">
          <span>窗口起点：{formatBeijing(markets?.window_start)}</span>
          <span>发现 {markets?.total ?? 0} 个市场 · {visibleEntryCount} 条巨鲸投入</span>
          <span>最后扫描：{formatBeijing(settings?.last_scan_at, true)}</span>
        </div>
      </section>

      {error && (
        <div className="pcAlert danger whalePageError" role="alert">
          <strong>请求未完成</strong>
          <p>{error}</p>
          <button type="button" onClick={() => void loadMarkets()}>重试</button>
        </div>
      )}

      {loading && !markets ? (
        <div className="pcPanel pcLoading whaleLoading">正在汇总巨鲸成交与市场状态…</div>
      ) : markets?.items.length ? (
        <div className={`whaleMarketList ${loading ? "refreshing" : ""}`} aria-live="polite">
          {markets.items.map((market) => (
            <WhaleMarketCard
              key={market.condition_id}
              market={market}
              detail={details[market.condition_id]}
              detailLoading={detailLoading.has(market.condition_id)}
              expanded={expanded}
              warningThreshold={numeric(settings?.max_price_delta_cents ?? 5)}
              onToggleEntry={toggleEntry}
              onFollow={(side, entry) => setFollowTarget({ market, side, entry })}
            />
          ))}
        </div>
      ) : (
        <section className="pcPanel pcEmptyState whaleEmptyState">
          <div className="pcEmptyIcon">◈</div>
          <h2>当前阈值下没有发现重仓投入</h2>
          <p>可以降低累计金额阈值，或缩短最小剩余时间后重新扫描。</p>
          <button
            className="pcButton primary"
            type="button"
            onClick={() => setAmountThreshold(String(Math.max(1000, Number(amountThreshold || 10000) / 2)))}
          >
            降低阈值
          </button>
        </section>
      )}

      {followTarget && (
        <WhaleFollowModal
          {...followTarget}
          maxAmount={numeric(settings?.max_follow_amount_usdc ?? 200)}
          onClose={() => setFollowTarget(null)}
          onCompleted={() => void loadMarkets()}
        />
      )}
    </PolyCopyShell>
  );
}

function WhaleMarketCard({
  market,
  detail,
  detailLoading,
  expanded,
  warningThreshold,
  onToggleEntry,
  onFollow,
}: {
  market: WhaleMarket;
  detail?: WhaleMarket;
  detailLoading: boolean;
  expanded: Set<string>;
  warningThreshold: number;
  onToggleEntry: (market: WhaleMarket, side: WhaleMarketSide, entry: WhaleEntry) => void;
  onFollow: (side: WhaleMarketSide, entry: WhaleEntry) => void;
}) {
  const endingSoon = market.remaining_seconds !== null && market.remaining_seconds < 3600;
  const leftRatio = market.sides[0]
    ? numeric(market.sides[0].side_total_usdc) / Math.max(1, numeric(market.total_whale_usdc))
    : 0;

  return (
    <article className="pcPanel whaleMarketCard">
      <header className="whaleMarketHeader">
        <div className="whaleMarketHeadline">
          {market.icon_url ? (
            // Gamma supplies remote market artwork whose host is not fixed enough
            // for Next's build-time image allow-list.
            // eslint-disable-next-line @next/next/no-img-element
            <img src={market.icon_url} alt="" className="whaleMarketIcon" />
          ) : (
            <span className="whaleMarketIcon fallback" aria-hidden="true">◈</span>
          )}
          <div>
            <div className="whaleMarketBadges">
              {market.tags.map((tag) => <span key={tag.id || tag.slug}>{tag.label}</span>)}
              {market.both_sides && <span className="duel">双边对赌</span>}
              <span className={endingSoon ? "ending" : ""}>
                {remainingLabel(market.remaining_seconds, market.end_date_is_date_only)}
              </span>
            </div>
            <a
              href={marketUrl(market.event_slug || market.market_slug, market.polymarket_url)}
              target="_blank"
              rel="noreferrer"
            >
              {market.title} <span aria-hidden="true">↗</span>
            </a>
            <p>
              流动性 {formatCompactUsdc(market.liquidity)} · 24h 成交 {formatCompactUsdc(market.volume_24h)}
              {market.end_date && <> · 预计结束 {formatBeijing(market.end_date)}</>}
            </p>
          </div>
        </div>
        <div className="whaleMarketTotals">
          <span>巨鲸累计投入</span>
          <strong>{formatCompactUsdc(market.total_whale_usdc)}</strong>
          <small>{market.whale_wallet_count} 个钱包</small>
        </div>
      </header>

      {market.both_sides && (
        <div className="whaleDuelNotice">
          <div>
            <strong>双边资金正在对赌</strong>
            <span>两侧均有大额买入代表分歧，而非共识；参考价值相对更低。</span>
          </div>
          <div className="whaleImbalance" aria-label="两侧金额占比">
            <span style={{ width: `${Math.max(4, Math.min(96, leftRatio * 100))}%` }} />
          </div>
          <b>{formatPercent(numeric(market.side_imbalance_ratio) * 100)} 主导侧占比</b>
        </div>
      )}

      <div className={`whaleSides ${market.sides.length > 1 ? "two" : "one"}`}>
        {market.sides.map((side) => (
          <section className="whaleSide" key={side.asset_id} aria-label={`${side.outcome} 方向`}>
            <header>
              <div>
                <span className={`whaleOutcomeMark ${side.outcome_index === 0 ? "positive" : "negative"}`}>
                  {side.outcome}
                </span>
                <strong>{formatPrice(side.current_price)}</strong>
                <small>当前价</small>
              </div>
              <div>
                <span>{formatCompactUsdc(side.side_total_usdc)}</span>
                <small>{side.side_wallet_count} 个钱包</small>
              </div>
            </header>
            <div className="whaleEntryHeader" aria-hidden="true">
              <span>钱包 / 注册</span><span>巨鲸买入</span><span>现价 / 劣化</span><span>累计投入</span><span>最近买入</span><span>操作</span>
            </div>
            <div className="whaleEntries">
              {side.entries.map((entry) => {
                const key = entryKey(market, side, entry);
                const isOpen = expanded.has(key);
                const detailed = findDetailEntry(detail, side, entry);
                const trades = detailed?.trades ?? entry.trades ?? [];
                return (
                  <div className={`whaleEntry ${entry.hedged ? "hedged" : ""}`} key={entry.entry_id}>
                    <div className="whaleEntryRow">
                      <a
                        className="whaleWallet"
                        href={profileUrl(entry.proxy_wallet, entry.profile_url)}
                        target="_blank"
                        rel="noreferrer"
                      >
                        <strong>{entry.display_name || shortAddress(entry.proxy_wallet)}</strong>
                        <span>{shortAddress(entry.proxy_wallet)}</span>
                        <small title="Polymarket 账号创建时间，不代表链上首次交易时间">
                          注册 {entry.wallet_age_days === null ? "未知" : `${entry.wallet_age_days} 天`}
                          {entry.verified_badge && " · 已认证"}
                        </small>
                      </a>
                      <div className="whaleEntryMetric">
                        <strong>{formatPrice(entry.avg_buy_price)}</strong>
                        <small>均价 · {entry.trade_count} 笔</small>
                      </div>
                      <div className="whaleEntryMetric">
                        <strong>{formatPrice(side.current_price)}</strong>
                        <small className={numeric(entry.price_delta_cents) > warningThreshold ? "warning" : numeric(entry.price_delta_cents) < 0 ? "profit" : ""}>
                          {formatSigned(entry.price_delta_cents, "¢")} · {formatPercent(entry.price_delta_percent)}
                        </small>
                      </div>
                      <div className="whaleEntryMetric">
                        <strong>{formatCompactUsdc(entry.gross_buy_usdc)}</strong>
                        <small>单笔最高 {formatCompactUsdc(entry.max_single_usdc)}</small>
                      </div>
                      <div className="whaleEntryMetric whaleEntryTime">
                        <strong>{formatBeijing(entry.last_buy_at)}</strong>
                        <small>
                          {entry.status === "reduced"
                            ? `已减仓 ${Math.max(0, 100 - numeric(entry.net_ratio)).toFixed(0)}%`
                            : entry.status === "exited"
                              ? "已退出"
                              : "仍在持有"}
                        </small>
                      </div>
                      <div className="whaleEntryActions">
                        {entry.hedged && <span className="pcBadge warning">疑似做市/对冲</span>}
                        <button className="pcButton primary" type="button" onClick={() => onFollow(side, entry)}>
                          跟随买入
                        </button>
                        <button
                          className="whaleDetailToggle"
                          type="button"
                          aria-expanded={isOpen}
                          onClick={() => onToggleEntry(market, side, entry)}
                        >
                          {isOpen ? "收起明细" : `查看 ${entry.trade_count} 笔成交`}
                        </button>
                      </div>
                    </div>
                    {isOpen && (
                      <WhaleTradeDetail
                        loading={detailLoading && !detailed}
                        trades={trades}
                        expected={entry.trade_count}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </article>
  );
}

function WhaleTradeDetail({ loading, trades, expected }: { loading: boolean; trades: WhaleTrade[]; expected: number }) {
  if (loading) return <div className="whaleTradeLoading">正在读取逐笔成交…</div>;
  if (!trades.length) return <div className="whaleTradeLoading">暂无可展示的逐笔成交明细（聚合笔数 {expected}）。</div>;
  return (
    <div className="whaleTradeDetail">
      <div className="whaleTradeHeader"><span>成交时间</span><span>方向</span><span>价格</span><span>份额</span><span>金额</span></div>
      {trades.map((trade, index) => (
        <div className="whaleTradeRow" key={trade.id ?? `${trade.timestamp}-${index}`}>
          <time>{formatBeijing(trade.timestamp, true)}</time>
          <span className={`pcBadge ${trade.side.toUpperCase() === "BUY" ? "success" : "sell"}`}>{trade.side}</span>
          <strong>{formatPrice(trade.price)}</strong>
          <span>{numeric(trade.size).toFixed(2)}</span>
          <strong>{formatUsdc(trade.amount)}</strong>
        </div>
      ))}
    </div>
  );
}

function WhaleFollowModal({
  market,
  side,
  entry,
  maxAmount,
  onClose,
  onCompleted,
}: {
  market: WhaleMarket;
  side: WhaleMarketSide;
  entry: WhaleEntry;
  maxAmount: number;
  onClose: () => void;
  onCompleted: () => void;
}) {
  const initialAmount = Math.min(20, maxAmount);
  const [amount, setAmount] = useState(String(initialAmount));
  const [preview, setPreview] = useState<WhaleFollowPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [order, setOrder] = useState<WhaleOrder | null>(null);

  const requestPreview = useCallback(async () => {
    const amountValue = Number(amount);
    if (!Number.isFinite(amountValue) || amountValue <= 0) {
      setPreview(null);
      setError("请输入大于 0 的跟单金额");
      return;
    }
    if (amountValue > maxAmount) {
      setPreview(null);
      setError(`单笔跟单金额不能超过 ${formatUsdc(maxAmount)}`);
      return;
    }
    setPreviewing(true);
    setError(null);
    try {
      setPreview(
        await whaleApi<WhaleFollowPreview>("/api/whales/follow/preview", {
          method: "POST",
          body: JSON.stringify({
            asset_id: side.asset_id,
            amount_usdc: amountValue,
            entry_id: entry.entry_id,
          }),
        }),
      );
    } catch (requestError) {
      setPreview(null);
      setError(requestError instanceof Error ? requestError.message : "跟单预览失败");
    } finally {
      setPreviewing(false);
    }
  }, [amount, entry.entry_id, maxAmount, side.asset_id]);

  useEffect(() => {
    const timer = window.setTimeout(() => void requestPreview(), 400);
    return () => window.clearTimeout(timer);
  }, [requestPreview]);

  const execute = async () => {
    if (!preview || confirmation !== "确认真实买入") return;
    setExecuting(true);
    setError(null);
    try {
      const result = await whaleApi<WhaleOrder>("/api/whales/follow/execute", {
        method: "POST",
        body: JSON.stringify({
          confirmation_id: preview.confirmation_id,
          confirmation_text: confirmation,
        }),
      });
      setOrder(result);
      onCompleted();
    } catch (requestError) {
      setConfirmation("");
      if (requestError instanceof ApiError && requestError.status === 409) {
        setError(`${requestError.message}，正在重新获取盘口。`);
        setPreview(null);
        await requestPreview();
      } else {
        setError(requestError instanceof Error ? requestError.message : "真实买入未完成");
      }
    } finally {
      setExecuting(false);
    }
  };

  const averageFill = order && numeric(order.filled_size) > 0
    ? numeric(order.filled_usdc) / numeric(order.filled_size)
    : null;

  return (
    <ModalShell
      title="跟随巨鲸买入"
      eyebrow="MANUAL WHALE FOLLOW"
      onClose={onClose}
      className="whaleFollowModal"
      footer={
        order ? (
          <button className="pcButton primary" type="button" onClick={onClose}>完成</button>
        ) : (
          <>
            <button className="pcButton ghost" type="button" onClick={onClose}>取消</button>
            <button
              className="pcButton primary"
              type="button"
              onClick={execute}
              disabled={!preview || previewing || executing || confirmation !== "确认真实买入"}
            >
              {executing ? "提交中" : "确认买入"}
            </button>
          </>
        )
      }
    >
      <div className="whaleFollowMarket">
        <span className={`whaleOutcomeMark ${side.outcome_index === 0 ? "positive" : "negative"}`}>{side.outcome}</span>
        <div>
          <strong>{market.title}</strong>
          <span>巨鲸均价 {formatPrice(entry.avg_buy_price)} · 当前价 {formatPrice(side.current_price)}</span>
        </div>
      </div>

      {order ? (
        <div className="whaleExecutionResult">
          <span className={`pcBadge ${numeric(order.filled_size) > 0 ? "success" : "warning"}`}>{order.status}</span>
          <h3>{numeric(order.filled_size) > 0 ? "买入成交已记录" : "订单已提交"}</h3>
          <dl>
            <div><dt>成交份额</dt><dd>{numeric(order.filled_size).toFixed(4)}</dd></div>
            <div><dt>成交均价</dt><dd>{formatPrice(averageFill)}</dd></div>
            <div><dt>成交金额</dt><dd>{formatUsdc(order.filled_usdc)}</dd></div>
            <div><dt>手续费</dt><dd>{formatUsdc(order.fee_usdc)}</dd></div>
          </dl>
          {order.reason && <p>{order.reason}</p>}
          <Link href="/whales/records" className="pcTextLink">查看完整跟单记录 →</Link>
        </div>
      ) : (
        <>
          <label className="whaleAmountInput">
            <span>跟单金额 <small>单笔上限 {formatUsdc(maxAmount)}</small></span>
            <div><input aria-label="跟单金额" type="number" min="0" max={maxAmount} step="1" value={amount} onChange={(event) => setAmount(event.target.value)} /><b>USDC</b></div>
          </label>
          <div className="whaleQuickAmounts" aria-label="快捷金额">
            {[20, 50, 100].filter((value) => value <= maxAmount).map((value) => (
              <button type="button" key={value} onClick={() => setAmount(String(value))}>{value} USDC</button>
            ))}
          </div>

          {previewing && <div className="whalePreviewLoading">正在读取实时盘口并计算成本…</div>}
          {error && <div className="pcFormError" role="alert">{error}</div>}
          {preview && !previewing && (
            <div className="whaleFollowPreview">
              <div className="whalePreviewHero">
                <span>该方向结算为 1 时的净盈利比</span>
                <strong className={numeric(preview.profit_ratio_percent) >= 0 ? "profit" : "loss"}>{formatPercent(preview.profit_ratio_percent)}</strong>
                <small>买入价上限 {formatPrice(preview.worst_price)} · 可用余额 {formatUsdc(preview.available_balance_usdc)}</small>
              </div>
              <dl className="whalePreviewGrid">
                <div><dt>预计份额</dt><dd>{numeric(preview.estimated_shares).toFixed(4)}</dd></div>
                <div><dt>预估手续费</dt><dd>{formatUsdc(preview.estimated_fee_usdc)}</dd></div>
                <div><dt>总成本</dt><dd>{formatUsdc(preview.total_cost_usdc)}</dd></div>
                <div><dt>结算可得</dt><dd>{formatUsdc(preview.estimated_shares)}</dd></div>
                <div><dt>最大亏损</dt><dd className="loss">{formatUsdc(preview.max_loss_usdc)}</dd></div>
                <div><dt>最小下单额</dt><dd>{formatUsdc(preview.minimum_order_usdc)}</dd></div>
              </dl>
              {preview.whale_avg_price !== null && (
                <p className="whalePriceComparison">
                  巨鲸买入价 {formatPrice(preview.whale_avg_price)}，其结算盈利比 {formatPercent(preview.whale_profit_ratio_percent)}；
                  你与巨鲸相差 {formatSigned(preview.profit_ratio_gap_percent, " 个百分点")}。
                </p>
              )}
              {preview.price_delta_warning && (
                <div className="whalePreviewWarning">
                  当前价已比巨鲸买入价高 {formatSigned(preview.price_delta_cents, " 美分")}，跟进成本明显上升。
                </div>
              )}
              {preview.reserve_warning && (
                <div className="whaleReserveWarning">本次买入会使执行钱包余额低于现金保留额，但不会阻断手动操作。</div>
              )}
              <label className="whaleConfirmation">
                <span>输入固定文案后才能执行真实订单</span>
                <input
                  aria-label="真实买入确认文案"
                  value={confirmation}
                  onChange={(event) => setConfirmation(event.target.value)}
                  placeholder="确认真实买入"
                  autoComplete="off"
                />
              </label>
            </div>
          )}
        </>
      )}
    </ModalShell>
  );
}
