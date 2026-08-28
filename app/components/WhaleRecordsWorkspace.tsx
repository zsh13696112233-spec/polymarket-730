"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { PolyCopyShell } from "./PolyCopyShell";
import {
  ApiError,
  ModalShell,
  WhaleOrder,
  WhalePosition,
  WhalePositionList,
  WhaleRecord,
  WhaleRecordList,
  WhaleRecordSummary,
  WhaleSellPreview,
  formatBeijing,
  formatPercent,
  formatPrice,
  formatUsdc,
  marketUrl,
  numeric,
  pnlClass,
  shortAddress,
  transactionUrl,
  whaleApi,
} from "./WhaleShared";

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
  win_rate_percent: null,
  average_profit_ratio_percent: null,
};

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    opening: "开仓中",
    open: "持有中",
    closing: "平仓中",
    closed: "已卖出",
    redeeming: "赎回中",
    redeemed: "已赎回",
    resolved_loss: "结算亏损",
  };
  return labels[status] || status;
}

function statusTone(status: string) {
  if (["open", "redeemed"].includes(status)) return "success";
  if (["opening", "closing", "redeeming"].includes(status)) return "processing";
  if (status === "resolved_loss") return "danger";
  return "neutral";
}

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
    auto_follow: "自动跟单",
    conflict_exit: "分歧风控",
    auto_redeem: "自动结算",
    reconciliation: "自动对账",
  };
  return labels[source] || source;
}

