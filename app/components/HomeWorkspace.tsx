"use client";

import Link from "next/link";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type ActiveDotProps,
  type DotItemDotProps,
  type TooltipContentProps,
} from "recharts";
import { PolyCopyShell } from "./PolyCopyShell";
import { WhaleRequestMonitorPanel } from "./WhaleRequestMonitorPanel";
import { useVisibleAutoRefresh } from "./useVisibleAutoRefresh";
import {
  Numeric,
  WhaleRule,
  formatBeijing,
  formatCompactSignedUsdc,
  formatCompactUsdc,
  formatPercent,
  formatUsdc,
  marketUrl,
  numeric,
  pnlClass,
  whaleApi,
} from "./WhaleShared";

type HomeSystemRule = {
  rule: WhaleRule;
  enabled: boolean;
  auto_follow_enabled: boolean;
  active_wallet_count: number;
};

type HomeDaily = {
  date: string;
  buy_amount_usdc: Numeric;
  buy_count: number;
  realized_pnl_usdc: Numeric;
  realized_cost_usdc: Numeric;
  realized_roi_percent: Numeric | null;
  win_count: number;
  loss_count: number;
  flat_count: number;
};

type HomeOverview = {
  as_of: string;
  timezone: "Asia/Shanghai";
  range_start: string;
  range_end: string;
  system: {
    status: "healthy" | "error" | "disabled";
    enabled: boolean;
    last_scan_at: string | null;
    last_scan_error: string | null;
    consecutive_failures: number;
    scan_interval_seconds: number;
    rules: HomeSystemRule[];
  };
  today: HomeDaily & {
    unrealized_pnl_usdc: Numeric | null;
    win_rate_percent: Numeric | null;
  };
  wallet: {
    status: string;
    available: boolean;
    cash_balance_usdc: Numeric | null;
    open_cost_usdc: Numeric;
    market_value_usdc: Numeric | null;
    total_assets_usdc: Numeric | null;
    unrealized_pnl_usdc: Numeric | null;
    cash_reserve_usdc: Numeric;
    available_cash_usdc: Numeric | null;
    open_position_count: number;
    last_balance_at: string | null;
    balance_stale: boolean;
    valuation_complete: boolean;
    unpriced_position_count: number;
    last_error: string | null;
  };
  daily: HomeDaily[];
  recent_auto_decisions: Array<{
    id: number;
    created_at: string;
    selected_rule: WhaleRule | null;
    matched_rules: WhaleRule[];
    title: string;
    outcome: string;
    market_slug: string | null;
    event_slug: string | null;
    configured_amount_usdc: Numeric | null;
    filled_usdc: Numeric;
    status: string;
    reason: string | null;
    is_risk_exit: boolean;
  }>;
};

type ConnectionState = "connecting" | "connected" | "disconnected";

const BALANCE_REFRESH_RETRY_MS = 5 * 60 * 1000;

const RULE_LABELS: Record<WhaleRule, string> = {
  new_account: "新号大额",
  large_amount: "全量超大额",
};

const DECISION_LABELS: Record<string, string> = {
  pending: "等待处理",
  bought: "已成交",
  skipped: "已跳过",
  failed: "执行失败",
  conflict_locked: "分歧锁定",
  exit_pending: "风控退出中",
  exit_completed: "风控退出完成",
};

function decisionTone(status: string, riskExit: boolean): string {
  if (riskExit || status.startsWith("exit_")) return "risk";
  if (status === "bought") return "success";
  if (status === "failed" || status === "conflict_locked") return "danger";
  if (status === "pending") return "processing";
  return "neutral";
}

function formatClock(value: string | null): string {
  if (!value) return "—";
  return formatBeijing(value, true);
}

