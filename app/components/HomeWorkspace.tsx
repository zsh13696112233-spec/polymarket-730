"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
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
    status: "healthy" | "degraded" | "error" | "disabled";
    enabled: boolean;
    last_scan_at: string | null;
    last_scan_error: string | null;
    coverage_incomplete_until: string | null;
    consecutive_failures: number;
    scan_interval_seconds: number;
    rules: HomeSystemRule[];
  };
  opportunity_counts: Record<WhaleRule, {
    last_1_day: number;
    last_3_days: number;
    last_5_days: number;
    last_7_days: number;
    last_15_days: number;
    last_30_days: number;
  }>;
  follow_counts: HomeOverview["opportunity_counts"];
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
    winning_pnl_usdc: Numeric;
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

function TrendDetails({ data, today }: { data: HomeDaily[]; today: string }) {
  const maxPnl = Math.max(0, ...data.map((day) => Math.abs(numeric(day.realized_pnl_usdc))));
  return (
    <section className="homeTrendDetails" data-point-count={data.length} aria-label="每日跟单明细">
      <header><div><h3>每日明细</h3></div><div className="homePnlLegend"><span className="negative">亏损向左</span><span className="positive">盈利向右</span></div></header>
      {maxPnl === 0 && <p className="homeTrendEmpty">所选区间每日已实现盈亏均为 0</p>}
      <div className="homeTrendTableScroll" tabIndex={0} role="region" aria-label="每日明细表，可滚动查看">
        <table>
          <thead><tr><th scope="col">日期</th><th scope="col" className="homePnlColumn">已实现盈亏 <small>USDC</small></th><th scope="col">买入金额 <small>USDC</small></th><th scope="col">买入次数</th><th scope="col">结束仓位 · 胜 / 负 / 平</th><th scope="col">胜率</th></tr></thead>
          <tbody>{[...data].reverse().map((day) => {
            const finished = day.win_count + day.loss_count + day.flat_count;
            const pnl = numeric(day.realized_pnl_usdc);
            return <tr key={day.date}>
              <th scope="row"><time dateTime={day.date}>{day.date.slice(5)}</time>{day.date === today && <span className="homeTrendToday">进行中</span>}</th>
              <td className="homePnlColumn"><div className="homePnlCell"><div className="homePnlTrack" aria-hidden="true">
                {pnl !== 0 && <span className={`homePnlBar ${pnl > 0 ? "positive" : "negative"}`} style={{ width: `${Math.abs(pnl) / maxPnl * 50}%` }} />}
              </div><span className="homePnlAmount">{pnl > 0 ? "+" : ""}{formatUsdc(day.realized_pnl_usdc).replace(" USDC", "")}</span></div></td>
              <td>{formatUsdc(day.buy_amount_usdc).replace(" USDC", "")}</td><td>{day.buy_count}</td>
              <td>{finished ? `${day.win_count} 胜 / ${day.loss_count} 负 / ${day.flat_count} 平` : "—"}</td><td>{formatRate(day.win_rate_percent)}</td>
            </tr>;
          })}</tbody>
        </table>
      </div>
    </section>
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
      buyAmount: data.reduce((total, item) => total + numeric(item.buy_amount_usdc), 0),
      realizedPnl: data.reduce((total, item) => total + numeric(item.realized_pnl_usdc), 0),
      wins,
      losses,
      flats,
      finished: decided + flats,
      winRate: decided ? wins / decided * 100 : null,
    };
  }, [data]);
  return (
    <dl className="homeTrendSummary" aria-label="所选区间跟单汇总">
      <div><dt>区间已实现盈亏</dt><dd>{formatCompactSignedUsdc(summary.realizedPnl)}</dd></div>
      <div><dt>区间买入金额</dt><dd>{formatCompactUsdc(summary.buyAmount)}</dd><small>{summary.buyCount} 次独立买入</small></div>
      <div><dt>结束仓位胜率</dt><dd>{formatRate(summary.winRate)}</dd><small>{summary.finished} 个结束仓位 · {summary.wins} 胜 / {summary.losses} 负 / {summary.flats} 平</small></div>
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
  secondaryDetail,
  tone = "",
  effectAngle,
}: {
  label: string;
  value: string;
  detail: string | null;
  secondaryDetail?: ReactNode;
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
      <span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}
      {secondaryDetail && <small>{secondaryDetail}</small>}
    </article>
  );
}

