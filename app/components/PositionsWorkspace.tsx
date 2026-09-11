"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { PolyCopyShell } from "./PolyCopyShell";
import { useVisibleAutoRefresh } from "./useVisibleAutoRefresh";
import { ModalShell, whaleApi, formatUsdc, formatPrice } from "./WhaleShared";

type Position = {
  asset_id: string; title: string; outcome: string; size: string;
  reserved_size: string; available_size: string; price: string | null;
  market_value: string | null; cost: string | null; pnl: string | null;
  status: string; reason: string | null;
};
type Order = {
  id: number | null; external_order_id: string | null; asset_id: string;
  title: string; outcome: string; order_type: string; price: string;
  size: string; filled_size: string; status: string; can_cancel: boolean; reason: string | null;
};
type Portfolio = { wallet: string; positions: Position[]; orders: Order[]; warning: string | null };
type SellPreview = {
  confirmation_id: string; expires_at: string; wallet: string; asset_id: string;
  title: string; outcome: string; order_type: string; size: string; price: string;
  estimated_proceeds: string; estimated_fee: string; estimated_pnl: string | null;
};
const ROOT = "/api/execution-account";
const statusLabels: Record<string, string> = {
  open: "可交易", settled: "已结算", unavailable: "待核对",
  planned: "准备提交", signed: "提交待核对", submitted: "已提交", live: "挂单中",
  partially_filled_live: "部分成交 · 余量挂单中", partially_filled: "部分成交 · 余量已取消",
  reconciliation_pending: "待对账", matched: "成交待结算", filled: "已成交",
  cancelled: "已撤销", canceled: "已撤销", blocked: "未提交", rejected: "已拒绝",
  unfilled: "未成交", expired: "已过期", delayed: "等待撮合",
};
const label = (status: string) => statusLabels[status] ?? status;
const money = (value: string | null) => value === null ? "未知" : formatUsdc(value);

export default function PositionsWorkspace() {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sell, setSell] = useState<Position | null>(null);
  const running = useRef(false);
  const refresh = useCallback(async () => {
    if (running.current || document.visibilityState === "hidden") return;
    running.current = true;
    setLoading(true);
    try {
      setPortfolio(await whaleApi<Portfolio>(`${ROOT}/positions`));
      setError(null);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "持仓加载失败");
    } finally {
      running.current = false;
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);
  useVisibleAutoRefresh(refresh, 10_000);
  const positions = portfolio?.positions.filter((position) => position.status !== "settled") ?? [];
  return (
    <PolyCopyShell active="positions" title="持仓管理"
      actions={<button className="pcButton ghost" disabled={loading} onClick={() => void refresh()}>{loading ? "刷新中…" : "刷新持仓"}</button>}>
      <div className="walletPositions">
        {error && <div className="pcFormError" role="alert">{error}；刷新成功前暂停操作。</div>}
        {portfolio?.warning && <div className="pcFormError" role="status">{portfolio.warning}</div>}
        <section className="pcPanel walletPositionPanel"><h2>当前持仓</h2>
          <p>可卖份额已扣除挂单占用。市值按买一价估算；缺失价格或成本时显示未知。</p>
          <div className="walletTableScroll"><table className="positionsTable walletHoldingsTable"><thead><tr>
            <th>市场 / 方向</th><th className="walletNumeric">持有 / 占用 / 可卖</th><th className="walletNumeric">现价 / 市值</th><th className="walletNumeric">成本 / 浮动盈亏</th><th className="walletStatusColumn">状态</th><th className="walletActionColumn">操作</th>
          </tr></thead><tbody>{positions.map((position) => <tr key={position.asset_id}>
            <td><strong className="walletMarketTitle">{position.title}</strong><small><span className="walletOutcome">{position.outcome}</span></small></td>
            <td className="walletNumeric"><strong className="walletMetric">{position.size}</strong><small>占用 {position.reserved_size} · 可卖 {position.available_size}</small></td>
            <td className="walletNumeric"><strong className="walletMetric">{position.price === null ? "未知" : formatPrice(position.price)}</strong><small>{money(position.market_value)}</small></td>
            <td className="walletNumeric"><strong className="walletMetric">{money(position.cost)}</strong><small className={position.pnl === null ? "" : Number(position.pnl) < 0 ? "walletLoss" : Number(position.pnl) > 0 ? "walletProfit" : ""}>{money(position.pnl)}</small></td>
            <td className="walletStatusColumn"><span className={`walletPositionStatus ${position.status}`}>{label(position.status)}</span>{position.reason && <small className="walletPositionReason">{position.reason}</small>}</td>
            <td className="walletActionColumn">
              {position.status === "open" && <button className="pcButton danger" disabled={!!error || Number(position.available_size) <= 0} onClick={() => setSell(position)}>一键卖出</button>}
            </td>
          </tr>)}</tbody></table></div>
          {!positions.length && <p className="walletEmpty">{loading ? "正在核对持仓…" : "暂无未结算持仓"}</p>}
        </section>
        <details className="pcPanel walletPositionPanel"><summary>卖出订单</summary>
          <div className="walletTableScroll"><table className="positionsTable"><thead><tr><th>市场 / 方向</th><th>委托 / 已成交</th><th>状态</th></tr></thead>
            <tbody>{portfolio?.orders.filter((order) => order.id !== null).map((order) => <tr key={order.id}>
              <td>{order.title}<small>{order.outcome} · #{order.id}</small></td>
              <td>{order.size}<small>已成交 {order.filled_size}</small></td>
              <td>{label(order.status)}{order.reason && <small>{order.reason}</small>}</td>
            </tr>)}</tbody>
          </table></div>
          {!portfolio?.orders.some((order) => order.id !== null) && <p className="walletEmpty">暂无卖出订单</p>}
        </details>
      </div>
      {sell && <SellDialog key={sell.asset_id} position={sell} onClose={() => setSell(null)} onCompleted={refresh} />}
    </PolyCopyShell>
  );
}

