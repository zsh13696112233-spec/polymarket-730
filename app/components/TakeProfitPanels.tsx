"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { formatBeijing, formatUsdc, whaleApi } from "./WhaleShared";
import { useVisibleAutoRefresh } from "./useVisibleAutoRefresh";

const ROOT = "/api/execution-account/take-profit";
export type TakeProfitSettings = {
  wallet: string | null; enabled: boolean; threshold_percent: string;
  running: boolean; reason: string; last_checked_at: string | null;
  protections: { asset_id: string; enabled: boolean; rebuy_blocked: boolean; reason: string | null }[];
};
export type TakeProfitStatistics = {
  summary: { realized_profit: string; saved: string; foregone: string; net_impact: string;
    filled_count: number; pending_resolution_count: number; pending_reconciliation_count: number };
  items: { order_id: number; title: string; outcome: string; threshold_percent: string;
    filled_size: string; net_proceeds: string | null; cost: string | null; cost_source: string;
    realized_profit: string | null; hypothetical_payout: string | null;
    saved: string | null; foregone: string | null; status: string; order_status: string;
    created_at: string; resolved_at: string | null }[];
  total: number;
};
type Holding = { asset_id: string; title: string; outcome: string; available_size: string };
const money = (value: string | null) => value === null ? "待核对" : formatUsdc(value);

function useTakeProfitData<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const running = useRef(false);
  const version = useRef(0);
  const acceptSaved = useCallback((value: T) => {
    version.current += 1;
    setData(value);
  }, []);
  const reload = useCallback(async () => {
    if (running.current || document.visibilityState === "hidden") return;
    running.current = true;
    const requestedVersion = version.current;
    setLoading(true);
    try {
      const response = await whaleApi<T>(path);
      if (requestedVersion === version.current) { setData(response); setError(null); }
    }
    catch (failure) {
      if (requestedVersion === version.current) setError(failure instanceof Error ? failure.message : "止盈信息读取失败");
    }
    finally { running.current = false; setLoading(false); }
  }, [path]);
  useEffect(() => {
    const timer = window.setTimeout(() => void reload(), 0);
    return () => window.clearTimeout(timer);
  }, [reload]);
  useVisibleAutoRefresh(reload, 10_000);
  return { data, setData: acceptSaved, error, setError, loading, reload };
}

export function TakeProfitSettingsPanel({ holdings, wallet }: { holdings: Holding[]; wallet: string | null }) {
  const { data, setData, error, setError, reload } = useTakeProfitData<TakeProfitSettings>(ROOT);
  const [saving, setSaving] = useState(false);
  const busy = useRef(false);
  const update = async (path: string, body: object) => {
    if (busy.current) return;
    busy.current = true; setSaving(true); setError(null);
    try { setData(await whaleApi<TakeProfitSettings>(path, { method: "PUT", body: JSON.stringify(body) })); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "止盈设置保存失败"); }
    finally { busy.current = false; setSaving(false); }
  };
  const ready = data?.wallet && data.wallet === wallet && !error;
  return <section className="pcPanel takeProfitPanel" aria-label="自动止盈设置">
    <header className="pcPanelHeader"><h2>自动止盈</h2><button className="pcButton ghost" onClick={() => void reload()}>刷新止盈状态</button></header>
    <p>扣费后净回款达到最高兑付额的设定比例，且相对记录成本盈利时，自动卖出全部可卖份额。实际成交取决于买盘深度，未成交部分会重新判断。</p>
    <p>开启后钱包全部新持仓自动加入，包括外部买入；现有持仓请逐笔加入。外部持仓采用公开平均买价成本，不含未记录的历史买入费用；成本缺失或数据不一致时暂停。首次止盈成交后，同方向不再自动买回。</p>
    {error && <div className="pcFormError" role="alert">{error}</div>}
    {data && <>
      <p role="status">{data.reason}{data.last_checked_at && ` · 最近检查 ${formatBeijing(data.last_checked_at)}`}</p>
      <SettingsForm key={`${data.wallet}:${data.enabled}:${data.threshold_percent}`} data={data}
        disabled={!ready || saving} onSave={(enabled, threshold) => update(ROOT, { wallet: data.wallet, enabled, threshold_percent: threshold })} />
      {ready && holdings.length > 0 && <div className="walletTableScroll"><table className="positionsTable"><thead><tr>
        <th>现有持仓 / 方向</th><th>净回款门槛</th><th>保护状态</th><th>操作</th>
      </tr></thead><tbody>{holdings.map((holding) => {
        const protection = data.protections?.find((item) => item.asset_id === holding.asset_id);
        return <tr key={holding.asset_id}><td>{holding.title}<small>{holding.outcome}</small></td>
          <td>{formatUsdc(Number(holding.available_size) * Number(data.threshold_percent) / 100)}<small>按当前可卖份额估算</small></td>
          <td>{protection?.enabled ? data.enabled ? "已加入保护" : "已加入 · 总开关关闭" : "未加入"}
            {protection?.reason && <small>{protection.reason}</small>}
            {protection?.rebuy_blocked && <small>已止盈，不再自动买回</small>}</td>
          <td><button className="pcButton ghost" disabled={saving}
            aria-label={`${protection?.enabled ? "退出" : "加入"}止盈保护 ${holding.title} ${holding.outcome}`}
            onClick={() => void update(`/api/execution-account/positions/${encodeURIComponent(holding.asset_id)}/take-profit`, {
              wallet: data.wallet, enabled: !protection?.enabled,
            })}>{protection?.enabled ? "退出保护" : "加入保护"}</button></td></tr>;
      })}</tbody></table></div>}
    </>}
  </section>;
}

