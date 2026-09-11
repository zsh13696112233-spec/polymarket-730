"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { PolyCopyShell } from "./PolyCopyShell";
import EmailSettingsPanel from "./EmailSettingsPanel";

const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8730").replace(/\/$/, "");

type Account = {
  signer_address: string | null;
  funder_address: string | null;
  signature_type: 1 | 3;
  credentials_configured: boolean;
  status: string;
  budget_usdc: number;
  cash_reserve_usdc: number;
  max_total_exposure_usdc: number;
  daily_buy_limit_usdc: number;
  daily_loss_limit_usdc: number;
  auto_redeem: boolean;
  collateral_balance: number | null;
  last_balance_at: string | null;
  last_error: string | null;
};

type Notice = {
  kind: "success" | "error";
  text: string;
};

type ChainTestOutcome = {
  asset_id: string;
  label: string;
  outcome_index: number;
  reference_price: number;
};

type ChainTestMarket = {
  condition_id: string;
  title: string;
  market_slug: string | null;
  event_slug: string | null;
  closed: boolean;
  active: boolean;
  accepting_orders: boolean;
  outcomes: ChainTestOutcome[];
};

type ChainTestResolution = {
  resolution_id: string;
  expires_at: string;
  market_url: string;
  event_title: string;
  markets: ChainTestMarket[];
};

type ChainTestBuyPreview = {
  confirmation_id: string;
  title: string;
  outcome: string;
  amount_usdc: number;
  best_ask: number;
  worst_price: number;
  minimum_order_usdc: number;
  estimated_shares: number;
  estimated_fee_usdc: number;
  total_cost_usdc: number;
  immediate_exit_price: number | null;
  immediate_exit_proceeds_usdc: number | null;
  immediate_exit_pnl_usdc: number | null;
  immediate_exit_unavailable_reason: string | null;
  available_balance_usdc: number;
  reserve_warning: boolean;
};

type ChainTestSellPreview = {
  confirmation_id: string;
  position_id: number;
  size: number;
  best_bid: number;
  worst_price: number;
  minimum_order_size: number;
  estimated_proceeds_usdc: number;
  estimated_fee_usdc: number;
  cost_basis_usdc: number;
  estimated_pnl_usdc: number;
  estimated_pnl_percent: number | null;
};

type ChainTestOrder = {
  id: number;
  position_id: number | null;
  side: "BUY" | "SELL";
  title: string;
  outcome: string;
  filled_size: number;
  filled_usdc: number;
  fee_usdc: number;
  status: string;
  reason: string | null;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}) },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.detail || `请求失败（${response.status}）`);
  return payload as T;
}

function money(value: number | null) {
  return value === null ? "—" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);
}

function price(value: number | null) {
  return value === null ? "—" : value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}