function SellDialog({ position, onClose, onCompleted }: { position: Position; onClose: () => void; onCompleted: () => Promise<void> }) {
  const [preview, setPreview] = useState<SellPreview | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Order | null>(null);
  const submitting = useRef(false);
  const previewRequest = useRef<AbortController | null>(null);
  const requestPreview = useCallback(async () => {
    previewRequest.current?.abort();
    const controller = new AbortController();
    previewRequest.current = controller;
    setBusy(true); setError(null); setPreview(null);
    try {
      const value = await whaleApi<SellPreview>(`${ROOT}/positions/${encodeURIComponent(position.asset_id)}/sell/preview`, {
        method: "POST", body: JSON.stringify({ order_type: "FAK", sell_all: true }), signal: controller.signal,
      });
      if (!controller.signal.aborted) setPreview(value);
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "卖出信息读取失败");
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }, [position.asset_id]);
  useEffect(() => {
    const timer = window.setTimeout(() => void requestPreview(), 0);
    return () => { window.clearTimeout(timer); previewRequest.current?.abort(); };
  }, [requestPreview]);
  const execute = async () => {
    if (submitting.current || !preview) return;
    if (Date.parse(preview.expires_at) <= Date.now()) {
      setPreview(null); setError("价格已过期，请重新获取卖出信息"); return;
    }
    submitting.current = true;
    setBusy(true); setError(null);
    try {
      setResult(await whaleApi<Order>(`${ROOT}/positions/${encodeURIComponent(position.asset_id)}/sell/execute`, {
        method: "POST", body: JSON.stringify({ confirmation_id: preview.confirmation_id, confirmation_text: "确认真实卖出" }),
      }));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "提交结果待确认，请刷新订单");
    } finally {
      setPreview(null); submitting.current = false; setBusy(false); void onCompleted();
    }
  };
  return <ModalShell title="确认卖出全部可卖份额" onClose={() => { if (!submitting.current) onClose(); }}
    footer={result ? <button className="pcButton primary" onClick={onClose}>完成</button> : <>
      <button className="pcButton ghost" disabled={busy} onClick={onClose}>取消</button>
      {error && !preview && <button className="pcButton ghost" disabled={busy} onClick={() => void requestPreview()}>重新获取</button>}
      <button className="pcButton danger" disabled={busy || !preview} onClick={() => void execute()}>{busy ? "处理中…" : "确认真实卖出"}</button>
    </>}>
    <div className="walletSellForm"><strong>{position.title} · {position.outcome}</strong>
      <p>按当前盘口卖出全部可卖份额，未成交部分自动取消。</p>
      {result ? <p role="status">{label(result.status)} · 已成交 {result.filled_size} 份{result.reason && ` · ${result.reason}`}</p> : preview ?
        <div className="walletPreview"><p>卖出 {preview.size} 份 · 价格下限 {formatPrice(preview.price)}</p>
          <p>预计成交金额 {money(preview.estimated_proceeds)} · 预估手续费 {money(preview.estimated_fee)}</p>
          <p>预估盈亏 {money(preview.estimated_pnl)}</p>
        </div> : busy && <p role="status">正在获取可卖份额和实时价格…</p>}
      {error && <div className="pcFormError" role="alert">{error}</div>}
    </div>
  </ModalShell>;
}