export default function WhaleRecordsWorkspace() {
  const [positions, setPositions] = useState<WhalePosition[]>([]);
  const [records, setRecords] = useState<WhaleRecord[]>([]);
  const [summary, setSummary] = useState<WhaleRecordSummary>(EMPTY_SUMMARY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [appliedStart, setAppliedStart] = useState("");
  const [appliedEnd, setAppliedEnd] = useState("");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [positionDetails, setPositionDetails] = useState<Record<number, WhalePosition>>({});
  const [detailLoading, setDetailLoading] = useState<Set<number>>(new Set());
  const [sellTarget, setSellTarget] = useState<WhalePosition | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    const params = new URLSearchParams({ limit: "200", offset: "0" });
    if (appliedStart) params.set("start_date", appliedStart);
    if (appliedEnd) params.set("end_date", appliedEnd);
    try {
      const [positionResponse, recordResponse] = await Promise.all([
        whaleApi<WhalePositionList>("/api/whales/positions?status=all&limit=200&offset=0"),
        whaleApi<WhaleRecordList>(`/api/whales/records?${params.toString()}`),
      ]);
      setPositions(positionResponse.items || []);
      setRecords(recordResponse.items || []);
      setSummary({ ...EMPTY_SUMMARY, ...(recordResponse.summary || {}) });
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "巨鲸跟单记录加载失败");
    } finally {
      setLoading(false);
    }
  }, [appliedEnd, appliedStart]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadData(), 0);
    return () => window.clearTimeout(timer);
  }, [loadData]);

  const openPositions = useMemo(
    () => positions.filter((position) => ["opening", "open", "closing", "redeeming"].includes(position.status)),
    [positions],
  );

  const togglePosition = async (position: WhalePosition) => {
    const willOpen = !expanded.has(position.id);
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(position.id)) next.delete(position.id);
      else next.add(position.id);
      return next;
    });
    if (!willOpen || positionDetails[position.id]) return;
    setDetailLoading((current) => new Set(current).add(position.id));
    try {
      const detail = await whaleApi<WhalePosition>(`/api/whales/positions/${position.id}`);
      setPositionDetails((current) => ({ ...current, [position.id]: detail }));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "逐仓明细加载失败");
    } finally {
      setDetailLoading((current) => {
        const next = new Set(current);
        next.delete(position.id);
        return next;
      });
    }
  };

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
        <Metric label="已结束胜率" value={formatPercent(summary.win_rate_percent)} hint={`${summary.win_count} 胜 / ${summary.loss_count} 负`} />
        <Metric label="平均盈利比" value={formatPercent(summary.average_profit_ratio_percent)} tone={pnlClass(summary.average_profit_ratio_percent)} hint="已结束仓位口径" />
      </section>

      {error && (
        <div className="pcAlert danger whalePageError" role="alert">
          <strong>记录读取未完成</strong><p>{error}</p><button type="button" onClick={() => void loadData()}>重试</button>
        </div>
      )}

      <section className="pcPanel whalePositionsPanel">
        <header className="pcPanelHeader">
          <div><span className="pcEyebrow">OPEN POSITIONS</span><h2>当前持仓</h2><p>市值按当前买一价估算；盘口缺失时不会用 0 代替。</p></div>
          <span className="pcPanelCount">{openPositions.length} 个仓位</span>
        </header>
        {loading && !positions.length ? (
          <div className="pcLoading whaleLoading">正在读取链路记录…</div>
        ) : openPositions.length ? (
          <div className="pcTableWrap">
            <table className="pcTable whalePositionTable">
              <thead><tr><th>市场 / 方向</th><th>跟随巨鲸</th><th className="numeric">持有份额</th><th className="numeric">买入均价</th><th className="numeric">持仓成本</th><th className="numeric">现价 / 市值</th><th className="numeric">浮动盈亏</th><th>开仓时间</th><th>状态 / 操作</th></tr></thead>
              <tbody>
                {openPositions.map((position) => {
                  const isOpen = expanded.has(position.id);
                  const detail = positionDetails[position.id];
                  const ledger = detail?.ledger ?? detail?.records ?? [];
                  return (
                    <PositionRows
                      key={position.id}
                      position={position}
                      isOpen={isOpen}
                      detailLoading={detailLoading.has(position.id)}
                      ledger={ledger}
                      onToggle={() => void togglePosition(position)}
                      onSell={() => setSellTarget(position)}
                    />
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="pcEmptyState whaleCompactEmpty"><div className="pcEmptyIcon">◇</div><h2>暂无巨鲸跟单持仓</h2><p>从巨鲸发现页完成一次真实买入后，持仓会出现在这里。</p><Link className="pcButton primary" href="/whales">浏览巨鲸市场</Link></div>
        )}
      </section>

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

      {sellTarget && (
        <WhaleSellModal
          position={sellTarget}
          onClose={() => setSellTarget(null)}
          onCompleted={() => void loadData()}
        />
      )}
    </PolyCopyShell>
  );
}

function Metric({ label, value, hint, tone = "" }: { label: string; value: string; hint: string; tone?: string }) {
  return <article className="pcMetricCard"><span>{label}</span><strong className={tone}>{value}</strong><small>{hint}</small></article>;
}

function PositionRows({
  position,
  isOpen,
  detailLoading,
  ledger,
  onToggle,
  onSell,
}: {
  position: WhalePosition;
  isOpen: boolean;
  detailLoading: boolean;
  ledger: WhaleRecord[];
  onToggle: () => void;
  onSell: () => void;
}) {
  const unavailable = position.valuation_status === "unavailable";
  return (
    <>
      <tr>
        <td>
          <a className="pcMarketIdentity" href={marketUrl(position.event_slug || position.market_slug)} target="_blank" rel="noreferrer">
            <strong>{position.title}</strong><span>{position.outcome} ↗</span>
          </a>
          <button className="whalePositionToggle" type="button" aria-expanded={isOpen} onClick={onToggle}>{isOpen ? "收起完整流水" : "展开完整流水"}</button>
        </td>
        <td><strong>{shortAddress(position.source_wallet)}</strong><small>巨鲸均价 {formatPrice(position.source_whale_avg_price)}</small></td>
        <td className="numeric"><strong>{numeric(position.size).toFixed(4)}</strong></td>
        <td className="numeric"><strong>{formatPrice(position.avg_cost_price)}</strong></td>
        <td className="numeric"><strong>{formatUsdc(position.cost_usdc)}</strong></td>
        <td className="numeric"><strong>{unavailable ? "—" : formatPrice(position.current_price)}</strong><small>{unavailable ? "无法估值" : formatUsdc(position.market_value_usdc)}</small></td>
        <td className="numeric"><strong className={unavailable ? "" : pnlClass(position.unrealized_pnl)}>{unavailable ? "—" : formatUsdc(position.unrealized_pnl)}</strong><small>已实现 {formatUsdc(position.realized_pnl)}</small></td>
        <td><strong>{formatBeijing(position.opened_at)}</strong><small>{position.closed_at ? `结束 ${formatBeijing(position.closed_at)}` : "尚未退出"}</small></td>
        <td><span className={`pcBadge ${statusTone(position.status)}`}>{statusLabel(position.status)}</span>{position.status === "open" && <button className="pcButton danger whaleSellButton" type="button" onClick={onSell}>一键卖出</button>}</td>
      </tr>
      {isOpen && (
        <tr className="whalePositionDetailRow"><td colSpan={9}>
          {detailLoading ? <div className="whaleTradeLoading">正在还原逐仓流水…</div> : <PositionTimeline position={position} ledger={ledger} />}
        </td></tr>
      )}
    </>
  );
}

function PositionTimeline({ position, ledger }: { position: WhalePosition; ledger: WhaleRecord[] }) {
  if (!ledger.length) return <div className="whaleTradeLoading">该仓位暂无可展开的流水明细。</div>;
  const invested = ledger.filter((item) => item.type === "buy").reduce((sum, item) => sum + numeric(item.amount_usdc), 0);
  const recovered = ledger.filter((item) => ["sell", "redeem"].includes(item.type)).reduce((sum, item) => sum + numeric(item.amount_usdc), 0);
  return (
    <div className="whalePositionTimeline">
      <div className="whaleTimelineList">
        {ledger.map((record, index) => (
          <div className="whaleTimelineItem" key={record.id}>
            <span className={`whaleTimelineDot ${recordTypeTone(record.type)}`}>{index + 1}</span>
            <div><strong>{recordSourceLabel(record.source)} · {recordTypeLabel(record.type)} · {formatBeijing(record.timestamp, true)}</strong><p>{numeric(record.size).toFixed(4)} 份 × {formatPrice(record.price)} · 金额 {formatUsdc(record.amount_usdc)} · 费用 {formatUsdc(record.fee_usdc)}</p>{record.detail && <small>{record.detail}</small>}</div>
            <b className={pnlClass(record.realized_pnl)}>{numeric(record.realized_pnl) === 0 ? "—" : formatUsdc(record.realized_pnl)}</b>
          </div>
        ))}
      </div>
      <div className="whalePositionConclusion"><span>仓位结论</span><strong>投入 {formatUsdc(invested)} · 回收 {formatUsdc(recovered)}</strong><b className={pnlClass(position.total_pnl)}>净盈亏 {formatUsdc(position.total_pnl)}</b></div>
    </div>
  );
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

function WhaleSellModal({ position, onClose, onCompleted }: { position: WhalePosition; onClose: () => void; onCompleted: () => void }) {
  const [sellAll, setSellAll] = useState(true);
  const [size, setSize] = useState(String(position.size));
  const [preview, setPreview] = useState<WhaleSellPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [order, setOrder] = useState<WhaleOrder | null>(null);

  const requestPreview = useCallback(async () => {
    const sizeValue = Number(size);
    if (!sellAll && (!Number.isFinite(sizeValue) || sizeValue <= 0 || sizeValue > numeric(position.size))) {
      setPreview(null);
      setError("卖出份额必须大于 0，且不能超过当前持仓");
      return;
    }
    setPreviewing(true);
    setError(null);
    try {
      setPreview(await whaleApi<WhaleSellPreview>(`/api/whales/positions/${position.id}/sell/preview`, { method: "POST", body: JSON.stringify(sellAll ? { sell_all: true } : { size: sizeValue, sell_all: false }) }));
    } catch (requestError) {
      setPreview(null);
      setError(requestError instanceof Error ? requestError.message : "卖出预览失败");
    } finally {
      setPreviewing(false);
    }
  }, [position.id, position.size, sellAll, size]);

  useEffect(() => {
    const timer = window.setTimeout(() => void requestPreview(), 400);
    return () => window.clearTimeout(timer);
  }, [requestPreview]);

  const execute = async () => {
    if (!preview || confirmation !== "确认真实卖出") return;
    setExecuting(true);
    setError(null);
    try {
      const result = await whaleApi<WhaleOrder>(`/api/whales/positions/${position.id}/sell/execute`, { method: "POST", body: JSON.stringify({ confirmation_id: preview.confirmation_id, confirmation_text: confirmation }) });
      setOrder(result);
      onCompleted();
    } catch (requestError) {
      setConfirmation("");
      if (requestError instanceof ApiError && requestError.status === 409) {
        setError(`${requestError.message}，正在重新获取盘口。`);
        setPreview(null);
        await requestPreview();
      } else {
        setError(requestError instanceof Error ? requestError.message : "真实卖出未完成");
      }
    } finally {
      setExecuting(false);
    }
  };

  return (
    <ModalShell
      title="卖出巨鲸跟单持仓"
      eyebrow="MANUAL EXIT"
      onClose={onClose}
      className="whaleSellModal"
      footer={order ? <button className="pcButton primary" type="button" onClick={onClose}>完成</button> : <><button className="pcButton ghost" type="button" onClick={onClose}>取消</button><button className="pcButton danger" type="button" onClick={execute} disabled={!preview || previewing || executing || confirmation !== "确认真实卖出"}>{executing ? "提交中" : "确认卖出"}</button></>}
    >
      <div className="whaleFollowMarket"><span className="whaleOutcomeMark negative">{position.outcome}</span><div><strong>{position.title}</strong><span>持有 {numeric(position.size).toFixed(4)} 份 · 成本 {formatUsdc(position.cost_usdc)}</span></div></div>
      {order ? (
        <div className="whaleExecutionResult"><span className={`pcBadge ${numeric(order.filled_size) > 0 ? "success" : "warning"}`}>{order.status}</span><h3>{numeric(order.filled_size) > 0 ? "卖出成交已记录" : "订单已提交"}</h3><dl><div><dt>成交份额</dt><dd>{numeric(order.filled_size).toFixed(4)}</dd></div><div><dt>成交金额</dt><dd>{formatUsdc(order.filled_usdc)}</dd></div><div><dt>手续费</dt><dd>{formatUsdc(order.fee_usdc)}</dd></div></dl>{order.reason && <p>{order.reason}</p>}</div>
      ) : (
        <>
          <label className="whaleSellAll"><input type="checkbox" checked={sellAll} onChange={(event) => setSellAll(event.target.checked)} /><span>卖出全部持仓</span></label>
          <label className="whaleAmountInput"><span>卖出份额 <small>最多 {numeric(position.size).toFixed(4)}</small></span><div><input aria-label="卖出份额" type="number" min="0" max={numeric(position.size)} step="0.0001" disabled={sellAll} value={size} onChange={(event) => setSize(event.target.value)} /><b>份</b></div></label>
          {previewing && <div className="whalePreviewLoading">正在读取实时买一价并估算回收…</div>}
          {error && <div className="pcFormError" role="alert">{error}</div>}
          {preview && !previewing && <div className="whaleFollowPreview"><div className="whalePreviewHero"><span>预计本次卖出盈亏</span><strong className={pnlClass(preview.estimated_pnl_usdc)}>{formatUsdc(preview.estimated_pnl_usdc)}</strong><small>{formatPercent(preview.estimated_pnl_percent)} · 卖出价下限 {formatPrice(preview.worst_price)}</small></div><dl className="whalePreviewGrid"><div><dt>卖出份额</dt><dd>{numeric(preview.size).toFixed(4)}</dd></div><div><dt>预计回收</dt><dd>{formatUsdc(preview.estimated_proceeds_usdc)}</dd></div><div><dt>预估手续费</dt><dd>{formatUsdc(preview.estimated_fee_usdc)}</dd></div><div><dt>结转成本</dt><dd>{formatUsdc(preview.cost_basis_usdc)}</dd></div></dl><label className="whaleConfirmation"><span>输入固定文案后才能执行真实订单</span><input aria-label="真实卖出确认文案" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} placeholder="确认真实卖出" autoComplete="off" /></label></div>}
        </>
      )}
    </ModalShell>
  );
}