export default function ExecutionSettingsWorkspace() {
  const [account, setAccount] = useState<Account | null>(null);
  const [signer, setSigner] = useState("");
  const [funder, setFunder] = useState("");
  const [cashReserve, setCashReserve] = useState("240");
  const [localAutoRedeem, setLocalAutoRedeem] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [chainUrl, setChainUrl] = useState("");
  const [chainResolution, setChainResolution] = useState<ChainTestResolution | null>(null);
  const [chainAsset, setChainAsset] = useState("");
  const [chainAmount, setChainAmount] = useState("5");
  const [chainBuyPreview, setChainBuyPreview] = useState<ChainTestBuyPreview | null>(null);
  const [chainBuyOrder, setChainBuyOrder] = useState<ChainTestOrder | null>(null);
  const [chainSellPreview, setChainSellPreview] = useState<ChainTestSellPreview | null>(null);
  const [chainSellOrder, setChainSellOrder] = useState<ChainTestOrder | null>(null);
  const [chainBusy, setChainBusy] = useState(false);
  const [chainNotice, setChainNotice] = useState<Notice | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await request<Account | null>("/api/execution-account");
      setAccount(next);
      if (next) {
        setSigner(next.signer_address ?? "");
        setFunder(next.funder_address ?? "");
        setCashReserve(String(next.cash_reserve_usdc));
        setLocalAutoRedeem(next.auto_redeem);
      }
    } catch (error) {
      setNotice({
        kind: "error",
        text: error instanceof Error ? error.message : "无法读取执行钱包",
      });
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setNotice(null);
    try {
      const reserve = Number(cashReserve);
      await request<Account>("/api/execution-account", {
        method: "PUT",
        body: JSON.stringify({
          signer_address: signer,
          funder_address: funder,
          signature_type: 3,
          budget_usdc: Math.max(400, reserve),
          cash_reserve_usdc: reserve,
          max_total_exposure_usdc: 160,
          daily_buy_limit_usdc: 80,
          daily_loss_limit_usdc: 40,
          auto_redeem: localAutoRedeem,
        }),
      });
      setNotice({ kind: "success", text: "执行钱包配置已保存。" });
      await load();
    } catch (error) {
      setNotice({
        kind: "error",
        text: error instanceof Error ? error.message : "保存失败",
      });
    } finally { setBusy(false); }
  }

  async function action(path: string, success: string) {
    setBusy(true); setNotice(null);
    try {
      await request<Account>(path, { method: "POST" });
      setNotice({ kind: "success", text: success });
      await load();
    } catch (error) {
      setNotice({
        kind: "error",
        text: error instanceof Error ? error.message : "操作失败",
      });
    } finally { setBusy(false); }
  }

  function resetChainTrade() {
    setChainBuyPreview(null);
    setChainBuyOrder(null);
    setChainSellPreview(null);
    setChainSellOrder(null);
  }

  async function resolveChainMarket() {
    setChainBusy(true); setChainNotice(null); resetChainTrade();
    try {
      const next = await request<ChainTestResolution>("/api/execution-account/chain-test/resolve", {
        method: "POST",
        body: JSON.stringify({ market_url: chainUrl }),
      });
      setChainResolution(next);
      setChainAsset("");
      setChainNotice({ kind: "success", text: `已识别 ${next.markets.length} 个市场，请选择要测试的 outcome。` });
    } catch (error) {
      setChainResolution(null); setChainAsset("");
      setChainNotice({ kind: "error", text: error instanceof Error ? error.message : "市场链接解析失败" });
    } finally { setChainBusy(false); }
  }

  async function previewChainBuy() {
    if (!chainResolution || !chainAsset) return;
    setChainBusy(true); setChainNotice(null); setChainBuyPreview(null); setChainSellPreview(null);
    try {
      const preview = await request<ChainTestBuyPreview>("/api/execution-account/chain-test/buy/preview", {
        method: "POST",
        body: JSON.stringify({
          resolution_id: chainResolution.resolution_id,
          asset_id: chainAsset,
          amount_usdc: Number(chainAmount),
        }),
      });
      setChainBuyPreview(preview);
    } catch (error) {
      setChainNotice({ kind: "error", text: error instanceof Error ? error.message : "买入预览失败" });
    } finally { setChainBusy(false); }
  }

  async function executeChainBuy() {
    if (!chainBuyPreview) return;
    setChainBusy(true); setChainNotice(null);
    try {
      const order = await request<ChainTestOrder>("/api/execution-account/chain-test/buy/execute", {
        method: "POST",
        body: JSON.stringify({
          confirmation_id: chainBuyPreview.confirmation_id,
          confirmation_text: "确认真实买入",
        }),
      });
      setChainBuyOrder(order); setChainBuyPreview(null);
      setChainNotice({
        kind: order.filled_size > 0 ? "success" : "error",
        text: order.filled_size > 0 ? "测试买入已成交并写入本地持仓。" : "买入没有立即成交，请先查看订单状态。",
      });
      await load();
    } catch (error) {
      setChainNotice({ kind: "error", text: error instanceof Error ? error.message : "真实买入失败" });
    } finally { setChainBusy(false); }
  }

  async function previewChainSell() {
    if (!chainBuyOrder?.position_id) return;
    setChainBusy(true); setChainNotice(null); setChainSellPreview(null);
    try {
      const preview = await request<ChainTestSellPreview>(
        `/api/execution-account/chain-test/orders/${chainBuyOrder.id}/sell/preview`,
        { method: "POST" },
      );
      setChainSellPreview(preview);
    } catch (error) {
      setChainNotice({ kind: "error", text: error instanceof Error ? error.message : "卖出预览失败" });
    } finally { setChainBusy(false); }
  }

  async function executeChainSell() {
    if (!chainSellPreview || !chainBuyOrder) return;
    setChainBusy(true); setChainNotice(null);
    try {
      const order = await request<ChainTestOrder>(
        `/api/execution-account/chain-test/orders/${chainBuyOrder.id}/sell/execute`,
        {
          method: "POST",
          body: JSON.stringify({
            confirmation_id: chainSellPreview.confirmation_id,
            confirmation_text: "确认真实卖出",
          }),
        },
      );
      setChainSellOrder(order); setChainSellPreview(null);
      setChainNotice({
        kind: order.filled_size > 0 ? "success" : "error",
        text: order.filled_size > 0 ? "测试卖出已成交，买卖链路验证完成。" : "卖出没有立即成交，请到跟单记录核对。",
      });
      await load();
    } catch (error) {
      setChainNotice({ kind: "error", text: error instanceof Error ? error.message : "真实卖出失败" });
    } finally { setChainBusy(false); }
  }

  return (
    <PolyCopyShell active="settings" title="系统设置">
      <section className="pcPanel pcExecutionAccountPanel">
        <header className="pcPanelHeader pcExecutionWalletHeader">
          <div><h2>执行钱包</h2></div>
          <div className="pcExecutionWalletMode" aria-label="钱包模式：Deposit Wallet">
            <span>钱包模式</span>
            <strong>Deposit Wallet</strong>
          </div>
        </header>
        {account && <div className="pcAccountSummary"><div><span>状态</span><strong>{account.status}</strong></div><div><span>pUSD 余额</span><strong>{money(account.collateral_balance)}</strong></div><div><span>钥匙串</span><strong>{account.credentials_configured ? "已配置" : "未配置"}</strong></div><div><span>最后更新</span><strong>{account.last_balance_at ? new Date(account.last_balance_at).toLocaleString("zh-CN") : "—"}</strong></div></div>}
        <form className="pcSettingsForm pcSystemSettingsForm" onSubmit={save}>
          <div className="pcExecutionAddressGrid">
            <label className="pcField"><span>签名钱包地址</span><input value={signer} onChange={(event) => setSigner(event.target.value)} placeholder="0x…" required /></label>
            <label className="pcField"><span>资金钱包地址</span><input value={funder} onChange={(event) => setFunder(event.target.value)} placeholder="0x…" required /></label>
          </div>
          <label className="pcExecutionRedemptionToggle">
            <input
              aria-label="启用本地主动赎回兜底"
              type="checkbox"
              checked={localAutoRedeem}
              onChange={(event) => setLocalAutoRedeem(event.target.checked)}
              disabled={busy}
            />
            <span>
              <strong>启用本地主动赎回兜底</strong>
              <small>默认由 Polymarket 线上自动赎回；仅在关闭线上功能后启用，避免重复提交。</small>
            </span>
          </label>
          {signer && !account?.credentials_configured && <div className="pcCommandHint"><span>导入执行密钥</span><code>uv run python -m backend.trading_cli set-key --account {signer}</code></div>}
          <div className="pcExecutionFormFooter">
            <label className="pcField"><span>现金保留额</span><div className="pcUnitInput"><input type="number" min="0" step="0.01" value={cashReserve} onChange={(event) => setCashReserve(event.target.value)} /><b>USDC</b></div><small>下单后余额低于该值时显示风险警告。</small></label>
            <div className="pcSettingsActions"><button className="pcButton primary" type="submit" disabled={busy}>保存配置</button><button className="pcButton ghost" type="button" disabled={busy || !account} onClick={() => void action("/api/execution-account/verify", "执行钱包验证完成。")}>验证密钥与授权</button><button className="pcButton ghost" type="button" disabled={busy || !account} onClick={() => void action("/api/execution-account/balance/refresh", "余额已刷新。")}>刷新余额</button></div>
          </div>
          {notice && <p className={notice.kind === "success" ? "pcFormSuccess" : "pcFormError"}>{notice.text}</p>}
        </form>
      </section>
      <section className="pcPanel pcChainTestPanel">
        <header className="pcPanelHeader">
          <div><h2>链上环境测试工具</h2></div>
          <span className="pcBadge warning">真实资金</span>
        </header>
        <div className="pcChainTestWarning">本工具会产生真实成交、手续费和买卖价差。请使用可承受损失的小额资金。</div>
        <div className="pcChainTestUrlRow">
          <label className="pcField"><span>Polymarket 市场链接</span><input aria-label="Polymarket 市场链接" type="url" value={chainUrl} onChange={(event) => { setChainUrl(event.target.value); setChainResolution(null); setChainAsset(""); resetChainTrade(); }} placeholder="https://polymarket.com/event/…" /></label>
          <button className="pcButton ghost" type="button" disabled={chainBusy || !chainUrl.trim()} onClick={() => void resolveChainMarket()}>{chainBusy ? "处理中" : "识别 outcome"}</button>
        </div>
        {chainResolution && (
          <div className="pcChainTestMarkets">
            <strong>{chainResolution.event_title}</strong>
            {chainResolution.markets.map((market) => {
              const tradable = market.active && !market.closed && market.accepting_orders;
              return (
                <fieldset key={market.condition_id} disabled={!tradable || chainBusy}>
                  <legend>{market.title}</legend>
                  {!tradable && <small>该市场当前不可交易</small>}
                  <div className="pcChainOutcomeGrid">
                    {market.outcomes.map((outcome) => (
                      <label key={outcome.asset_id} className={chainAsset === outcome.asset_id ? "selected" : ""}>
                        <input type="radio" name="chain-test-outcome" value={outcome.asset_id} checked={chainAsset === outcome.asset_id} onChange={() => { setChainAsset(outcome.asset_id); resetChainTrade(); }} />
                        <span><strong>{outcome.label}</strong><small>参考价 {price(outcome.reference_price)}</small></span>
                      </label>
                    ))}
                  </div>
                </fieldset>
              );
            })}
          </div>
        )}
        {chainResolution && (
          <div className="pcChainTestBuyRow">
            <label className="pcField"><span>真实买入金额</span><div className="pcUnitInput"><input aria-label="链上测试买入金额" type="number" min="0.01" step="0.01" value={chainAmount} onChange={(event) => { setChainAmount(event.target.value); resetChainTrade(); }} /><b>USDC</b></div></label>
            <button className="pcButton primary" type="button" disabled={chainBusy || !chainAsset || !(Number(chainAmount) > 0)} onClick={() => void previewChainBuy()}>预览真实买入</button>
          </div>
        )}
        {chainBuyPreview && (
          <div className="pcChainTestQuote">
            <h3>买入预览 · {chainBuyPreview.outcome}</h3>
            <dl><div><dt>当前卖价</dt><dd>{price(chainBuyPreview.best_ask)}</dd></div><div><dt>最高成交价</dt><dd>{price(chainBuyPreview.worst_price)}</dd></div><div><dt>预计份额</dt><dd>{chainBuyPreview.estimated_shares.toFixed(4)}</dd></div><div><dt>预计总成本</dt><dd>{money(chainBuyPreview.total_cost_usdc)}</dd></div><div><dt>预计手续费</dt><dd>{money(chainBuyPreview.estimated_fee_usdc)}</dd></div><div><dt>可用余额</dt><dd>{money(chainBuyPreview.available_balance_usdc)}</dd></div></dl>
            {chainBuyPreview.immediate_exit_pnl_usdc !== null ? <p>按当前盘口立即卖出预计损益：{money(chainBuyPreview.immediate_exit_pnl_usdc)}</p> : <p>{chainBuyPreview.immediate_exit_unavailable_reason}</p>}
            {chainBuyPreview.reserve_warning && <p className="pcFormError">本次买入会使余额低于现金保留额。</p>}
            <button className="pcButton danger" type="button" disabled={chainBusy} onClick={() => void executeChainBuy()}>确认真实买入 {money(chainBuyPreview.amount_usdc)}</button>
          </div>
        )}
        {chainBuyOrder && (
          <div className="pcChainTestResult">
            <h3>买入结果</h3><p>{chainBuyOrder.outcome} · 状态 {chainBuyOrder.status} · 成交 {chainBuyOrder.filled_size.toFixed(4)} 份 · {money(chainBuyOrder.filled_usdc)}</p>
            {chainBuyOrder.reason && <small>{chainBuyOrder.reason}</small>}
            {chainBuyOrder.filled_size > 0 && !chainSellOrder && !chainSellPreview && <button className="pcButton ghost" type="button" disabled={chainBusy} onClick={() => void previewChainSell()}>一键卖出本次成交</button>}
          </div>
        )}
        {chainSellPreview && (
          <div className="pcChainTestQuote">
            <h3>卖出本次成交预览</h3>
            <dl><div><dt>卖出份额</dt><dd>{chainSellPreview.size.toFixed(4)}</dd></div><div><dt>当前买价</dt><dd>{price(chainSellPreview.best_bid)}</dd></div><div><dt>最低成交价</dt><dd>{price(chainSellPreview.worst_price)}</dd></div><div><dt>预计回收</dt><dd>{money(chainSellPreview.estimated_proceeds_usdc)}</dd></div><div><dt>预计手续费</dt><dd>{money(chainSellPreview.estimated_fee_usdc)}</dd></div><div><dt>预计损益</dt><dd>{money(chainSellPreview.estimated_pnl_usdc)}</dd></div></dl>
            <button className="pcButton danger" type="button" disabled={chainBusy} onClick={() => void executeChainSell()}>确认卖出本次成交</button>
          </div>
        )}
        {chainSellOrder && <div className="pcChainTestResult"><h3>买卖链路验证完成</h3><p>卖出状态 {chainSellOrder.status} · 成交 {chainSellOrder.filled_size.toFixed(4)} 份 · 回收 {money(chainSellOrder.filled_usdc)}</p>{chainSellOrder.reason && <small>{chainSellOrder.reason}</small>}</div>}
        {chainNotice && <p role={chainNotice.kind === "error" ? "alert" : undefined} className={chainNotice.kind === "success" ? "pcFormSuccess" : "pcFormError"}>{chainNotice.text}</p>}
      </section>
      <EmailSettingsPanel />
    </PolyCopyShell>
  );
}