function formatRate(value: Numeric | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${numeric(value).toFixed(1)}%`;
}

type HomeTrendPoint = HomeDaily & {
  buyAmount: number;
  realizedPnl: number;
  positivePnlPlot: number | null;
  negativePnlPlot: number | null;
  neutralPnlPlot: number | null;
  shortDate: string;
};

function compactAxisAmount(value: number): string {
  const absolute = Math.abs(value);
  if (absolute >= 1000) return `${value < 0 ? "-" : ""}${(absolute / 1000).toFixed(absolute >= 10_000 ? 0 : 1)}k`;
  return `${value < 0 ? "-" : ""}${Math.round(absolute)}`;
}

function chartCeiling(value: number): number {
  if (value <= 1) return 1;
  const padded = value * 1.12;
  const magnitude = 10 ** Math.floor(Math.log10(padded));
  const normalized = padded / magnitude;
  const step = normalized <= 2 ? 0.5 : normalized <= 5 ? 1 : 2;
  return Math.ceil(normalized / step) * step * magnitude;
}

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return reduced;
}

function TrendTooltip({ active, payload }: TooltipContentProps) {
  const point = payload?.[0]?.payload as HomeTrendPoint | undefined;
  if (!active || !point || (point.buyAmount === 0 && point.realizedPnl === 0)) return null;
  const tone = pnlClass(point.realizedPnl);
  return (
    <div className="homeTrendTooltip">
      <header><strong>{point.date.slice(5, 7)} 月 {point.date.slice(8)} 日</strong><span>{point.buy_count} 次买入</span></header>
      <dl>
        <div><dt><i className="buy" />跟单买入</dt><dd>{formatUsdc(point.buyAmount)}</dd></div>
        <div><dt><i className={tone || "neutral"} />已实现盈亏</dt><dd className={tone}>{formatCompactSignedUsdc(point.realizedPnl)}</dd></div>
      </dl>
      <footer><span>已实现 ROI</span><strong className={tone}>{formatRate(point.realized_roi_percent)}</strong></footer>
    </div>
  );
}

function TrendDot({ cx, cy, payload }: DotItemDotProps) {
  const point = payload as HomeTrendPoint | undefined;
  if (cx == null || cy == null || !point || point.realizedPnl === 0) return null;
  return <circle className={`homeTrendDot ${pnlClass(point.realizedPnl)}`} cx={cx} cy={cy} r="3.2" />;
}

function TrendActiveDot({ cx, cy, payload }: ActiveDotProps) {
  const point = payload as HomeTrendPoint | undefined;
  if (cx == null || cy == null || !point || point.realizedPnl === 0) return null;
  return <circle className={`homeTrendActiveDot ${pnlClass(point.realizedPnl)}`} cx={cx} cy={cy} r="5" />;
}

function TrendChart({ data }: { data: HomeDaily[] }) {
  const gradientId = useId().replaceAll(":", "");
  const reducedMotion = useReducedMotion();
  const chartData = useMemo<HomeTrendPoint[]>(() => {
    const points = data.map((item) => ({
      ...item,
      buyAmount: numeric(item.buy_amount_usdc),
      realizedPnl: numeric(item.realized_pnl_usdc),
      shortDate: item.date.slice(5),
    }));
    const firstActiveIndex = points.findIndex((point) => point.realizedPnl !== 0);
    let lastActiveIndex = -1;
    for (let index = points.length - 1; index >= 0; index -= 1) {
      if (points[index].realizedPnl !== 0) {
        lastActiveIndex = index;
        break;
      }
    }
    return points.map((point, index) => ({
      ...point,
      positivePnlPlot: point.realizedPnl > 0
        || (point.realizedPnl === 0 && (points[index - 1]?.realizedPnl > 0 || points[index + 1]?.realizedPnl > 0))
        ? point.realizedPnl
        : null,
      negativePnlPlot: point.realizedPnl < 0
        || (point.realizedPnl === 0 && (points[index - 1]?.realizedPnl < 0 || points[index + 1]?.realizedPnl < 0))
        ? point.realizedPnl
        : null,
      neutralPnlPlot: point.realizedPnl === 0 && index >= firstActiveIndex && index <= lastActiveIndex
        ? 0
        : null,
    }));
  }, [data]);
  const buyMax = chartCeiling(Math.max(1, ...chartData.map((item) => item.buyAmount)));
  const pnlMax = chartCeiling(Math.max(1, ...chartData.map((item) => Math.abs(item.realizedPnl))));
  return (
    <div className="homeTrendChart" data-point-count={data.length} role="img" aria-label="每日买入金额和已实现盈亏趋势">
      <div className="homeChartLegend" aria-hidden="true">
        <span className="buy">买入金额</span><span className="pnl">已实现盈亏</span>
      </div>
      <div className="homeTrendPlot">
        <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={240}>
          <ComposedChart data={chartData} margin={{ top: 14, right: 4, bottom: 2, left: 0 }} accessibilityLayer>
            <defs>
              <linearGradient id={`${gradientId}-buy`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#6487f6" stopOpacity="0.92" />
                <stop offset="100%" stopColor="#c9d7ff" stopOpacity="0.72" />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke="#e8eaf0" strokeDasharray="3 5" />
            <XAxis dataKey="shortDate" axisLine={false} tickLine={false} tickMargin={12} minTickGap={data.length <= 7 ? 12 : 32} tick={{ fill: "#969aa4", fontSize: 10 }} />
            <YAxis yAxisId="buy" domain={[0, buyMax]} axisLine={false} tickLine={false} tickCount={4} width={38} tickFormatter={compactAxisAmount} tick={{ fill: "#a0a4ad", fontSize: 9 }} />
            <YAxis yAxisId="pnl" orientation="right" domain={[-pnlMax, pnlMax]} axisLine={false} tickLine={false} tickCount={5} width={38} tickFormatter={compactAxisAmount} tick={{ fill: "#a0a4ad", fontSize: 9 }} />
            <ReferenceLine yAxisId="pnl" y={0} stroke="#bfc4ce" strokeDasharray="3 4" />
            <Tooltip content={TrendTooltip} cursor={false} wrapperStyle={{ outline: "none", zIndex: 10 }} />
            <Bar yAxisId="buy" dataKey="buyAmount" name="买入金额" fill={`url(#${gradientId}-buy)`} radius={[4, 4, 1, 1]} maxBarSize={data.length <= 7 ? 42 : 22} isAnimationActive={!reducedMotion} animationDuration={550} />
            <Line yAxisId="pnl" dataKey="neutralPnlPlot" name="零盈亏" type="linear" stroke="#aeb4c0" strokeWidth={1.7} dot={false} activeDot={false} isAnimationActive={!reducedMotion} animationDuration={500} />
            <Line yAxisId="pnl" dataKey="positivePnlPlot" name="已实现盈亏" type="monotoneX" stroke="#109568" strokeWidth={2.4} dot={TrendDot} activeDot={TrendActiveDot} isAnimationActive={!reducedMotion} animationDuration={650} />
            <Line yAxisId="pnl" dataKey="negativePnlPlot" name="已实现盈亏" type="monotoneX" stroke="#d4514b" strokeWidth={2.4} dot={TrendDot} activeDot={TrendActiveDot} isAnimationActive={!reducedMotion} animationDuration={650} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function MetricCard({ label, value, detail, tone = "" }: { label: string; value: string; detail: string; tone?: string }) {
  return <article className={`homeMetricCard ${tone}`}><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>;
}

export default function HomeWorkspace() {
  const [overview, setOverview] = useState<HomeOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [balanceError, setBalanceError] = useState<string | null>(null);
  const [range, setRange] = useState<7 | 15 | 30>(7);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const balanceRefreshInFlight = useRef(false);
  const lastBalanceRefreshAttempt = useRef(0);

  const loadOverview = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const next = await whaleApi<HomeOverview>("/api/home/overview");
      setOverview(next);
      setError(null);
      return next;
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "首页数据加载失败");
      return null;
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  const refreshBalance = useCallback(async (force: boolean) => {
    const now = Date.now();
    if (balanceRefreshInFlight.current) return;
    if (!force && now - lastBalanceRefreshAttempt.current < BALANCE_REFRESH_RETRY_MS) return;
    balanceRefreshInFlight.current = true;
    lastBalanceRefreshAttempt.current = now;
    setRefreshing(true);
    setBalanceError(null);
    try {
      await whaleApi("/api/execution-account/balance/refresh", { method: "POST" });
    } catch (refreshError) {
      setBalanceError(refreshError instanceof Error ? refreshError.message : "钱包余额刷新失败");
    } finally {
      await loadOverview(true);
      balanceRefreshInFlight.current = false;
      setRefreshing(false);
    }
  }, [loadOverview]);

  useEffect(() => {
    let active = true;
    const initial = window.setTimeout(async () => {
      const next = await loadOverview();
      if (!active || !next?.wallet.balance_stale) return;
      await refreshBalance(false);
    }, 0);
    return () => { active = false; window.clearTimeout(initial); };
  }, [loadOverview, refreshBalance]);

  useVisibleAutoRefresh(async () => {
    const next = await loadOverview(true);
    if (next?.wallet.balance_stale) await refreshBalance(false);
  }, 30_000);

  const visibleDays = useMemo(() => overview?.daily.slice(-range) ?? [], [overview, range]);
  const runtimeStatus = error !== null || connection === "disconnected"
    ? "error"
    : overview?.system.status;
  const runtimeLabel = error
    ? "运行状态无法确认"
    : connection === "disconnected"
      ? "运行状态连接中断"
      : runtimeStatus === "healthy"
        ? "系统运行正常"
        : runtimeStatus === "disabled"
          ? "链上扫描已停用"
          : "系统存在异常";
  const alerts = useMemo(() => {
    if (!overview) return [];
    const items: Array<{ key: string; title: string; detail: string }> = [];
    if (overview.system.status === "error") items.push({ key: "scan", title: "链上扫描异常", detail: overview.system.last_scan_error || `已连续失败 ${overview.system.consecutive_failures} 次` });
    if (!overview.wallet.available) items.push({ key: "wallet", title: "交易钱包不可用", detail: overview.wallet.last_error || "请先在设置中完成交易账户配置。" });
    if (!overview.wallet.valuation_complete) items.push({ key: "valuation", title: "持仓估值不完整", detail: `${overview.wallet.unpriced_position_count} 个开放仓位缺少有效买一价，已暂停总资产估算。` });
    if (connection === "disconnected") items.push({ key: "stream", title: "请求实时流已断开", detail: "正在自动重连；下方仍保留当前进程的最近请求快照。" });
    if (balanceError) items.push({ key: "balance", title: "余额刷新失败，已保留缓存数据", detail: `${balanceError} · 缓存时间 ${formatClock(overview.wallet.last_balance_at)}` });
    return items;
  }, [balanceError, connection, overview]);

  return (
    <PolyCopyShell
      active="home"
      title="首页"
      subtitle="运行状态、跟单表现与钱包资产总览"
      actions={<button className="pcButton primary" type="button" onClick={() => void refreshBalance(true)} disabled={refreshing || loading}><span className={refreshing ? "spinning" : ""}>↻</span>{refreshing ? "刷新中" : "刷新首页"}</button>}
    >
      {error && <div className="pcAlert danger homeLoadAlert" role="alert"><div><strong>首页数据请求失败</strong><p>{error}</p></div><button type="button" onClick={() => void loadOverview()}>重试</button></div>}

      {alerts.length > 0 && <section className="homeAlertStack" aria-label="首页告警">{alerts.map((item) => <div className="homeAlert" role="alert" key={item.key}><span aria-hidden="true">!</span><div><strong>{item.title}</strong><p>{item.detail}</p></div></div>)}</section>}

      {loading && !overview ? <div className="pcPanel pcLoading homeLoading">正在汇总运行与跟单数据…</div> : overview && <>
        <section className="homeStatusBar" aria-label="系统状态">
          <div><span className={`homeStatusDot ${runtimeStatus}`} /><strong>{runtimeLabel}</strong></div>
          <dl><div><dt>最后扫描</dt><dd>{formatClock(overview.system.last_scan_at)}</dd></div><div><dt>数据更新</dt><dd>{formatClock(overview.as_of)}</dd></div></dl>
        </section>

        <section className="pcPanel homeRuntimePanel homeRuntimeFront" aria-label="运行概览">
          <header className="homeSectionHeader"><div><span>RUNTIME OVERVIEW</span><h2>运行概览</h2><p>先确认扫描与两套发现规则是否正常，再查看资金表现。</p></div><Link className="pcButton ghost" href="/whales/settings">监测设置</Link></header>
          <div className="homeRuntimeBody">
            <div className="homeRuleCards">{overview.system.rules.map((rule) => {
              const ruleRunning = rule.enabled && runtimeStatus === "healthy";
              const monitorLabel = !rule.enabled
                ? "已停用"
                : connection === "disconnected" || error !== null
                  ? "连接中断"
                  : runtimeStatus === "healthy"
                    ? "运行中"
                    : "未运行";
              return <article key={rule.rule}><div><span className={`homeRuleDot ${ruleRunning ? "enabled" : ""}`} /><strong>{RULE_LABELS[rule.rule]}</strong></div><dl><div><dt>监测</dt><dd>{monitorLabel}</dd></div><div><dt>自动跟单</dt><dd>{rule.auto_follow_enabled ? "已开启" : "未开启"}</dd></div><div><dt>活跃钱包</dt><dd>{rule.active_wallet_count}</dd></div></dl></article>;
            })}</div>
            <article className="homeScannerCard" aria-label="扫描状态">
              <div className="homeScannerHeading"><span className={`homeStatusDot ${runtimeStatus}`} /><strong>扫描状态</strong></div>
              <dl className="homeRuntimeMeta"><div><dt>间隔</dt><dd>{overview.system.scan_interval_seconds} 秒</dd></div><div><dt>最后扫描</dt><dd>{formatClock(overview.system.last_scan_at)}</dd></div><div className={overview.system.consecutive_failures ? "danger" : "healthy"}><dt>连续失败</dt><dd>{overview.system.consecutive_failures} 次</dd></div></dl>
            </article>
          </div>
        </section>

        <section className="homeMetrics" aria-label="今日核心指标">
          <MetricCard label="今日跟单买入" value={formatCompactUsdc(overview.today.buy_amount_usdc)} detail={`${overview.today.buy_count} 次已成交买入`} />
          <MetricCard label="今日已实现盈亏" value={formatCompactSignedUsdc(overview.today.realized_pnl_usdc)} detail={`已实现 ROI ${formatPercent(overview.today.realized_roi_percent)}`} tone={pnlClass(overview.today.realized_pnl_usdc)} />
          <MetricCard label="当前持仓浮盈亏" value={formatCompactSignedUsdc(overview.today.unrealized_pnl_usdc)} detail={overview.wallet.valuation_complete ? "按当前有效买一价估值" : "估值不完整"} tone={pnlClass(overview.today.unrealized_pnl_usdc)} />
          <MetricCard label="今日结束仓位胜率" value={formatRate(overview.today.win_rate_percent)} detail={`${overview.today.win_count} 胜 · ${overview.today.loss_count} 负${overview.today.flat_count ? ` · ${overview.today.flat_count} 平` : ""}`} />
          <MetricCard label="钱包总资产估值" value={overview.wallet.total_assets_usdc == null ? "估值不完整" : formatCompactUsdc(overview.wallet.total_assets_usdc)} detail="现金余额 + 可估值持仓市值" />
        </section>

        <div className="homePortfolioGrid">
          <section className="pcPanel homeTrendPanel">
            <header className="homeSectionHeader"><div><span>DAILY COPY TREND</span><h2>每日跟单趋势</h2><p>北京时间自然日；柱状图为买入金额，折线为已实现盈亏。</p></div><div className="homeRangeSwitch" role="group" aria-label="趋势日期范围"><button type="button" className={range === 7 ? "active" : ""} aria-pressed={range === 7} onClick={() => setRange(7)}>近 7 日</button><button type="button" className={range === 15 ? "active" : ""} aria-pressed={range === 15} onClick={() => setRange(15)}>近 15 日</button><button type="button" className={range === 30 ? "active" : ""} aria-pressed={range === 30} onClick={() => setRange(30)}>近 30 日</button></div></header>
            <TrendChart data={visibleDays} />
          </section>

          <section className="pcPanel homeWalletPanel" aria-label="钱包资产">
            <header className="homeSectionHeader"><div><span>WALLET ASSETS</span><h2>钱包资产</h2><p>余额更新 {formatClock(overview.wallet.last_balance_at)}</p></div><span className={`homeWalletState ${overview.wallet.available ? "ready" : "danger"}`}>{overview.wallet.available ? "账户可用" : "账户不可用"}</span></header>
            <div className="homeWalletHero"><span>估算总资产</span><strong>{overview.wallet.total_assets_usdc == null ? "估值不完整" : formatUsdc(overview.wallet.total_assets_usdc)}</strong><small>pUSD / USDC 与开放仓位合计</small></div>
            <dl className="homeWalletMetrics">
              <div><dt>pUSD / USDC 现金</dt><dd>{formatUsdc(overview.wallet.cash_balance_usdc)}</dd></div>
              <div><dt>持仓成本</dt><dd>{formatUsdc(overview.wallet.open_cost_usdc)}</dd></div>
              <div><dt>持仓市值</dt><dd>{overview.wallet.valuation_complete ? formatUsdc(overview.wallet.market_value_usdc) : "估值不完整"}</dd></div>
              <div><dt>当前浮盈亏</dt><dd className={pnlClass(overview.wallet.unrealized_pnl_usdc)}>{formatCompactSignedUsdc(overview.wallet.unrealized_pnl_usdc)}</dd></div>
              <div><dt>现金保留线</dt><dd>{formatUsdc(overview.wallet.cash_reserve_usdc)}</dd></div>
              <div><dt>保留线以上可用现金</dt><dd>{formatUsdc(overview.wallet.available_cash_usdc)}</dd></div>
              <div><dt>开放仓位</dt><dd>{overview.wallet.open_position_count} 个</dd></div>
            </dl>
          </section>
        </div>

        <section className="pcPanel homeDecisionsPanel" aria-label="最近自动跟单">
          <header className="homeSectionHeader"><div><span>AUTO COPY DECISIONS</span><h2>最近自动跟单</h2><p>最近 8 条策略决策与实际成交结果。</p></div><div className="homeSectionLinks"><Link href="/whales/auto-follow">查看自动跟单</Link><Link href="/whales/records">查看我的跟单</Link></div></header>
          {overview.recent_auto_decisions.length ? <div className="homeDecisionList">{overview.recent_auto_decisions.map((item) => <article key={item.id} className="homeDecisionRow"><time>{formatClock(item.created_at)}</time><div className="homeDecisionRule"><strong>{item.selected_rule ? RULE_LABELS[item.selected_rule] : item.matched_rules.map((rule) => RULE_LABELS[rule]).join(" + ") || "待定规则"}</strong><small>计划 {formatUsdc(item.configured_amount_usdc)}</small></div><a href={marketUrl(item.event_slug || item.market_slug)} target="_blank" rel="noreferrer"><strong>{item.title}</strong><small>{item.outcome}</small></a><div className="homeDecisionFill"><strong>{numeric(item.filled_usdc) > 0 ? formatUsdc(item.filled_usdc) : "未成交"}</strong><small>实际成交</small></div><span className={`homeDecisionStatus ${decisionTone(item.status, item.is_risk_exit)}`}>{DECISION_LABELS[item.status] || item.status}</span><p>{item.reason || "等待处理"}</p></article>)}</div> : <div className="homeEmpty">暂无自动跟单决策。</div>}
        </section>

        <section className="homeRequestSection" aria-label="请求监控">
          <WhaleRequestMonitorPanel onConnectionChange={setConnection} />
        </section>
      </>}
    </PolyCopyShell>
  );
}
