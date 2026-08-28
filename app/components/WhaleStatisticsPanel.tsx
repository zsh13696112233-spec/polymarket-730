"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Numeric,
  WhaleRule,
  WhaleStatistics,
  WhaleStatisticsAmountBand,
  WhaleStatisticsCategory,
  WhaleStatisticsMetrics,
  WhaleStatisticsRange,
  WhaleStatisticsResult,
  WhaleStatisticsRule,
  WhaleStatisticsSignal,
  WhaleStatisticsSignalList,
  WhaleStatisticsSort,
  WhaleStatisticsSubcategory,
  formatBeijing,
  formatCompactUsdc,
  formatPrice,
  formatSigned,
  numeric,
  pnlClass,
  shortAddress,
  whaleApi,
} from "./WhaleShared";

const RANGE_LABELS: Record<WhaleStatisticsRange, string> = {
  all: "全部",
  "7d": "近 7 天",
  "30d": "近 30 天",
  "90d": "近 90 天",
};

const RULE_LABELS: Record<WhaleRule, string> = {
  new_account: "新号大额",
  large_amount: "全量超大额",
};

const CATEGORY_LABELS: Record<WhaleStatisticsCategory, string> = {
  all: "全部分类",
  esports: "电竞",
  sports: "传统体育",
  politics: "政治",
  crypto: "加密",
  science_tech: "科学与科技",
  entertainment: "娱乐",
  other: "其他",
};

const CATEGORY_KEYS = Object.keys(CATEGORY_LABELS) as WhaleStatisticsCategory[];

function percent(value: Numeric | null | undefined, signed = false): string {
  if (value === null || value === undefined) return "样本不足";
  const amount = numeric(value);
  return `${signed && amount > 0 ? "+" : ""}${amount.toFixed(1)}%`;
}