function SettingsForm({ data, disabled, onSave }: { data: TakeProfitSettings; disabled: boolean;
  onSave: (enabled: boolean, threshold: string) => Promise<void> }) {
  const [enabled, setEnabled] = useState(data.enabled);
  const [threshold, setThreshold] = useState(data.threshold_percent);
  return <form className="takeProfitForm" onSubmit={(event) => { event.preventDefault(); void onSave(enabled, threshold); }}>
    <label><input type="checkbox" checked={enabled} disabled={disabled} onChange={(event) => setEnabled(event.target.checked)} />启用自动止盈（满足条件即真实卖出）</label>
    <label>兑付比例阈值（%）<input aria-label="兑付比例阈值" type="number" min="0.01" max="99.99" step="0.01" required
      value={threshold} disabled={disabled} onChange={(event) => setThreshold(event.target.value)} /></label>
    <button className="pcButton primary" disabled={disabled} type="submit">保存止盈设置</button>
    <span>例如最高兑付 70 美元，净回款达到 {Number.isFinite(Number(threshold)) ? formatUsdc(70 * Number(threshold) / 100) : "—"} 时触发。</span>
  </form>;
}

export function TakeProfitStatisticsPanel() {
  const [page, setPage] = useState(0);
  return <TakeProfitStatisticsPage key={page} page={page} setPage={setPage} />;
}

function TakeProfitStatisticsPage({ page, setPage }: { page: number; setPage: (page: number) => void }) {
  const { data, error, loading, reload } = useTakeProfitData<TakeProfitStatistics>(`${ROOT}/statistics?limit=20&offset=${page * 20}`);
  const labels: Record<string, string> = { settled: "已结算比较", pending_resolution: "待市场结算",
    pending_reconciliation: "成交 / 费用待核对", no_fill: "未成交" };
  return <section className="pcPanel takeProfitPanel" aria-label="自动止盈效果">
    <header className="pcPanelHeader"><h2>自动止盈效果</h2><button className="pcButton ghost" disabled={loading} onClick={() => void reload()}>刷新止盈统计</button></header>
    <p>当前钱包全部自动止盈记录（含外部买入），与同份额持有到最终结算比较。保住回款不是交易利润；未结算与费用待核对记录不计入净影响。此处为全部时间统计，不受流水日期筛选影响。</p>
    <p>止盈利润按记录成本计算：本系统持仓沿用交易账本，外部持仓采用公开平均买价，不含未记录的历史买入费用。保住回款和少赚金额均扣除实际卖出费用。</p>
    {error && <div className="pcFormError" role="alert">{error}</div>}
    {data?.summary && <>
      <div className="whaleRecordMetrics">
        {([ ["已实现止盈利润", data.summary.realized_profit], ["累计保住回款", data.summary.saved],
          ["累计少赚金额", data.summary.foregone], ["提前卖出净影响", data.summary.net_impact] ] as const).map(([label, value]) =>
          <div className="takeProfitMetric" key={label}><span>{label}</span><strong>{formatUsdc(value)}</strong></div>)}
      </div>
      <p>已确认成交 {data.summary.filled_count} 笔 · 待市场结算 {data.summary.pending_resolution_count} 笔 · 成交或费用待核对 {data.summary.pending_reconciliation_count} 笔</p>
      <div className="walletTableScroll"><table className="positionsTable"><thead><tr>
        <th>时间 / 市场</th><th>阈值 / 实际卖出</th><th>净回款 / 成本 / 利润</th><th>持有到结算回款</th><th>保住 / 少赚</th><th>状态</th>
      </tr></thead><tbody>{data.items.map((item) => <tr key={item.order_id}>
        <td>{formatBeijing(item.created_at)}<small>{item.title} · {item.outcome}</small></td>
        <td>{Number(item.threshold_percent)}%<small>{item.filled_size} 份</small></td>
        <td>{money(item.net_proceeds)}<small>{item.cost_source === "public_average" ? "公开均价成本" : "账本成本"} {money(item.cost)} · 利润 {money(item.realized_profit)}</small></td>
        <td>{item.hypothetical_payout === null ? "待结算 / 核对" : formatUsdc(item.hypothetical_payout)}</td>
        <td>{item.saved === null ? "—" : formatUsdc(item.saved)} / {item.foregone === null ? "—" : formatUsdc(item.foregone)}</td>
        <td>{labels[item.status] || item.status}{item.resolved_at && <small>{formatBeijing(item.resolved_at)}</small>}</td>
      </tr>)}</tbody></table></div>
      {!data.items.length && <p className="walletEmpty">暂无自动止盈记录</p>}
      <div className="takeProfitPagination"><button className="pcButton ghost" disabled={page === 0 || loading} onClick={() => setPage(page - 1)}>上一页</button>
        <span>第 {page + 1} 页 · 共 {data.total} 条</span>
        <button className="pcButton ghost" disabled={(page + 1) * 20 >= data.total || loading} onClick={() => setPage(page + 1)}>下一页</button></div>
    </>}
  </section>;
}
