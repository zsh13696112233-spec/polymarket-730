"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PolyCopyShell } from "./PolyCopyShell";
import { useVisibleAutoRefresh } from "./useVisibleAutoRefresh";
import {
  WhaleRecord,
  WhaleRecordList,
  WhaleRecordSummary,
  formatBeijing,
  formatPercent,
  formatPrice,
  formatUsdc,
  marketUrl,
  numeric,
  pnlClass,
  transactionUrl,
  whaleApi,
} from "./WhaleShared";

const AUTO_REFRESH_INTERVAL_MS = 10_000;

const EMPTY_SUMMARY: WhaleRecordSummary = {
  total_invested_usdc: 0,
  total_proceeds_usdc: 0,
  total_fee_usdc: 0,
  realized_pnl: 0,
  unrealized_pnl: 0,
  total_pnl: 0,
  open_position_count: 0,
  closed_position_count: 0,
  win_count: 0,
  loss_count: 0,
  excluded_conflict_exit_count: 0,
  excluded_chain_test_count: 0,
  win_rate_percent: null,
  average_profit_ratio_percent: null,
};

function recordTypeLabel(type: string) {
  const labels: Record<string, string> = {
    buy: "买入",
    sell: "卖出",
    redeem: "赎回",
    resolved_loss: "结算亏损",
    dust_writeoff: "尾差核销",
  };
  return labels[type] || type;
}

function recordTypeTone(type: string) {
  if (type === "buy") return "buy";
  if (type === "sell") return "sell";
  if (type === "redeem") return "success";
  return "danger";
}

function recordSourceLabel(source: string) {
  const labels: Record<string, string> = {
    follow: "跟单成交",
    manual: "手动交易",
    wallet_manual: "持仓管理卖出",
    auto_follow: "自动跟单",
    conflict_exit: "分歧风控",
    chain_test: "链路测试",
    auto_redeem: "自动结算",
    reconciliation: "自动对账",
  };
  return labels[source] || source;
}

function performanceExclusionHint(conflictExits: number, chainTests: number) {
  const excluded = [
    conflictExits ? `${conflictExits} 笔分歧退出` : "",
    chainTests ? `${chainTests} 笔链路测试` : "",
  ].filter(Boolean);
  return excluded.length ? ` · 不含 ${excluded.join("、")}` : "";
}