function fullBeijingDate(value: string | null): string {
  if (!value) return "尚无已记录信号";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function metricTone(value: Numeric | null | undefined): string {
  return pnlClass(value);
}

function sampleLabel(metrics: WhaleStatisticsMetrics): string | null {
  return metrics.effective_sample_count > 0 && metrics.effective_sample_count < 10
    ? "样本偏少"
    : null;
}

function HitRate({ metrics }: { metrics: WhaleStatisticsMetrics }) {
  return (
    <>
      <strong>{percent(metrics.hit_rate_percent)}</strong>
      <small>
        {metrics.hit_count} 胜 / {metrics.miss_count} 负
        {metrics.special_count ? ` · ${metrics.special_count} 特殊` : ""}
      </small>
    </>
  );
}

function CohortCard({
  title,
  eyebrow,
  metrics,
}: {
  title: string;
  eyebrow: string;
  metrics: WhaleStatisticsMetrics;
}) {
  return (
    <article className="whaleStatsCohortCard">
      <header>
        <div><span>{eyebrow}</span><h3>{title}</h3></div>
        <div className="whaleStatsCohortRate"><HitRate metrics={metrics} /></div>
      </header>
      <dl>
        <div><dt>有效样本</dt><dd>{metrics.effective_sample_count}</dd></div>
        <div><dt>当前待结算</dt><dd>{metrics.pending_count}</dd></div>
        <div><dt>理论净盈亏</dt><dd className={metricTone(metrics.theoretical_pnl_usdc)}>{formatSigned(metrics.theoretical_pnl_usdc, " USDC")}</dd></div>
        <div><dt>资金加权 ROI</dt><dd className={metricTone(metrics.theoretical_roi_percent)}>{percent(metrics.theoretical_roi_percent, true)}</dd></div>
        <div><dt>加权买入价</dt><dd>{formatPrice(metrics.weighted_avg_buy_price)}</dd></div>
        <div><dt>命中优势</dt><dd className={metricTone(metrics.edge_percentage_points)}>{metrics.edge_percentage_points == null ? "样本不足" : `${formatSigned(metrics.edge_percentage_points)} 个点`}</dd></div>
      </dl>
    </article>
  );
}

function BreakdownButton({
  label,
  metrics,
  active,
  onClick,
}: {
  label: string;
  metrics: WhaleStatisticsMetrics;
  active: boolean;
  onClick: () => void;
}) {
  const warning = sampleLabel(metrics);
  return (
    <button
      type="button"
      className={`whaleStatsBreakdownCard ${active ? "active" : ""}`}
      aria-pressed={active}
      onClick={onClick}
    >
      <span className="whaleStatsBreakdownTitle">
        <strong>{label}</strong>
        {warning && <em>{warning}</em>}
      </span>
      <b>{percent(metrics.hit_rate_percent)}</b>
      <small>{metrics.hit_count} 胜 / {metrics.miss_count} 负{metrics.special_count ? ` · ${metrics.special_count} 特殊` : ""}</small>
      <span className={metricTone(metrics.theoretical_pnl_usdc)}>{formatSigned(metrics.theoretical_pnl_usdc, " USDC")}</span>
      <small className={metricTone(metrics.theoretical_roi_percent)}>ROI {percent(metrics.theoretical_roi_percent, true)} · {metrics.effective_sample_count} 样本</small>
    </button>
  );
}

function RuleBadges({ rules }: { rules: WhaleRule[] }) {
  return (
    <span className="whaleRuleBadges">
      {rules.map((rule) => (
        <span className={`pcBadge ${rule === "new_account" ? "warning" : "danger"}`} key={rule}>
          {RULE_LABELS[rule]}
        </span>
      ))}
    </span>
  );
}

function resultLabel(result: WhaleStatisticsSignal["result"]): string {
  if (result === "hit") return "命中";
  if (result === "miss") return "未命中";
  return "特殊结算";
}

function resultTone(result: WhaleStatisticsSignal["result"]): string {
  if (result === "hit") return "success";
  if (result === "miss") return "danger";
  return "warning";
}

export function WhaleStatisticsPanel({ refreshToken = 0 }: { refreshToken?: number }) {
  const [range, setRange] = useState<WhaleStatisticsRange>("all");
  const [statistics, setStatistics] = useState<WhaleStatistics | null>(null);
  const [signals, setSignals] = useState<WhaleStatisticsSignalList | null>(null);
  const [rule, setRule] = useState<WhaleStatisticsRule>("all");
  const [result, setResult] = useState<WhaleStatisticsResult>("all");
  const [amountBand, setAmountBand] = useState<WhaleStatisticsAmountBand>("all");
  const [category, setCategory] = useState<WhaleStatisticsCategory>("all");
  const [subcategory, setSubcategory] = useState<WhaleStatisticsSubcategory>("all");
  const [sort, setSort] = useState<WhaleStatisticsSort>("settled_desc");
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [signalLoading, setSignalLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const limit = 50;

  const loadStatistics = useCallback(async () => {
    setLoading(true);
    const params = new URLSearchParams({
      range,
      category,
      subcategory,
      refresh: String(refreshToken),
    });
    try {
      setStatistics(await whaleApi<WhaleStatistics>(`/api/whales/statistics?${params.toString()}`));
      setError(null);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "巨鲸统计加载失败");
    } finally {
      setLoading(false);
    }
  }, [category, range, refreshToken, subcategory]);

  const loadSignals = useCallback(async () => {
    setSignalLoading(true);
    const params = new URLSearchParams({
      range,
      rule,
      result,
      amount_band: amountBand,
      category,
      subcategory,
      sort,
      limit: String(limit),
      offset: String(offset),
      refresh: String(refreshToken),
    });
    try {
      setSignals(await whaleApi<WhaleStatisticsSignalList>(`/api/whales/statistics/signals?${params.toString()}`));
      setError(null);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "巨鲸统计明细加载失败");
    } finally {
      setSignalLoading(false);
    }
  }, [amountBand, category, offset, range, refreshToken, result, rule, sort, subcategory]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadStatistics(), 0);
    return () => window.clearTimeout(timer);
  }, [loadStatistics]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadSignals(), 0);
    return () => window.clearTimeout(timer);
  }, [loadSignals]);

  const changeRange = (next: WhaleStatisticsRange) => {
    setRange(next);
    setOffset(0);
  };
  const changeCategory = (next: WhaleStatisticsCategory) => {
    setCategory(next);
    setSubcategory("all");
    setOffset(0);
  };
  const setFilter = <T,>(setter: (value: T) => void, value: T) => {
    setter(value);
    setOffset(0);
  };
  const overall = statistics?.overall;

  return (
    <div className="whaleStatisticsWorkspace" aria-label="巨鲸命中率统计">
      <section className="pcPanel whaleStatsToolbar">
        <div>
          <span className="pcEyebrow">SIGNAL PERFORMANCE</span>
          <h2>链上信号表现</h2>
          <p>统计自 {fullBeijingDate(statistics?.coverage_start ?? null)} · 最后计算 {formatBeijing(statistics?.generated_at, true)}</p>
        </div>
        <div className="whaleStatsRange" aria-label="统计时间范围">
          {(Object.keys(RANGE_LABELS) as WhaleStatisticsRange[]).map((item) => (
            <button type="button" className={range === item ? "active" : ""} aria-pressed={range === item} onClick={() => changeRange(item)} key={item}>
              {RANGE_LABELS[item]}
            </button>
          ))}
        </div>
      </section>

      <section className="pcPanel whaleStatsCategoryControls" aria-label="统计分类筛选">
        <div>
          <span className="pcEyebrow">MARKET CATEGORY</span>
          <strong>市场分类</strong>
        </div>
        <div className="whaleStatsCategoryButtons">
          {CATEGORY_KEYS.map((item) => (
            <button
              type="button"
              className={category === item ? "active" : ""}
              aria-pressed={category === item}
              onClick={() => changeCategory(item)}
              key={item}
            >
              {CATEGORY_LABELS[item]}
            </button>
          ))}
        </div>
        {(category === "esports" || category === "sports") && (
          <label>
            <span>子分类</span>
            <select
              aria-label="统计子分类筛选"
              value={subcategory}
              onChange={(event) => setFilter(setSubcategory, event.target.value)}
            >
              <option value="all">全部{CATEGORY_LABELS[category]}</option>
              {(statistics?.subcategory_breakdown ?? []).map((slice) => (
                <option value={slice.key} key={slice.key}>{slice.label}</option>
              ))}
            </select>
          </label>
        )}
      </section>

      {error && <div className="pcAlert danger whalePageError" role="alert"><strong>统计请求未完成</strong><p>{error}</p><button type="button" onClick={() => void Promise.all([loadStatistics(), loadSignals()])}>重试</button></div>}

      {loading && !statistics ? (
        <div className="pcPanel pcLoading whaleLoading">正在计算巨鲸命中率…</div>
      ) : overall ? (
        <>
          <section className="whaleStatsHero" aria-label="整体统计">
            <article className="whaleStatsHeroCard primary"><span>整体命中率</span><HitRate metrics={overall} /><em>{overall.effective_sample_count} 个有效样本</em></article>
            <article className="whaleStatsHeroCard"><span>理论净盈亏</span><strong className={metricTone(overall.theoretical_pnl_usdc)}>{formatSigned(overall.theoretical_pnl_usdc, " USDC")}</strong><small className={metricTone(overall.theoretical_roi_percent)}>资金加权 ROI {percent(overall.theoretical_roi_percent, true)}</small><em>持有至结算，未计手续费</em></article>
            <article className="whaleStatsHeroCard"><span>当前待结算</span><strong>{overall.pending_count}</strong><small>不受结算时间筛选影响</small><em>待市场给出最终结果</em></article>
            <article className="whaleStatsHeroCard"><span>已结算覆盖</span><strong>{overall.wallet_count} 钱包</strong><small>{overall.market_count} 个市场 · {overall.settled_count} 条信号</small><em>理论投入 {formatCompactUsdc(overall.theoretical_cost_usdc)}</em></article>
          </section>

          <section className="whaleStatsCohorts" aria-label="规则表现对比">
            <CohortCard title="新号大额" eyebrow="NEW ACCOUNT" metrics={statistics.new_account} />
            <CohortCard title="全量超大额" eyebrow="LARGE AMOUNT" metrics={statistics.large_amount} />
            <CohortCard title="双重命中" eyebrow="DUAL MATCH" metrics={statistics.dual_match} />
          </section>

          <section className="pcPanel whaleStatsBreakdownPanel" aria-label="分类表现">
            <header className="pcPanelHeader">
              <div>
                <span className="pcEyebrow">CATEGORY PERFORMANCE</span>
                <h2>分类表现</h2>
                <p>主分类互斥，电竞不会重复计入传统体育；点击分类可查看完整下钻统计。</p>
              </div>
            </header>
            <div className="whaleStatsBreakdownGrid">
              {(statistics.category_breakdown ?? []).map((slice) => (
                <BreakdownButton
                  key={slice.key}
                  label={slice.label}
                  metrics={slice.metrics}
                  active={category === slice.key}
                  onClick={() => changeCategory(slice.key as WhaleStatisticsCategory)}
                />
              ))}
            </div>
            {(statistics.subcategory_breakdown ?? []).length > 0 && (
              <div className="whaleStatsSubcategoryBlock">
                <header>
                  <strong>{CATEGORY_LABELS[category]}细分</strong>
                  <span>按游戏或运动项目继续下钻</span>
                </header>
                <div className="whaleStatsBreakdownGrid compact">
                  {(statistics.subcategory_breakdown ?? []).map((slice) => (
                    <BreakdownButton
                      key={slice.key}
                      label={slice.label}
                      metrics={slice.metrics}
                      active={subcategory === slice.key}
                      onClick={() => setFilter(setSubcategory, slice.key)}
                    />
                  ))}
                </div>
              </div>
            )}
          </section>

          <section className="whaleStatsAnalysisGrid">
            <article className="pcPanel whaleStatsTrendPanel">
              <header className="pcPanelHeader"><div><span className="pcEyebrow">TREND</span><h2>命中趋势</h2><p>黄色为实际命中率，竖线为资金加权盈亏平衡率。</p></div></header>
              {statistics.trend.length ? <div className="whaleStatsTrendList">{statistics.trend.map((slice) => {
                const rate = Math.max(0, Math.min(100, numeric(slice.metrics.hit_rate_percent)));
                const breakEven = Math.max(0, Math.min(100, numeric(slice.metrics.break_even_rate_percent)));
                return <div className="whaleStatsTrendRow" key={slice.key}><span>{slice.label}</span><div className="whaleStatsTrendBar"><b style={{ width: `${rate}%` }} /><i style={{ left: `${breakEven}%` }} /></div><strong>{percent(slice.metrics.hit_rate_percent)}</strong><small>{slice.metrics.effective_sample_count} 样本</small></div>;
              })}</div> : <div className="whaleHistoryEmpty">所选范围内暂无已结算信号。</div>}
            </article>
            <article className="pcPanel whaleStatsBandsPanel">
              <header className="pcPanelHeader"><div><span className="pcEyebrow">AMOUNT TIERS</span><h2>金额分层</h2><p>按触发窗口累计买入金额比较信号质量。</p></div></header>
              <div className="whaleStatsBandGrid">{statistics.amount_bands.map((slice) => <button type="button" key={slice.key} onClick={() => setFilter(setAmountBand, slice.key as WhaleStatisticsAmountBand)}><span>{slice.label} USDC</span><strong>{percent(slice.metrics.hit_rate_percent)}</strong><small>{slice.metrics.hit_count} 胜 / {slice.metrics.miss_count} 负 · ROI {percent(slice.metrics.theoretical_roi_percent, true)}</small></button>)}</div>
            </article>
          </section>
        </>
      ) : null}

      <section className="pcPanel whaleStatsSignalsPanel" aria-label="已结算信号明细">
        <header className="pcPanelHeader"><div><span className="pcEyebrow">SETTLED SIGNALS</span><h2>已结算信号明细</h2><p>命中率只计算结算价为 1 或 0 的方向；特殊派彩单独列出。</p></div><strong>{signals?.total ?? 0} 条</strong></header>
        <div className="whaleStatsFilters">
          <label><span>规则</span><select aria-label="统计规则筛选" value={rule} onChange={(event) => setFilter(setRule, event.target.value as WhaleStatisticsRule)}><option value="all">全部规则</option><option value="new_account">新号大额</option><option value="large_amount">全量超大额</option><option value="both">双重命中</option></select></label>
          <label><span>结果</span><select aria-label="统计结果筛选" value={result} onChange={(event) => setFilter(setResult, event.target.value as WhaleStatisticsResult)}><option value="all">全部结果</option><option value="hit">命中</option><option value="miss">未命中</option><option value="special">特殊结算</option></select></label>
          <label><span>金额</span><select aria-label="统计金额筛选" value={amountBand} onChange={(event) => setFilter(setAmountBand, event.target.value as WhaleStatisticsAmountBand)}><option value="all">全部金额</option><option value="lt_100k">小于 10万</option><option value="100k_500k">10万–50万</option><option value="500k_1m">50万–100万</option><option value="gte_1m">100万以上</option></select></label>
          <label><span>排序</span><select aria-label="统计明细排序" value={sort} onChange={(event) => setFilter(setSort, event.target.value as WhaleStatisticsSort)}><option value="settled_desc">最近结算</option><option value="amount_desc">买入金额最高</option><option value="pnl_desc">理论盈利最高</option><option value="pnl_asc">理论亏损最高</option></select></label>
        </div>
        {signalLoading && !signals ? <div className="pcLoading whaleLoading">正在读取结算明细…</div> : signals?.items.length ? <div className={`whaleStatsTableWrap ${signalLoading ? "refreshing" : ""}`}><table className="whaleStatsTable"><thead><tr><th>结果 / 规则</th><th>钱包</th><th>市场 / 方向</th><th>买入金额 / 均价</th><th>理论盈亏 / ROI</th><th>触发 / 结算</th></tr></thead><tbody>{signals.items.map((signal) => <tr key={signal.entry_id}><td><span className={`pcBadge ${resultTone(signal.result)}`}>{resultLabel(signal.result)}</span><RuleBadges rules={signal.matched_rules} /></td><td><a href={signal.profile_url} target="_blank" rel="noreferrer"><strong>{signal.display_name || shortAddress(signal.proxy_wallet)} ↗</strong><small>{shortAddress(signal.proxy_wallet)}</small><small>触发时账号 {signal.wallet_age_days_at_trigger == null ? "年龄未知" : `${signal.wallet_age_days_at_trigger} 天`}</small></a></td><td>{signal.category_label && <span className="whaleStatsSignalCategory"><b>{signal.category_label}</b>{signal.subcategory_label && <em>{signal.subcategory_label}</em>}</span>}<a href={signal.polymarket_url} target="_blank" rel="noreferrer">{signal.title} ↗</a><small>{signal.outcome} · 结算价 {formatPrice(signal.settlement_price)}</small></td><td className="numeric"><strong>{formatCompactUsdc(signal.gross_buy_usdc)}</strong><small>均价 {formatPrice(signal.avg_buy_price)}</small></td><td className={`numeric ${metricTone(signal.theoretical_pnl_usdc)}`}><strong>{formatSigned(signal.theoretical_pnl_usdc, " USDC")}</strong><small>{percent(signal.theoretical_roi_percent, true)}</small></td><td><strong>触发 {formatBeijing(signal.first_triggered_at)}</strong><small>结算 {formatBeijing(signal.settled_at)}</small></td></tr>)}</tbody></table></div> : <div className="whaleHistoryEmpty">当前筛选下暂无已结算信号。</div>}
        <footer className="whaleStatsPagination"><span>第 {signals?.total ? Math.floor(offset / limit) + 1 : 0} 页</span><div><button type="button" disabled={offset === 0 || signalLoading} onClick={() => setOffset(Math.max(0, offset - limit))}>上一页</button><button type="button" disabled={signalLoading || offset + limit >= (signals?.total ?? 0)} onClick={() => setOffset(offset + limit)}>下一页</button></div></footer>
      </section>
    </div>
  );
}
