"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
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
  conflict_exit_proceeds_usdc: Numeric;
  conflict_exit_count: number;
  realized_pnl_usdc: Numeric;
  realized_cost_usdc: Numeric;
  realized_roi_percent: Numeric | null;
  win_count: number;
  loss_count: number;
  flat_count: number;
  excluded_conflict_exit_count: number;
  excluded_chain_test_count: number;
  win_rate_percent: Numeric | null;
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
type TrendMode = "finance" | "quality";

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

function performanceExclusionDetail(conflictExits: number, chainTests: number): string {
  const excluded = [
    conflictExits ? `${conflictExits} 笔分歧退出` : "",
    chainTests ? `${chainTests} 笔链路测试` : "",
  ].filter(Boolean);
  return excluded.length ? ` · 不含 ${excluded.join("、")}` : "";
}

type HomeTrendPoint = HomeDaily & {
  buyAmount: number;
  buyCount: number;
  realizedPnl: number;
  finishedCount: number;
  winRate: number | null;
  realizedPnlPlot: number | null;
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
  if (!active || !point || (
    point.buyAmount === 0
    && point.realizedPnl === 0
    && point.finishedCount === 0
  )) return null;
  const tone = pnlClass(point.realizedPnl);
  return (
    <div className="homeTrendTooltip">
      <header><strong>{point.date.slice(5, 7)} 月 {point.date.slice(8)} 日</strong><span>{point.buy_count} 次跟单</span></header>
      <dl>
        <div><dt><i className="buy" />实际跟单买入</dt><dd>{formatUsdc(point.buyAmount)}</dd></div>
        <div><dt><i className={tone || "neutral"} />已实现盈亏</dt><dd className={tone}>{formatCompactSignedUsdc(point.realizedPnl)}</dd></div>
        <div><dt><i className="finished" />结束仓位</dt><dd>{point.finishedCount} 个</dd></div>
        <div><dt><i className="roi" />已实现 ROI</dt><dd className={tone}>{formatRate(point.realized_roi_percent)}</dd></div>
      </dl>
      <footer><span>结束仓位胜率</span><strong>{formatRate(point.win_rate_percent)}</strong><small>{point.win_count} 胜 / {point.loss_count} 负{point.flat_count ? ` / ${point.flat_count} 平` : ""}</small></footer>
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

function QualityDot({ cx, cy, payload }: DotItemDotProps) {
  const point = payload as HomeTrendPoint | undefined;
  if (cx == null || cy == null || !point || point.winRate === null) return null;
  return <circle className="homeQualityDot" cx={cx} cy={cy} r="3.2" />;
}

function QualityActiveDot({ cx, cy, payload }: ActiveDotProps) {
  const point = payload as HomeTrendPoint | undefined;
  if (cx == null || cy == null || !point || point.winRate === null) return null;
  return <circle className="homeQualityActiveDot" cx={cx} cy={cy} r="5" />;
}

export function buildHomeTrendData(data: HomeDaily[]): HomeTrendPoint[] {
  const points = data.map((item) => ({
    ...item,
    buyAmount: numeric(item.buy_amount_usdc),
    buyCount: item.buy_count,
    realizedPnl: numeric(item.realized_pnl_usdc),
    finishedCount: item.win_count + item.loss_count + item.flat_count,
    winRate: item.win_rate_percent == null ? null : numeric(item.win_rate_percent),
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
    realizedPnlPlot: firstActiveIndex >= 0
      && index >= Math.max(0, firstActiveIndex - 1)
      && index <= Math.min(points.length - 1, lastActiveIndex + 1)
      ? point.realizedPnl
      : null,
  }));
}

function TrendChart({ data, mode }: { data: HomeDaily[]; mode: TrendMode }) {
  const gradientId = useId().replaceAll(":", "");
  const reducedMotion = useReducedMotion();
  const chartData = useMemo(() => buildHomeTrendData(data), [data]);
  const buyMax = chartCeiling(Math.max(1, ...chartData.map((item) => item.buyAmount)));
  const countMax = chartCeiling(Math.max(1, ...chartData.map((item) => item.buyCount)));
  const pnlMax = chartCeiling(Math.max(1, ...chartData.map((item) => Math.abs(item.realizedPnl))));
  const plottedPnl = chartData.flatMap((item) => item.realizedPnlPlot == null ? [] : [item.realizedPnlPlot]);
  const highestPnl = Math.max(0, ...plottedPnl);
  const lowestPnl = Math.min(0, ...plottedPnl);
  const hasPositivePnl = highestPnl > 0;
  const hasNegativePnl = lowestPnl < 0;
  const zeroOffset = hasPositivePnl && hasNegativePnl
    ? highestPnl / (highestPnl - lowestPnl) * 100
    : 50;
  return (
    <div className="homeTrendChart" data-point-count={data.length} data-mode={mode} role="img" aria-label={mode === "finance" ? "每日买入金额和已实现盈亏趋势" : "每日跟单次数和结束仓位胜率趋势"}>
      <div className="homeChartLegend" aria-hidden="true">
        {mode === "finance"
          ? <><span className="buy">买入金额</span><span className="pnl">已实现盈亏</span></>
          : <><span className="count">跟单次数</span><span className="winRate">结束仓位胜率</span></>}
      </div>
      <div className="homeTrendPlot">
        <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={240}>
          <ComposedChart data={chartData} margin={{ top: 14, right: 4, bottom: 2, left: 0 }} accessibilityLayer>
            <defs>
              <linearGradient id={`${gradientId}-buy`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#6487f6" stopOpacity="0.92" />
                <stop offset="100%" stopColor="#c9d7ff" stopOpacity="0.72" />
              </linearGradient>
              <linearGradient id={`${gradientId}-pnl`} x1="0" y1="0" x2="0" y2="1">
                {hasPositivePnl && hasNegativePnl ? <>
                  <stop offset="0%" stopColor="#109568" />
                  <stop offset={`${Math.max(0, zeroOffset - 0.5)}%`} stopColor="#109568" />
                  <stop offset={`${zeroOffset}%`} stopColor="#aeb4c0" />
                  <stop offset={`${Math.min(100, zeroOffset + 0.5)}%`} stopColor="#d4514b" />
                  <stop offset="100%" stopColor="#d4514b" />
                </> : <>
                  <stop offset="0%" stopColor={hasNegativePnl ? "#d4514b" : "#109568"} />
                  <stop offset="100%" stopColor={hasNegativePnl ? "#d4514b" : "#109568"} />
                </>}
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke="#e8eaf0" strokeDasharray="3 5" />
            <XAxis dataKey="shortDate" axisLine={false} tickLine={false} tickMargin={12} minTickGap={data.length <= 7 ? 12 : 32} tick={{ fill: "#969aa4", fontSize: 10 }} />
            <Tooltip content={TrendTooltip} cursor={false} wrapperStyle={{ outline: "none", zIndex: 10 }} />
            {mode === "finance" ? <>
              <YAxis yAxisId="buy" domain={[0, buyMax]} axisLine={false} tickLine={false} tickCount={4} width={38} tickFormatter={compactAxisAmount} tick={{ fill: "#a0a4ad", fontSize: 9 }} />
              <YAxis yAxisId="pnl" orientation="right" domain={[-pnlMax, pnlMax]} axisLine={false} tickLine={false} tickCount={5} width={38} tickFormatter={compactAxisAmount} tick={{ fill: "#a0a4ad", fontSize: 9 }} />
              <ReferenceLine yAxisId="pnl" y={0} stroke="#bfc4ce" strokeDasharray="3 4" />
              <Bar yAxisId="buy" dataKey="buyAmount" name="买入金额" fill={`url(#${gradientId}-buy)`} radius={[4, 4, 1, 1]} maxBarSize={data.length <= 7 ? 42 : 22} isAnimationActive={!reducedMotion} animationDuration={550} />
              <Line yAxisId="pnl" dataKey="realizedPnlPlot" name="已实现盈亏" type="linear" stroke={`url(#${gradientId}-pnl)`} strokeWidth={2.4} dot={TrendDot} activeDot={TrendActiveDot} isAnimationActive={!reducedMotion} animationDuration={650} />
            </> : <>
              <YAxis yAxisId="count" domain={[0, countMax]} allowDecimals={false} axisLine={false} tickLine={false} tickCount={4} width={38} tick={{ fill: "#a0a4ad", fontSize: 9 }} />
              <YAxis yAxisId="rate" orientation="right" domain={[0, 100]} ticks={[0, 50, 100]} axisLine={false} tickLine={false} width={38} tickFormatter={(value) => `${value}%`} tick={{ fill: "#a0a4ad", fontSize: 9 }} />
              <ReferenceLine yAxisId="rate" y={50} stroke="#c8cbd2" strokeDasharray="3 4" />
              <Bar yAxisId="count" dataKey="buyCount" name="跟单次数" fill={`url(#${gradientId}-buy)`} radius={[4, 4, 1, 1]} maxBarSize={data.length <= 7 ? 42 : 22} isAnimationActive={!reducedMotion} animationDuration={550} />
              <Line yAxisId="rate" dataKey="winRate" name="结束仓位胜率" type="monotoneX" stroke="#8b5cf6" strokeWidth={2.4} dot={QualityDot} activeDot={QualityActiveDot} connectNulls={false} isAnimationActive={!reducedMotion} animationDuration={650} />
            </>}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function TrendSummary({ data }: { data: HomeDaily[] }) {
  const summary = useMemo(() => {
    const buyCount = data.reduce((total, item) => total + item.buy_count, 0);
    const wins = data.reduce((total, item) => total + item.win_count, 0);
    const losses = data.reduce((total, item) => total + item.loss_count, 0);
    const flats = data.reduce((total, item) => total + item.flat_count, 0);
    const decided = wins + losses;
    return {
      buyCount,
      wins,
      losses,
      flats,
      finished: decided + flats,
      winRate: decided ? wins / decided * 100 : null,
    };
  }, [data]);
  return (
    <dl className="homeTrendSummary" aria-label="所选区间跟单汇总">
      <div><dt>跟单次数</dt><dd>{summary.buyCount} 次</dd><small>独立买入事件</small></div>
      <div><dt>结束仓位</dt><dd>{summary.finished} 个</dd><small>{summary.flats ? `${summary.flats} 个平局` : "按结束日统计"}</small></div>
      <div><dt>区间胜率</dt><dd>{formatRate(summary.winRate)}</dd><small>{summary.wins} 胜 / {summary.losses} 负</small></div>
    </dl>
  );
}

function moveMetricCardEffect(event: ReactPointerEvent<HTMLElement>) {
  if (event.pointerType === "touch" || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  const card = event.currentTarget;
  const bounds = card.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return;

  const x = Math.min(Math.max(event.clientX - bounds.left, 0), bounds.width);
  const y = Math.min(Math.max(event.clientY - bounds.top, 0), bounds.height);
  const horizontal = (x / bounds.width - 0.5) * 2;
  const vertical = (y / bounds.height - 0.5) * 2;
  const angleOffset = Number(card.dataset.effectAngle ?? 0);
  const angle = Math.atan2(vertical, horizontal) * (180 / Math.PI) + 90 + angleOffset;

  card.style.setProperty("--effect-x", `${((x / bounds.width) * 100).toFixed(2)}%`);
  card.style.setProperty("--effect-y", `${((y / bounds.height) * 100).toFixed(2)}%`);
  card.style.setProperty("--effect-angle", `${angle.toFixed(2)}deg`);
  card.style.setProperty("--tilt-x", `${(-vertical * 1.4).toFixed(2)}deg`);
  card.style.setProperty("--tilt-y", `${(horizontal * 1.4).toFixed(2)}deg`);
}

function resetMetricCardEffect(event: ReactPointerEvent<HTMLElement>) {
  const card = event.currentTarget;
  ["--effect-x", "--effect-y", "--effect-angle", "--tilt-x", "--tilt-y"].forEach((property) => {
    card.style.removeProperty(property);
  });
}

function MetricCard({
  label,
  value,
  detail,
  tone = "",
  effectAngle,
}: {
  label: string;
  value: string;
  detail: string;
  tone?: string;
  effectAngle: number;
}) {
  return (
    <article
      className={`homeMetricCard ${tone}`}
      data-effect-angle={effectAngle}
      onPointerMove={moveMetricCardEffect}
      onPointerLeave={resetMetricCardEffect}
      onPointerCancel={resetMetricCardEffect}
    >
      <span>{label}</span><strong>{value}</strong><small>{detail}</small>
    </article>
  );
}

function CopyFlowMetricCard({
  buyAmount,
  buyCount,
  exitProceeds,
  exitCount,
}: {
  buyAmount: Numeric;
  buyCount: number;
  exitProceeds: Numeric;
  exitCount: number;
}) {
  return (
    <article
      className="homeMetricCard homeFlowMetricCard"
      aria-label="今日跟单买入与今日分歧退出回款"
      data-effect-angle={-12}
      onPointerMove={moveMetricCardEffect}
      onPointerLeave={resetMetricCardEffect}
      onPointerCancel={resetMetricCardEffect}
    >
      <span>今日跟单资金流</span>
      <div className="homeFlowValues">
        <div className="homeFlowValue buy"><small>实际跟单买入</small><strong>{formatCompactUsdc(buyAmount)}</strong></div>
        <div className="homeFlowValue exit"><small>分歧退出回款</small><strong>{formatCompactUsdc(exitProceeds)}</strong></div>
      </div>
      <small>{buyCount} 次买入 · {exitCount} 次分歧退出到账</small>
    </article>
  );
}

export default function HomeWorkspace() {
  const [overview, setOverview] = useState<HomeOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [balanceError, setBalanceError] = useState<string | null>(null);
  const [range, setRange] = useState<7 | 15 | 30>(7);
  const [trendMode, setTrendMode] = useState<TrendMode>("finance");
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
  const runtimeTone = loading && !overview ? "connecting" : runtimeStatus;
  const runtimeLabel = loading && !overview
    ? "正在读取状态"
    : error
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
      actions={<>
        <div className="homeTopStatus" aria-label="系统状态">
          <div className="homeTopHealth"><span className={`homeStatusDot ${runtimeTone}`} /><strong>{runtimeLabel}</strong></div>
          <dl>
            <div className="homeTopLastScan"><dt>最后扫描</dt><dd>{formatClock(overview?.system.last_scan_at ?? null)}</dd></div>
            <div><dt>数据更新</dt><dd>{formatClock(overview?.as_of ?? null)}</dd></div>
          </dl>
        </div>
        <button className="pcButton primary" type="button" onClick={() => void refreshBalance(true)} disabled={refreshing || loading}><span className={refreshing ? "spinning" : ""}>↻</span>{refreshing ? "刷新中" : "刷新首页"}</button>
      </>}
    >
      {error && <div className="pcAlert danger homeLoadAlert" role="alert"><div><strong>首页数据请求失败</strong><p>{error}</p></div><button type="button" onClick={() => void loadOverview()}>重试</button></div>}

      {alerts.length > 0 && <section className="homeAlertStack" aria-label="首页告警">{alerts.map((item) => <div className="homeAlert" role="alert" key={item.key}><span aria-hidden="true">!</span><div><strong>{item.title}</strong><p>{item.detail}</p></div></div>)}</section>}

      {loading && !overview ? <div className="pcPanel pcLoading homeLoading">正在汇总运行与跟单数据…</div> : overview && <>
        <section className="homeMetrics" aria-label="今日核心指标">
          <CopyFlowMetricCard buyAmount={overview.today.buy_amount_usdc} buyCount={overview.today.buy_count} exitProceeds={overview.today.conflict_exit_proceeds_usdc} exitCount={overview.today.conflict_exit_count} />
          <MetricCard label="今日已实现盈亏" value={formatCompactSignedUsdc(overview.today.realized_pnl_usdc)} detail={`已实现 ROI ${formatPercent(overview.today.realized_roi_percent)}`} tone={pnlClass(overview.today.realized_pnl_usdc)} effectAngle={18} />
          <MetricCard label="全仓未实现盈亏" value={formatCompactSignedUsdc(overview.today.unrealized_pnl_usdc)} detail={overview.wallet.valuation_complete ? "按当前有效买一价估值" : "估值不完整"} tone={pnlClass(overview.today.unrealized_pnl_usdc)} effectAngle={-24} />
          <MetricCard label="今日结束仓位胜率" value={formatRate(overview.today.win_rate_percent)} detail={`${overview.today.win_count} 胜 · ${overview.today.loss_count} 负${overview.today.flat_count ? ` · ${overview.today.flat_count} 平` : ""}${performanceExclusionDetail(overview.today.excluded_conflict_exit_count, overview.today.excluded_chain_test_count)}`} effectAngle={32} />
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

        <div className="homePortfolioGrid">
          <section className="pcPanel homeTrendPanel">
            <header className="homeSectionHeader"><div><span>DAILY COPY TREND</span><h2>每日跟单趋势</h2><p>北京时间自然日；跟单按买入日、胜率按仓位结束日统计，平局不计入胜率。</p></div><div className="homeTrendControls"><div className="homeViewSwitch" role="group" aria-label="趋势指标"><button type="button" className={trendMode === "finance" ? "active" : ""} aria-pressed={trendMode === "finance"} onClick={() => setTrendMode("finance")}>资金表现</button><button type="button" className={trendMode === "quality" ? "active" : ""} aria-pressed={trendMode === "quality"} onClick={() => setTrendMode("quality")}>次数与胜率</button></div><div className="homeRangeSwitch" role="group" aria-label="趋势日期范围"><button type="button" className={range === 7 ? "active" : ""} aria-pressed={range === 7} onClick={() => setRange(7)}>近 7 日</button><button type="button" className={range === 15 ? "active" : ""} aria-pressed={range === 15} onClick={() => setRange(15)}>近 15 日</button><button type="button" className={range === 30 ? "active" : ""} aria-pressed={range === 30} onClick={() => setRange(30)}>近 30 日</button></div></div></header>
            <TrendSummary data={visibleDays} />
            <TrendChart data={visibleDays} mode={trendMode} />
          </section>

          <section className="pcPanel homeWalletPanel" aria-label="钱包资产">
            <header className="homeSectionHeader"><div><span>WALLET ASSETS</span><h2>钱包资产</h2><p>余额更新 {formatClock(overview.wallet.last_balance_at)}</p></div><span className={`homeWalletState ${overview.wallet.available ? "ready" : "danger"}`}>{overview.wallet.available ? "账户可用" : "账户不可用"}</span></header>
            <div className="homeWalletHero"><span>估算总资产</span><strong>{overview.wallet.total_assets_usdc == null ? "估值不完整" : formatUsdc(overview.wallet.total_assets_usdc)}</strong><small>pUSD / USDC 与开放仓位合计</small></div>
            <dl className="homeWalletMetrics">
              <div><dt>pUSD / USDC 现金</dt><dd>{formatUsdc(overview.wallet.cash_balance_usdc)}</dd></div>
              <div><dt>持仓成本</dt><dd>{formatUsdc(overview.wallet.open_cost_usdc)}</dd></div>
              <div><dt>持仓市值</dt><dd>{overview.wallet.valuation_complete ? formatUsdc(overview.wallet.market_value_usdc) : "估值不完整"}</dd></div>
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