function CopyFlowMetricCard({
  buyAmount,
  buyCount,
  exitProceeds,
  exitCount,
  signals,
}: {
  buyAmount: Numeric;
  buyCount: number;
  exitProceeds: Numeric;
  exitCount: number;
  signals: HomeOverview["opportunity_counts"];
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
      <small className="homeTodaySignals" aria-label="今日监测信号"><span>今日监测信号</span><span>新号大额 <b>{signals.new_account.last_1_day}</b></span><span>全量超大额 <b>{signals.large_amount.last_1_day}</b></span></small>
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
        : runtimeStatus === "degraded"
          ? "重点市场历史补齐中"
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
      actions={<>
        <button className="pcButton primary" type="button" onClick={() => void refreshBalance(true)} disabled={refreshing || loading}><span className={refreshing ? "spinning" : ""}>↻</span>{refreshing ? "刷新中" : "刷新首页"}</button>
      </>}
    >
      {error && <div className="pcAlert danger homeLoadAlert" role="alert"><div><strong>首页数据请求失败</strong><p>{error}</p></div><button type="button" onClick={() => void loadOverview()}>重试</button></div>}

      {alerts.length > 0 && <section className="homeAlertStack" aria-label="首页告警">{alerts.map((item) => <div className="homeAlert" role="alert" key={item.key}><span aria-hidden="true">!</span><div><strong>{item.title}</strong><p>{item.detail}</p></div></div>)}</section>}

      {loading && !overview ? <div className="pcPanel pcLoading homeLoading">正在汇总运行与跟单数据…</div> : overview && <>
        <section className="homeMetrics" aria-label="今日核心指标">
          <CopyFlowMetricCard buyAmount={overview.today.buy_amount_usdc} buyCount={overview.today.buy_count} exitProceeds={overview.today.conflict_exit_proceeds_usdc} exitCount={overview.today.conflict_exit_count} signals={overview.opportunity_counts} />
          <MetricCard label="今日已实现盈亏" value={formatCompactSignedUsdc(overview.today.realized_pnl_usdc)} detail={`已实现 ROI ${formatPercent(overview.today.realized_roi_percent)}`} tone={pnlClass(overview.today.realized_pnl_usdc)} effectAngle={18} />
          <MetricCard label="当前持仓浮动盈亏合计" value={formatCompactSignedUsdc(overview.today.unrealized_pnl_usdc)} detail={overview.wallet.valuation_complete ? null : "估值不完整"} secondaryDetail={<>获胜收益 <span className={pnlClass(overview.wallet.winning_pnl_usdc)}>{formatCompactSignedUsdc(overview.wallet.winning_pnl_usdc)}</span></>} tone={pnlClass(overview.today.unrealized_pnl_usdc)} effectAngle={-24} />
          <MetricCard label="今日结束仓位胜率" value={formatRate(overview.today.win_rate_percent)} detail={`${overview.today.win_count} 胜 · ${overview.today.loss_count} 负${overview.today.flat_count ? ` · ${overview.today.flat_count} 平` : ""}${performanceExclusionDetail(overview.today.excluded_conflict_exit_count, overview.today.excluded_chain_test_count)}`} effectAngle={32} />
        </section>

        <div className="homePortfolioGrid">
          <section className="pcPanel homeTrendPanel">
            <header className="homeSectionHeader"><div><h2>每日跟单表现</h2></div><div className="homeTrendControls"><div className="homeRangeSwitch" role="group" aria-label="趋势日期范围"><button type="button" className={range === 7 ? "active" : ""} aria-pressed={range === 7} onClick={() => setRange(7)}>近 7 日</button><button type="button" className={range === 15 ? "active" : ""} aria-pressed={range === 15} onClick={() => setRange(15)}>近 15 日</button><button type="button" className={range === 30 ? "active" : ""} aria-pressed={range === 30} onClick={() => setRange(30)}>近 30 日</button></div></div></header>
            <TrendSummary data={visibleDays} />
            <div className="homeSignalSummary" aria-label="链上监测信号">
              <span>监测信号</span>
              <dl>{(["new_account", "large_amount"] as const).map((rule) => <div key={rule}><dt>{RULE_LABELS[rule]}</dt><dd>{overview.opportunity_counts[rule][`last_${range}_days` as const]}</dd></div>)}</dl>
            </div>
            <TrendDetails data={visibleDays} today={overview.today.date} />
          </section>
          <div className="homeStatusWalletGrid">
          <section className="pcPanel homeRuntimePanel" aria-label="运行概览">
            <header className="homeSectionHeader"><div><h2>运行概览</h2></div><div className="homeRuntimeHealth" aria-label="系统状态"><span className={`homeStatusDot ${runtimeTone}`} aria-hidden="true" /><strong>{runtimeLabel}</strong></div></header>
            <div className="homeRuntimeCompact">
              {overview.system.rules.map((rule) => {
                const monitorLabel = !rule.enabled ? "已停用" : error !== null || connection === "disconnected" ? "状态待确认" : runtimeStatus === "disabled" ? "未运行" : runtimeStatus === "error" ? "监测异常" : "监测中";
                return <div className="homeRuntimeRule" key={rule.rule} aria-label={RULE_LABELS[rule.rule]}><strong>{RULE_LABELS[rule.rule]}</strong><div>{monitorLabel !== "监测中" && <span className="homeRuleNotice">{monitorLabel}</span>}<span>自动跟单{rule.auto_follow_enabled ? "已开启" : "未开启"}</span></div></div>;
              })}
              {(runtimeStatus === "error" || runtimeStatus === "degraded") && <p className="homeRuntimeReason">{error || (connection === "disconnected" ? "连接中断，正在重连" : overview.system.last_scan_error) || runtimeLabel}</p>}
              <footer className="homeRuntimeFooter">
                <dl><div><dt>最后扫描</dt><dd>{formatClock(overview.system.last_scan_at)}</dd></div><div><dt>数据更新</dt><dd>{formatClock(overview.as_of)}</dd></div></dl>
                <Link className="homeRuntimeSettings" href="/whales/settings">监测设置 ↗</Link>
              </footer>
            </div>
          </section>
          <section className="pcPanel homeWalletPanel" aria-label="钱包资产">
            <header className="homeSectionHeader"><div><h2>钱包资产</h2><p>余额更新 {formatClock(overview.wallet.last_balance_at)}</p></div><span className={`homeWalletState ${overview.wallet.available ? "ready" : "danger"}`}>{overview.wallet.available ? "账户可用" : "账户不可用"}</span></header>
            <div className="homeWalletCompact">
            <div className="homeWalletHero"><span>估算总资产</span><strong>{overview.wallet.total_assets_usdc == null ? "估值不完整" : formatUsdc(overview.wallet.total_assets_usdc)}</strong><small>pUSD / USDC 与开放仓位合计</small></div>
            <dl className="homeWalletMetrics">
              <div><dt>pUSD / USDC 现金</dt><dd>{formatUsdc(overview.wallet.cash_balance_usdc)}</dd></div>
              <div><dt>持仓成本</dt><dd>{formatUsdc(overview.wallet.open_cost_usdc)}</dd></div>
              <div><dt>持仓市值</dt><dd>{overview.wallet.valuation_complete ? formatUsdc(overview.wallet.market_value_usdc) : "估值不完整"}</dd></div>
              <div><dt>现金保留线</dt><dd>{formatUsdc(overview.wallet.cash_reserve_usdc)}</dd></div>
              <div><dt>保留线以上可用现金</dt><dd>{formatUsdc(overview.wallet.available_cash_usdc)}</dd></div>
              <div><dt>开放仓位</dt><dd>{overview.wallet.open_position_count} 个</dd></div>
            </dl>
            </div>
          </section>
        </div>
        </div>

        <section className="homeRequestSection" aria-label="请求监控">
          <WhaleRequestMonitorPanel onConnectionChange={setConnection} />
        </section>
      </>}
    </PolyCopyShell>
  );
}