export default function WhaleRecordsWorkspace() {
  const [records, setRecords] = useState<WhaleRecord[]>([]);
  const [summary, setSummary] = useState<WhaleRecordSummary>(EMPTY_SUMMARY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [appliedStart, setAppliedStart] = useState("");
  const [appliedEnd, setAppliedEnd] = useState("");

  const loadData = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError(null);
    const params = new URLSearchParams({ limit: "200", offset: "0" });
    if (appliedStart) params.set("start_date", appliedStart);
    if (appliedEnd) params.set("end_date", appliedEnd);
    try {
      const recordResponse = await whaleApi<WhaleRecordList>(`/api/whales/records?${params.toString()}`);
      setRecords(recordResponse.items || []);
      setSummary({ ...EMPTY_SUMMARY, ...(recordResponse.summary || {}) });
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "巨鲸跟单记录加载失败");
    } finally {
      if (!silent) setLoading(false);
    }
  }, [appliedEnd, appliedStart]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadData(), 0);
    return () => window.clearTimeout(timer);
  }, [loadData]);

  useVisibleAutoRefresh(async () => {
    if (loading) return;
    await loadData(true);
  }, AUTO_REFRESH_INTERVAL_MS);

  const applyDates = () => {
    if (startDate && endDate && startDate > endDate) {
      setError("开始日期不能晚于结束日期");
      return;
    }
    setAppliedStart(startDate);
    setAppliedEnd(endDate);
  };

  return (
    <PolyCopyShell
      active="whale-records"
      title="巨鲸跟单记录"
      subtitle="从真实买入到卖出或结算赎回，逐笔还原投入、费用与最终盈亏。"
      actions={
        <>
          <Link className="pcButton ghost" href="/positions">查看持仓管理</Link>
          <Link className="pcButton ghost" href="/whales">返回巨鲸发现</Link>
          <button className="pcButton primary" type="button" onClick={() => void loadData()} disabled={loading}>
            <span className={loading ? "spinning" : ""}>↻</span> 刷新记录
          </button>
        </>
      }
    >
      <section className="whaleRecordMetrics" aria-label="跟单汇总">
        <Metric label="总投入" value={formatUsdc(summary.total_invested_usdc)} hint={`累计手续费 ${formatUsdc(summary.total_fee_usdc)}`} />
        <Metric label="总回收" value={formatUsdc(summary.total_proceeds_usdc)} hint="卖出与赎回到账" />
        <Metric label="已实现盈亏" value={formatUsdc(summary.realized_pnl)} tone={pnlClass(summary.realized_pnl)} hint={`${summary.closed_position_count} 个已结束仓位`} />
        <Metric label="浮动盈亏" value={formatUsdc(summary.unrealized_pnl)} tone={pnlClass(summary.unrealized_pnl)} hint={`${summary.open_position_count} 个当前仓位`} />
        <Metric label="总盈亏" value={formatUsdc(summary.total_pnl)} tone={pnlClass(summary.total_pnl)} hint="已实现 + 浮动" />
        <Metric label="已结束胜率" value={formatPercent(summary.win_rate_percent)} hint={`${summary.win_count} 胜 / ${summary.loss_count} 负${performanceExclusionHint(summary.excluded_conflict_exit_count, summary.excluded_chain_test_count)}`} />
        <Metric label="平均盈利比" value={formatPercent(summary.average_profit_ratio_percent)} tone={pnlClass(summary.average_profit_ratio_percent)} hint="已结束仓位口径" />
      </section>

      {error && (
        <div className="pcAlert danger whalePageError" role="alert">
          <strong>记录读取未完成</strong><p>{error}</p><button type="button" onClick={() => void loadData()}>重试</button>
        </div>
      )}

      <section className="pcPanel whaleLedgerPanel">
        <header className="pcPanelHeader whaleRecordsHeader">
          <div><span className="pcEyebrow">FOLLOW LEDGER</span><h2>历史流水</h2><p>每一行对应一笔买入、卖出、赎回或结算亏损。</p></div>
          <div className="whaleDateFilters">
            <label><span>开始</span><input aria-label="流水开始日期" type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
            <label><span>结束</span><input aria-label="流水结束日期" type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label>
            <button className="pcButton ghost" type="button" onClick={applyDates}>筛选</button>
            {(appliedStart || appliedEnd) && <button className="whaleClearDates" type="button" onClick={() => { setStartDate(""); setEndDate(""); setAppliedStart(""); setAppliedEnd(""); }}>清除</button>}
          </div>
        </header>
        {records.length ? (
          <div className="pcTableWrap">
            <table className="pcTable whaleLedgerTable">
              <thead><tr><th>时间</th><th>来源 / 类型</th><th>市场 / 方向</th><th className="numeric">价格</th><th className="numeric">份额</th><th className="numeric">金额</th><th className="numeric">手续费</th><th className="numeric">本笔盈亏</th><th>交易</th></tr></thead>
              <tbody>{records.map((record) => <LedgerRow key={record.id} record={record} />)}</tbody>
            </table>
          </div>
        ) : (
          <div className="pcEmptyState whaleCompactEmpty"><div className="pcEmptyIcon">≡</div><h2>所选日期内没有流水</h2><p>调整日期范围，或等待第一笔跟单成交。</p></div>
        )}
      </section>

    </PolyCopyShell>
  );
}

function Metric({ label, value, hint, tone = "" }: { label: string; value: string; hint: string; tone?: string }) {
  return <article className="pcMetricCard"><span>{label}</span><strong className={tone}>{value}</strong><small>{hint}</small></article>;
}

function LedgerRow({ record }: { record: WhaleRecord }) {
  return (
    <tr>
      <td><strong>{formatBeijing(record.timestamp, true)}</strong></td>
      <td><span className={`pcBadge ${recordTypeTone(record.type)}`}>{recordSourceLabel(record.source)}</span><small>{recordTypeLabel(record.type)}</small></td>
      <td><a className="pcMarketIdentity" href={marketUrl(record.event_slug || record.market_slug)} target="_blank" rel="noreferrer"><strong>{record.title}</strong><span>{record.outcome}</span></a></td>
      <td className="numeric"><strong>{formatPrice(record.price)}</strong></td>
      <td className="numeric"><strong>{numeric(record.size).toFixed(4)}</strong></td>
      <td className="numeric"><strong>{formatUsdc(record.amount_usdc)}</strong></td>
      <td className="numeric"><strong>{formatUsdc(record.fee_usdc)}</strong></td>
      <td className="numeric"><strong className={pnlClass(record.realized_pnl)}>{numeric(record.realized_pnl) === 0 ? "—" : formatUsdc(record.realized_pnl)}</strong></td>
      <td>{record.transaction_hash ? <a className="pcTextLink" href={transactionUrl(record.transaction_hash)} target="_blank" rel="noreferrer">{record.transaction_hash.slice(0, 8)}… ↗</a> : <span>—</span>}</td>
    </tr>
  );
}
