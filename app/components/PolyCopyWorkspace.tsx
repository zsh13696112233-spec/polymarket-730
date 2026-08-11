"use client";

import { CSSProperties, FormEvent, ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { PolyCopyShell, WorkspaceView } from "./PolyCopyShell";

const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8730"
).replace(/\/$/, "");

type Numeric = number | string;

type Wallet = {
  id: number;
  address: string;
  proxy_wallet: string;
  label: string;
  wallet_role: "self" | "tracked";
  enabled: boolean;
  status: string;
  last_success_at: string | null;
  last_error: string | null;
};

type Account = {
  wallet_id: number;
  signer_address: string | null;
  funder_address: string | null;
  signature_type: 1 | 3;
  credentials_configured: boolean;
  status: string;
  budget_usdc: Numeric;
  cash_reserve_usdc: Numeric;
  max_total_exposure_usdc: Numeric;
  daily_buy_limit_usdc: Numeric;
  daily_loss_limit_usdc: Numeric;
  auto_redeem: boolean;
  collateral_balance: Numeric | null;
  last_balance_at: string | null;
  last_error: string | null;
};

type Subscription = {
  id: number;
  tracked_wallet_id: number;
  tracked_wallet_label: string | null;
  enabled: boolean;
  state: "active" | "paused" | "exit_only" | "closing" | "disabled" | "error";
  copy_ratio_percent: Numeric;
  position_cap_usdc: Numeric;
  large_increase_threshold_usdc: Numeric;
  total_exposure_cap_usdc: Numeric;
  market_slippage_cents: Numeric;
  open_exposure_usdc: Numeric;
  daily_bought_usdc: Numeric;
  daily_realized_pnl: Numeric;
  last_error: string | null;
};

type Portfolio = {
  open_cost_usdc: Numeric;
  market_value_usdc: Numeric | null;
  unrealized_pnl: Numeric | null;
  realized_pnl: Numeric;
  total_pnl: Numeric | null;
  valuation_complete: boolean;
  unpriced_positions: number;
  valued_at: string | null;
};

type Strategy = {
  subscription: Subscription;
  wallet: Wallet;
  portfolio: Portfolio;
  lifetime_bought_usdc: Numeric;
  open_positions: number;
  stale: boolean;
};

type CopyOrder = {
  id: number;
  asset_id: string;
  side: "BUY" | "SELL";
  requested_usdc: Numeric;
  leader_purchase_usdc: Numeric | null;
  proportional_target_usdc: Numeric | null;
  filled_size: Numeric;
  filled_usdc: Numeric;
  fee_usdc: Numeric;
  reference_price: Numeric | null;
  limit_price: Numeric;
  average_fill_price: Numeric | null;
  status: string;
  reason: string | null;
  created_at: string;
  tracked_wallet_id: number | null;
  tracked_wallet_label: string | null;
  tracked_wallet_address: string | null;
  title: string | null;
  outcome: string | null;
  event_slug: string | null;
};

type CopyPosition = {
  id: number;
  asset_id: string;
  title: string;
  outcome: string;
  event_slug: string | null;
  tracked_wallet_id: number;
  tracked_wallet_label: string;
  tracked_wallet_address: string;
  attributed_size: Numeric;
  attributed_cost: Numeric;
  realized_pnl: Numeric;
  status: string;
  average_entry_price: Numeric | null;
  current_bid: Numeric | null;
  current_value: Numeric | null;
  unrealized_pnl: Numeric | null;
  unrealized_pnl_percent: Numeric | null;
  total_pnl: Numeric | null;
  lifetime_bought_usdc: Numeric;
  lifetime_sold_usdc: Numeric;
  valuation_status: "ok" | "unavailable" | "not_applicable";
  updated_at: string;
};

type Overview = {
  live_copy_enabled: boolean;
  account: Account | null;
  totals: {
    collateral_balance: Numeric | null;
    available_capacity_usdc: Numeric;
    open_exposure_usdc: Numeric;
    daily_bought_usdc: Numeric;
    total_pnl: Numeric | null;
    valuation_complete: boolean;
    unpriced_positions: number;
  };
  daily_realized_pnl: Array<{
    date: string;
    realized_pnl: Numeric;
  }>;
  strategies: Strategy[];
  recent_orders: CopyOrder[];
  as_of: string;
};

type PositionResponse = { items: CopyPosition[]; portfolio: Portfolio; as_of: string };
type OrderResponse = { items: CopyOrder[]; next_cursor: string | null };

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail || `请求失败（${response.status}）`);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function number(value: Numeric | null | undefined) {
  const result = Number(value ?? 0);
  return Number.isFinite(result) ? result : 0;
}

function money(value: Numeric | null | undefined, fallback = "—") {
  if (value === null || value === undefined) return fallback;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(number(value));
}

function signedMoney(value: Numeric | null | undefined, fallback = "—") {
  if (value === null || value === undefined) return fallback;
  const amount = number(value);
  if (amount > 0) return `+${money(amount)}`;
  if (amount < 0) return `-${money(Math.abs(amount))}`;
  return money(amount);
}

function price(value: Numeric | null | undefined) {
  return value === null || value === undefined ? "—" : `${(number(value) * 100).toFixed(1)}¢`;
}

function dateTime(value: string | null | undefined) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function shortAddress(value: string | null | undefined) {
  if (!value) return "未关联";
  return value.length > 12 ? `${value.slice(0, 6)}…${value.slice(-4)}` : value;
}

function profileUrl(proxyWallet: string) {
  return `https://polymarket.com/profile/${proxyWallet}`;
}

const pixelGlyphs: Record<string, string[]> = {
  "0": ["0110", "1001", "1001", "1001", "1001", "1001", "0110"],
  "1": ["0010", "0110", "0010", "0010", "0010", "0010", "0111"],
  "2": ["0110", "1001", "0001", "0010", "0100", "1000", "1111"],
  "3": ["1110", "0001", "0001", "0110", "0001", "0001", "1110"],
  "4": ["0010", "0110", "1010", "1010", "1111", "0010", "0010"],
  "5": ["1111", "1000", "1000", "1110", "0001", "0001", "1110"],
  "6": ["0110", "1000", "1000", "1110", "1001", "1001", "0110"],
  "7": ["1111", "0001", "0010", "0010", "0100", "0100", "0100"],
  "8": ["0110", "1001", "1001", "0110", "1001", "1001", "0110"],
  "9": ["0110", "1001", "1001", "0111", "0001", "0001", "0110"],
  "$": ["00100", "01111", "10100", "01110", "00101", "11110", "00100"],
  "+": ["000", "010", "010", "111", "010", "010", "000"],
  "-": ["000", "000", "000", "111", "000", "000", "000"],
  ",": ["00", "00", "00", "00", "00", "01", "10"],
  ".": ["0", "0", "0", "0", "0", "0", "1"],
  "—": ["0000", "0000", "0000", "1111", "0000", "0000", "0000"],
};

function PixelAmount({ value }: { value: string }) {
  const glyphs = Array.from(value, (character) => pixelGlyphs[character]);
  if (glyphs.some((glyph) => !glyph)) return <>{value}</>;

  let cursor = 0;
  const pixels: ReactNode[] = [];
  glyphs.forEach((glyph, glyphIndex) => {
    glyph.forEach((row, rowIndex) => {
      Array.from(row).forEach((pixel, columnIndex) => {
        if (pixel === "1") {
          pixels.push(
            <rect
              key={`${glyphIndex}-${rowIndex}-${columnIndex}`}
              x={cursor + columnIndex}
              y={rowIndex}
              width="0.88"
              height="0.88"
            />,
          );
        }
      });
    });
    cursor += glyph[0].length + 1;
  });

  const viewWidth = Math.max(cursor - 1, 1);
  return (
    <span className="pcPixelAmount" aria-label={value}>
      <svg
        aria-hidden="true"
        className="pcPixelAmountGlyphs"
        focusable="false"
        viewBox={`0 0 ${viewWidth} 7`}
        style={{ width: `${viewWidth / 7}em` }}
      >
        {pixels}
      </svg>
    </span>
  );
}

const strategyState: Record<string, { label: string; tone: string }> = {
  active: { label: "运行中", tone: "success" },
  paused: { label: "已暂停", tone: "warning" },
  exit_only: { label: "只出不进", tone: "warning" },
  closing: { label: "清仓中", tone: "warning" },
  disabled: { label: "未开启", tone: "neutral" },
  error: { label: "异常", tone: "danger" },
};

const orderState: Record<string, { label: string; tone: string }> = {
  filled: { label: "已成交", tone: "success" },
  partially_filled: { label: "部分成交", tone: "warning" },
  unfilled: { label: "未成交", tone: "neutral" },
  skipped: { label: "已跳过", tone: "neutral" },
  blocked: { label: "风控阻止", tone: "warning" },
  planned: { label: "计划中", tone: "processing" },
  signed: { label: "已签名", tone: "processing" },
  submitted: { label: "已提交", tone: "processing" },
  reconciliation_pending: { label: "待核对", tone: "danger" },
  interrupted_before_submit: { label: "提交中断", tone: "danger" },
};

const positionState: Record<string, { label: string; tone: string }> = {
  open: { label: "持仓中", tone: "success" },
  dust_closed: { label: "零碎残留已核销", tone: "neutral" },
};

function Badge({ label, tone = "neutral" }: { label: string; tone?: string }) {
  return <span className={`pcBadge ${tone}`}>{label}</span>;
}

function Pnl({ value, secondary }: { value: Numeric | null; secondary?: string }) {
  if (value === null) return <span className="pcMissing">未定价</span>;
  const tone = number(value) > 0 ? "profit" : number(value) < 0 ? "loss" : "flat";
  return (
    <span className={`pcPnl ${tone}`}>
      {signedMoney(value)}
      {secondary && <small>{secondary}</small>}
    </span>
  );
}

function MarketIdentity({ order }: { order: CopyOrder }) {
  const title = order.title || `资产 ${shortAddress(order.asset_id)}`;
  const content = (
    <>
      <strong>{title}</strong>
      <span>
        {order.outcome || "未知 Outcome"} · {order.tracked_wallet_label || shortAddress(order.tracked_wallet_address)}
      </span>
    </>
  );
  return order.event_slug ? (
    <a className="pcMarketIdentity" href={`https://polymarket.com/event/${order.event_slug}`} target="_blank" rel="noreferrer">
      {content}
    </a>
  ) : (
    <span className="pcMarketIdentity">{content}</span>
  );
}

function EmptyState({
  title,
  message,
  action,
}: {
  title: string;
  message: string;
  action?: ReactNode;
}) {
  return (
    <div className="pcEmptyState">
      <span className="pcEmptyIcon">◇</span>
      <h2>{title}</h2>
      <p>{message}</p>
      {action}
    </div>
  );
}

function LoadingState() {
  return (
    <div className="pcLoading" role="status">
      <span />
      正在读取最新数据…
    </div>
  );
}

function Modal({
  title,
  eyebrow,
  onClose,
  children,
}: {
  title: string;
  eyebrow: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <div className="pcModalBackdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="pcModal" role="dialog" aria-modal="true" aria-label={title}>
        <header>
          <div>
            <span className="pcEyebrow">{eyebrow}</span>
            <h2>{title}</h2>
          </div>
          <button type="button" className="pcIconButton" aria-label="关闭" onClick={onClose}>×</button>
        </header>
        {children}
      </section>
    </div>
  );
}

function WalletModal({
  mode,
  onClose,
  onSaved,
}: {
  mode: "tracked" | "self";
  onClose: () => void;
  onSaved: () => void;
}) {
  const [address, setAddress] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!address.trim()) return setError("请输入钱包地址或 Polymarket 个人页链接");
    setBusy(true);
    setError(null);
    try {
      await api(mode === "self" ? "/api/my-wallet" : "/api/wallets", {
        method: mode === "self" ? "PUT" : "POST",
        body: JSON.stringify({ address: address.trim(), ...(label.trim() ? { label: label.trim() } : {}) }),
      });
      onSaved();
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "无法保存钱包");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={mode === "self" ? "设置执行钱包地址" : "添加目标"}
      eyebrow={mode === "self" ? "执行账户" : "公开钱包"}
      onClose={onClose}
    >
      <form className="pcForm" onSubmit={submit}>
        <label className="pcField">
          <span>钱包地址或个人页链接</span>
          <input value={address} onChange={(event) => setAddress(event.target.value)} placeholder="0x… 或 polymarket.com/profile/0x…" disabled={busy} />
        </label>
        <label className="pcField">
          <span>钱包备注 <small>可选</small></span>
          <input value={label} onChange={(event) => setLabel(event.target.value)} placeholder={mode === "self" ? "例如：我的执行钱包" : "例如：高胜率体育账户"} disabled={busy} />
        </label>
        <p className="pcFormHint">只读取公开地址信息；私钥不会通过此页面提交。</p>
        {error && <p className="pcFormError" role="alert">{error}</p>}
        <div className="pcModalActions">
          <button className="pcButton ghost" type="button" onClick={onClose}>取消</button>
          <button className="pcButton primary" type="submit" disabled={busy}>{busy ? "正在保存…" : "保存钱包"}</button>
        </div>
      </form>
    </Modal>
  );
}

function QuickSettingsModal({
  wallet,
  strategy,
  account,
  onClose,
  onSaved,
}: {
  wallet: Wallet;
  strategy: Strategy | null;
  account: Account | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const subscription = strategy?.subscription;
  const [ratio, setRatio] = useState(String(subscription?.copy_ratio_percent ?? 10));
  const [positionCap, setPositionCap] = useState(String(subscription?.position_cap_usdc ?? 20));
  const [largeIncreaseThreshold, setLargeIncreaseThreshold] = useState(String(subscription?.large_increase_threshold_usdc ?? 100));
  const [dailyLimit, setDailyLimit] = useState(String(account?.daily_buy_limit_usdc ?? 80));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const subscriptionPayload = {
        copy_ratio_percent: Number(ratio),
        position_cap_usdc: Number(positionCap),
        large_increase_threshold_usdc: Number(largeIncreaseThreshold),
        total_exposure_cap_usdc: Number(subscription?.total_exposure_cap_usdc ?? 160),
        market_slippage_cents: Number(subscription?.market_slippage_cents ?? 5),
      };
      await api(
        subscription ? `/api/copy-trading/subscriptions/${subscription.id}` : "/api/copy-trading/subscriptions",
        {
          method: subscription ? "PUT" : "POST",
          body: JSON.stringify(subscription ? subscriptionPayload : { ...subscriptionPayload, tracked_wallet_id: wallet.id }),
        },
      );
      if (account && Number(dailyLimit) !== Number(account.daily_buy_limit_usdc)) {
        await api("/api/copy-trading/account", {
          method: "PUT",
          body: JSON.stringify({
            wallet_id: account.wallet_id,
            signer_address: account.signer_address,
            funder_address: account.funder_address,
            signature_type: account.signature_type,
            budget_usdc: Number(account.budget_usdc),
            cash_reserve_usdc: Number(account.cash_reserve_usdc),
            max_total_exposure_usdc: Number(account.max_total_exposure_usdc),
            daily_buy_limit_usdc: Number(dailyLimit),
            daily_loss_limit_usdc: Number(account.daily_loss_limit_usdc),
            auto_redeem: account.auto_redeem,
          }),
        });
      }
      onSaved();
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "无法保存策略参数");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={`钱包策略设置 · ${wallet.label || shortAddress(wallet.proxy_wallet)}`} eyebrow="跟单与资金边界" onClose={onClose}>
      <form className="pcForm pcQuickSettings" onSubmit={submit}>
        <p className="pcQuickSettingsIntro">这些设置决定该目标钱包如何跟单；保存后仅影响之后的新订单。</p>
        <div className="pcFormGrid three">
          <label className="pcField">
            <span>跟单比例</span>
            <div className="pcUnitInput"><input type="number" min="0.01" max="100" step="0.01" value={ratio} onChange={(event) => setRatio(event.target.value)} /><b>%</b></div>
            <small>目标钱包每买入 $100，本策略按此比例买入；例如 10% 约买 $10。</small>
          </label>
          <label className="pcField">
            <span>单个市场最多投入</span>
            <div className="pcUnitInput"><input type="number" min="0.01" step="0.01" value={positionCap} onChange={(event) => setPositionCap(event.target.value)} /><b>USDC</b></div>
            <small>同一市场累计买入的最高金额；达到后不再继续加仓。</small>
          </label>
          <label className="pcField">
            <span>加仓触发金额</span>
            <div className="pcUnitInput"><input type="number" min="0.01" step="0.01" value={largeIncreaseThreshold} onChange={(event) => setLargeIncreaseThreshold(event.target.value)} /><b>USDC</b></div>
            <small>目标钱包单次净加仓达到此金额才跟随；低于该值只记录、不下单。</small>
          </label>
          <label className="pcField">
            <span>今日最多买入</span>
            <div className="pcUnitInput"><input type="number" min="0" step="0.01" value={dailyLimit} onChange={(event) => setDailyLimit(event.target.value)} disabled={!account} /><b>USDC</b></div>
            <small>{account ? "所有跟单策略共享的当日总买入额度；达到后当天不再开新仓。" : "绑定执行钱包后可配置。"}</small>
          </label>
        </div>
        <p className="pcFormHint">滑点、总敞口、现金保留额等账户级风控参数位于“设置 → 高级参数”。</p>
        {error && <p className="pcFormError" role="alert">{error}</p>}
        <div className="pcModalActions">
          <button className="pcButton ghost" type="button" onClick={onClose}>取消</button>
          <button className="pcButton primary" type="submit" disabled={busy}>{busy ? "正在保存…" : subscription ? "保存参数" : "创建策略"}</button>
        </div>
      </form>
    </Modal>
  );
}

function OrderTable({ orders }: { orders: CopyOrder[] }) {
  if (orders.length === 0) {
    return <EmptyState title="暂无记录" message="策略产生信号后，成功、跳过和异常记录都会显示在这里。" />;
  }
  return (
    <>
      <div className="pcTableWrap pcDesktopOnly">
        <table className="pcTable pcOrderTable">
          <colgroup>
            <col className="pcOrderTimeColumn" />
            <col className="pcOrderMarketColumn" />
            <col className="pcOrderSideColumn" />
            <col className="pcOrderLeaderColumn" />
            <col className="pcOrderTargetColumn" />
            <col className="pcOrderPlannedColumn" />
            <col className="pcOrderFilledColumn" />
            <col className="pcOrderPriceColumn" />
            <col className="pcOrderStatusColumn" />
          </colgroup>
          <thead><tr><th>时间</th><th>市场 / 来源</th><th>方向</th><th className="numeric">源钱包交易金额</th><th className="numeric">按比例目标金额</th><th className="numeric">执行预算</th><th className="numeric">实际执行金额</th><th className="numeric">成交价</th><th>状态 / 原因</th></tr></thead>
          <tbody>
            {orders.map((order) => {
              const state = orderState[order.status] || { label: order.status, tone: "neutral" };
              return (
                <tr key={order.id}>
                  <td className="pcTimeCell"><time>{dateTime(order.created_at)}</time><small>#{order.id}</small></td>
                  <td><MarketIdentity order={order} /></td>
                  <td><Badge label={order.side === "BUY" ? "买入" : "卖出"} tone={order.side === "BUY" ? "buy" : "sell"} /></td>
                  <td className="numeric">{money(order.side === "BUY" ? order.leader_purchase_usdc : null)}</td>
                  <td className="numeric">{money(order.side === "BUY" ? order.proportional_target_usdc : null)}</td>
                  <td className="numeric">{money(order.requested_usdc)}</td>
                  <td className="numeric"><strong>{money(order.filled_usdc)}</strong><small>{number(order.fee_usdc) > 0 ? `费用 ${money(order.fee_usdc)}` : ""}</small></td>
                  <td className="numeric">{price(order.average_fill_price ?? order.reference_price ?? order.limit_price)}</td>
                  <td className="pcStatusCell"><Badge label={state.label} tone={state.tone} />{order.reason && <small title={order.reason}>{order.reason}</small>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="pcMobileCards">
        {orders.map((order) => {
          const state = orderState[order.status] || { label: order.status, tone: "neutral" };
          return (
            <article className="pcMobileCard" key={order.id}>
              <div className="pcMobileCardHeader"><MarketIdentity order={order} /><Badge label={state.label} tone={state.tone} /></div>
              <dl><div><dt>方向</dt><dd>{order.side === "BUY" ? "买入" : "卖出"}</dd></div><div><dt>源钱包交易金额</dt><dd>{money(order.side === "BUY" ? order.leader_purchase_usdc : null)}</dd></div><div><dt>按比例目标金额</dt><dd>{money(order.side === "BUY" ? order.proportional_target_usdc : null)}</dd></div><div><dt>执行预算</dt><dd>{money(order.requested_usdc)}</dd></div><div><dt>实际执行金额</dt><dd>{money(order.filled_usdc)}</dd></div><div><dt>成交价</dt><dd>{price(order.average_fill_price)}</dd></div><div><dt>时间</dt><dd>{dateTime(order.created_at)}</dd></div></dl>
              {order.reason && <p>{order.reason}</p>}
            </article>
          );
        })}
      </div>
    </>
  );
}

function DailyRealizedPnlChart({ items }: { items: Overview["daily_realized_pnl"] }) {
  const [range, setRange] = useState<"7" | "15" | "30" | "all">("30");
  const visibleItems = range === "all" ? items : items.slice(-Number(range));
  const values = visibleItems.map((item) => number(item.realized_pnl));
  const maximum = Math.max(0, ...values.map((value) => Math.abs(value)));
  const total = values.reduce((sum, value) => sum + value, 0);
  const profitableDays = values.filter((value) => value > 0).length;
  const totalTone = total > 0 ? "profit" : total < 0 ? "loss" : "flat";
  const rangeLabel = range === "all" ? "全部" : `${range} 日`;
  const labelStep = visibleItems.length <= 7 ? 1 : visibleItems.length <= 15 ? 2 : 5;
  const chartStyle = {
    "--pc-daily-pnl-columns": visibleItems.length,
    "--pc-daily-pnl-min-width": `${range === "all" ? Math.max(340, visibleItems.length * 18) : 340}px`,
  } as CSSProperties;

  return (
    <section className="pcPanel pcDailyPnlPanel" aria-labelledby="daily-pnl-title">
      <header className="pcPanelHeader">
        <div>
          <span className="pcEyebrow">REALIZED PNL · 30 DAYS</span>
          <h2 id="daily-pnl-title">每日盈亏</h2>
          <p>全部策略按北京时间汇总的已实现盈亏。</p>
        </div>
        <div className="pcDailyPnlHeaderTools">
          <div className="pcDailyPnlRanges" aria-label="每日盈亏日期范围">
            {(["7", "15", "30", "all"] as const).map((value) => (
              <button type="button" key={value} className={range === value ? "active" : ""} aria-pressed={range === value} onClick={() => setRange(value)}>
                {value === "all" ? "全部" : `${value}天`}
              </button>
            ))}
          </div>
          <dl className="pcDailyPnlSummary">
            <div><dt>{rangeLabel}累计</dt><dd className={totalTone}>{signedMoney(total)}</dd></div>
            <div><dt>盈利天数</dt><dd>{profitableDays} 天</dd></div>
          </dl>
        </div>
      </header>
      {maximum === 0 ? (
        <div className="pcDailyPnlEmpty" role="status">近 30 日暂无已实现盈亏</div>
      ) : (
        <div className="pcDailyPnlScroll">
          <div className="pcDailyPnlChart" style={chartStyle}>
            <div className="pcDailyPnlPlot">
              <span className="pcDailyPnlGridLine top" aria-hidden="true" />
              <span className="pcDailyPnlGridLine zero" aria-hidden="true" />
              <span className="pcDailyPnlGridLine bottom" aria-hidden="true" />
              <div className="pcDailyPnlBars">
                {visibleItems.map((item) => {
                  const value = number(item.realized_pnl);
                  const height = value === 0 ? 0 : Math.max(2.5, Math.abs(value) / maximum * 44);
                  const [year, month, day] = item.date.split("-");
                  const readableDate = `${year}年${Number(month)}月${Number(day)}日`;
                  const tone = value > 0 ? "profit" : value < 0 ? "loss" : "flat";
                  return (
                    <span
                      className={`pcDailyPnlColumn ${tone}`}
                      key={item.date}
                      role="img"
                      tabIndex={0}
                      aria-label={`${readableDate}，已实现盈亏 ${signedMoney(value)}`}
                      title={`${readableDate} · ${signedMoney(value)}`}
                      style={{ "--pc-daily-pnl-height": `${height}%` } as CSSProperties}
                    >
                      <span className="pcDailyPnlBar" aria-hidden="true" />
                      <span className="pcDailyPnlTooltip" role="tooltip">{signedMoney(value)}</span>
                    </span>
                  );
                })}
              </div>
            </div>
            <div className="pcDailyPnlDates" aria-hidden="true">
              {visibleItems.map((item, index) => {
                const [, month, day] = item.date.split("-");
                const visible = index % labelStep === 0 || index === visibleItems.length - 1;
                return <span className={visible ? "visible" : ""} key={item.date}>{Number(month)}/{Number(day)}</span>;
              })}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function OverviewPage({
  overview,
  wallets,
  busyId,
  onToggle,
  onConfigure,
  onCloseStrategy,
}: {
  overview: Overview;
  wallets: Wallet[];
  busyId: number | null;
  onToggle: (strategy: Strategy) => void;
  onConfigure: (wallet: Wallet) => void;
  onCloseStrategy: (strategy: Strategy) => void;
}) {
  const trackedWallets = wallets.filter((wallet) => wallet.wallet_role === "tracked" && wallet.enabled !== false);
  const strategyByWallet = new Map(overview.strategies.map((strategy) => [strategy.wallet.id, strategy]));
  const total = overview.totals.total_pnl;
  const metrics = [
    { label: "执行钱包余额", value: money(overview.totals.collateral_balance), meta: overview.account?.last_balance_at ? `更新于 ${dateTime(overview.account.last_balance_at)}` : "等待账户验证", tone: "" },
    { label: "可用额度", value: money(overview.totals.available_capacity_usdc), meta: `今日已买入 ${money(overview.totals.daily_bought_usdc)}`, tone: "" },
    { label: "当前敞口", value: money(overview.totals.open_exposure_usdc), meta: `${overview.strategies.filter((item) => item.subscription.enabled).length} 个策略运行中`, tone: "" },
    { label: "总盈亏", value: total === null ? "未完整定价" : signedMoney(total), meta: overview.totals.valuation_complete ? "包含已实现与浮动盈亏" : `${overview.totals.unpriced_positions} 个仓位缺少报价`, tone: total === null ? "" : number(total) > 0 ? "profit" : number(total) < 0 ? "loss" : "" },
  ];
  return (
    <>
      {!overview.live_copy_enabled && <div className="pcAlert danger"><span>!</span><p><strong>实盘策略已被系统停用</strong>当前不能开启新的买入，已有仓位仍会继续处理退出。</p></div>}
      <section className="pcMetricGrid" aria-label="全局资金概览">
        {metrics.map((metric) => <article className="pcMetricCard" key={metric.label}><span>{metric.label}</span><strong className={metric.tone}><PixelAmount value={metric.value} /></strong><small>{metric.meta}</small></article>)}
      </section>

      <DailyRealizedPnlChart items={overview.daily_realized_pnl ?? []} />

      <section className="pcPanel">
        <header className="pcPanelHeader">
          <div><span className="pcEyebrow">STRATEGIES</span><h2>策略</h2><p>每个目标钱包独立启停，资金边界由执行钱包统一控制。</p></div>
          <span className="pcPanelCount">{trackedWallets.length} 个目标</span>
        </header>
        {trackedWallets.length === 0 ? (
          <EmptyState title="还没有目标" message="添加一个公开 Polymarket 钱包，设置参数后即可开始。" />
        ) : (
          <div className="pcStrategyList">
            {trackedWallets.map((wallet) => {
              const strategy = strategyByWallet.get(wallet.id) || null;
              const subscription = strategy?.subscription;
              const state = subscription ? strategyState[subscription.state] || { label: subscription.state, tone: "neutral" } : { label: "未配置", tone: "neutral" };
              return (
                <article className="pcStrategyRow" key={wallet.id}>
                  <a className="pcStrategyIdentity" href={profileUrl(wallet.proxy_wallet)} target="_blank" rel="noreferrer" aria-label={`查看 ${wallet.label || shortAddress(wallet.proxy_wallet)} 的 Polymarket 主页`}><span className="pcWalletAvatar">{(wallet.label || "0x").slice(0, 2).toUpperCase()}</span><span><strong>{wallet.label || shortAddress(wallet.proxy_wallet)}</strong><small>{shortAddress(wallet.proxy_wallet)}{strategy?.stale ? " · 数据延迟" : ""}</small></span></a>
                  <div className="pcStrategyStatus"><Badge label={state.label} tone={state.tone} />{subscription && <small>{number(subscription.copy_ratio_percent)}% · 单市场 {money(subscription.position_cap_usdc)}</small>}</div>
                  <dl className="pcStrategyMetrics"><div><dt>当前敞口</dt><dd>{money(subscription?.open_exposure_usdc ?? 0)}</dd></div><div><dt>今日买入</dt><dd>{money(subscription?.daily_bought_usdc ?? 0)}</dd></div><div><dt>钱包总投入</dt><dd>{money(strategy?.lifetime_bought_usdc ?? 0)}</dd></div><div><dt>总盈亏</dt><dd><Pnl value={strategy?.portfolio.total_pnl ?? 0} /></dd></div><div><dt>持仓</dt><dd>{strategy?.open_positions ?? 0}</dd></div></dl>
                  <div className="pcStrategyActions">
                    <button className="pcButton ghost" type="button" onClick={() => onConfigure(wallet)}>{subscription ? "参数" : "配置"}</button>
                    {strategy && <button className={`pcSwitch ${subscription?.enabled ? "on" : ""}`} type="button" role="switch" aria-checked={subscription?.enabled} aria-label={`${wallet.label}${subscription?.enabled ? "暂停策略" : "恢复策略"}`} disabled={busyId === subscription?.id || subscription?.state === "closing"} onClick={() => onToggle(strategy)}><span /></button>}
                    {strategy && <details className="pcActionMenu"><summary aria-label={`${wallet.label}更多操作`}>⋮</summary><div><button type="button" onClick={() => onCloseStrategy(strategy)}>停止策略并立即清仓</button></div></details>}
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>

      <section className="pcPanel">
        <header className="pcPanelHeader compact"><div><span className="pcEyebrow">ACTIVITY</span><h2>最近记录</h2></div><Link className="pcTextLink" href="/records">查看全部 →</Link></header>
        <OrderTable orders={overview.recent_orders} />
      </section>
    </>
  );
}

function PositionsPage({ overview }: { overview: Overview }) {
  const [scope, setScope] = useState<"open" | "history">("open");
  const [walletId, setWalletId] = useState("all");
  const [data, setData] = useState<PositionResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setLoading(true);
      const params = new URLSearchParams({ scope });
      if (walletId !== "all") params.set("tracked_wallet_id", walletId);
      void api<PositionResponse>(`/api/copy-trading/positions?${params}`)
        .then((result) => { if (!cancelled) { setData(result); setError(null); } })
        .catch((loadError) => { if (!cancelled) setError(loadError instanceof Error ? loadError.message : "无法读取持仓"); })
        .finally(() => { if (!cancelled) setLoading(false); });
    }, 0);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [scope, walletId]);

  return (
    <section className="pcPanel pcPagePanel">
      <header className="pcPanelHeader pcFilterHeader">
        <div className="pcSegmented"><button className={scope === "open" ? "active" : ""} onClick={() => setScope("open")} type="button">当前持仓</button><button className={scope === "history" ? "active" : ""} onClick={() => setScope("history")} type="button">历史仓位</button></div>
        <label className="pcSelect"><span>目标钱包</span><select value={walletId} onChange={(event) => setWalletId(event.target.value)}><option value="all">全部钱包</option>{overview.strategies.map((strategy) => <option value={strategy.wallet.id} key={strategy.wallet.id}>{strategy.wallet.label}</option>)}</select></label>
      </header>
      {data && scope === "open" && <div className="pcInlineSummary"><div><span>持仓成本</span><strong>{money(data.portfolio.open_cost_usdc)}</strong></div><div><span>当前市值</span><strong>{money(data.portfolio.market_value_usdc)}</strong></div><div><span>浮动盈亏</span><Pnl value={data.portfolio.unrealized_pnl} /></div><div><span>已实现盈亏</span><Pnl value={data.portfolio.realized_pnl} /></div>{!data.portfolio.valuation_complete && <p>{data.portfolio.unpriced_positions} 个仓位未定价，汇总盈亏暂不显示。</p>}</div>}
      {loading ? <LoadingState /> : error ? <div className="pcAlert danger"><span>!</span><p><strong>持仓读取失败</strong>{error}</p></div> : !data?.items.length ? <EmptyState title={scope === "open" ? "暂无自动策略持仓" : "暂无历史仓位"} message={scope === "open" ? "策略完成首次买入后，仓位会出现在这里。" : "已完成清仓或赎回的仓位会保留在这里。"} /> : (
        <>
          <div className="pcTableWrap pcDesktopOnly"><table className="pcTable"><thead><tr><th>市场 / Outcome</th><th>来源钱包</th><th className="numeric">成本 / 均价</th><th className="numeric">当前价 / 市值</th><th className="numeric">浮动盈亏</th><th className="numeric">已实现 / 总盈亏</th><th>状态</th></tr></thead><tbody>{data.items.map((position) => { const state = positionState[position.status] || { label: position.status, tone: "neutral" }; return <tr key={position.id}><td><a className="pcMarketIdentity" href={position.event_slug ? `https://polymarket.com/event/${position.event_slug}` : undefined} target="_blank" rel="noreferrer"><strong>{position.title}</strong><span>{position.outcome}</span></a></td><td><span className="pcWalletCell"><strong>{position.tracked_wallet_label}</strong><small>{shortAddress(position.tracked_wallet_address)}</small></span></td><td className="numeric"><strong>{money(scope === "open" ? position.attributed_cost : position.lifetime_bought_usdc)}</strong><small>{price(position.average_entry_price)}</small></td><td className="numeric">{position.valuation_status === "unavailable" ? <span className="pcMissing">未定价</span> : <><strong>{price(position.current_bid)}</strong><small>{money(position.current_value)}</small></>}</td><td className="numeric"><Pnl value={position.unrealized_pnl} secondary={position.unrealized_pnl_percent === null ? undefined : `${number(position.unrealized_pnl_percent).toFixed(2)}%`} /></td><td className="numeric"><Pnl value={position.total_pnl} secondary={`已实现 ${money(position.realized_pnl)}`} /></td><td><Badge label={state.label} tone={state.tone} /></td></tr>; })}</tbody></table></div>
          <div className="pcMobileCards">{data.items.map((position) => { const state = positionState[position.status] || { label: position.status, tone: "neutral" }; return <article className="pcMobileCard" key={position.id}><div className="pcMobileCardHeader"><span className="pcMarketIdentity"><strong>{position.title}</strong><span>{position.outcome} · {position.tracked_wallet_label}</span></span><Badge label={state.label} tone={state.tone} /></div><dl><div><dt>成本</dt><dd>{money(position.attributed_cost)}</dd></div><div><dt>当前市值</dt><dd>{money(position.current_value)}</dd></div><div><dt>浮动盈亏</dt><dd><Pnl value={position.unrealized_pnl} /></dd></div><div><dt>总盈亏</dt><dd><Pnl value={position.total_pnl} /></dd></div></dl></article>; })}</div>
        </>
      )}
    </section>
  );
}

function RecordsPage({ overview }: { overview: Overview }) {
  const [walletId, setWalletId] = useState("all");
  const [side, setSide] = useState("all");
  const [status, setStatus] = useState("all");
  const [range, setRange] = useState("7d");
  const [items, setItems] = useState<CopyOrder[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const buildPath = useCallback((nextCursor?: string) => {
    const params = new URLSearchParams({ limit: "50" });
    if (walletId !== "all") params.set("tracked_wallet_id", walletId);
    if (side !== "all") params.set("side", side);
    if (status !== "all") params.set("status_group", status);
    if (range !== "all") {
      const days = range === "today" ? 0 : Number(range.replace("d", ""));
      const from = new Date();
      if (range === "today") from.setHours(0, 0, 0, 0);
      else from.setDate(from.getDate() - days);
      params.set("from_time", from.toISOString());
    }
    if (nextCursor) params.set("cursor", nextCursor);
    return `/api/copy-trading/orders?${params}`;
  }, [range, side, status, walletId]);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setLoading(true);
      void api<OrderResponse>(buildPath())
        .then((result) => { if (!cancelled) { setItems(result.items); setCursor(result.next_cursor); setError(null); } })
        .catch((loadError) => { if (!cancelled) setError(loadError instanceof Error ? loadError.message : "无法读取记录"); })
        .finally(() => { if (!cancelled) setLoading(false); });
    }, 0);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [buildPath]);

  async function loadMore() {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const result = await api<OrderResponse>(buildPath(cursor));
      setItems((current) => [...current, ...result.items]);
      setCursor(result.next_cursor);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "无法加载更多记录");
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <section className="pcPanel pcPagePanel">
      <header className="pcRecordsHeader">
        <div className="pcFilters">
          <label className="pcSelect"><span>目标钱包</span><select value={walletId} onChange={(event) => setWalletId(event.target.value)}><option value="all">全部钱包</option>{overview.strategies.map((strategy) => <option value={strategy.wallet.id} key={strategy.wallet.id}>{strategy.wallet.label}</option>)}</select></label>
          <label className="pcSelect"><span>方向</span><select value={side} onChange={(event) => setSide(event.target.value)}><option value="all">全部方向</option><option value="BUY">买入</option><option value="SELL">卖出</option></select></label>
          <label className="pcSelect"><span>状态</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">全部状态</option><option value="filled">已成交</option><option value="partial">部分成交</option><option value="unfilled">未成交</option><option value="skipped">跳过 / 风控</option><option value="processing">处理中</option><option value="attention">需要关注</option></select></label>
          <label className="pcSelect"><span>时间</span><select value={range} onChange={(event) => setRange(event.target.value)}><option value="today">今天</option><option value="7d">近 7 天</option><option value="30d">近 30 天</option><option value="all">全部时间</option></select></label>
        </div>
        <span className="pcRecordCount">{items.length} 条记录</span>
      </header>
      {loading ? <LoadingState /> : error && items.length === 0 ? <div className="pcAlert danger"><span>!</span><p><strong>记录读取失败</strong>{error}</p></div> : <OrderTable orders={items} />}
      {error && items.length > 0 && <p className="pcInlineError">{error}</p>}
      {cursor && <div className="pcLoadMore"><button className="pcButton ghost" type="button" disabled={loadingMore} onClick={loadMore}>{loadingMore ? "正在加载…" : "加载更早记录"}</button></div>}
    </section>
  );
}

function AccountRiskForm({ account, onSaved }: { account: Account; onSaved: () => void }) {
  const [values, setValues] = useState({
    budget_usdc: String(account.budget_usdc),
    cash_reserve_usdc: String(account.cash_reserve_usdc),
    max_total_exposure_usdc: String(account.max_total_exposure_usdc),
    daily_buy_limit_usdc: String(account.daily_buy_limit_usdc),
    daily_loss_limit_usdc: String(account.daily_loss_limit_usdc),
  });
  const [autoRedeem, setAutoRedeem] = useState(account.auto_redeem);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setMessage(null);
    try {
      await api("/api/copy-trading/account", { method: "PUT", body: JSON.stringify({ wallet_id: account.wallet_id, signer_address: account.signer_address, funder_address: account.funder_address, signature_type: account.signature_type, auto_redeem: autoRedeem, ...Object.fromEntries(Object.entries(values).map(([key, value]) => [key, Number(value)])) }) });
      setMessage("资金风控已保存，重新验证账户后生效。"); onSaved();
    } catch (submitError) { setMessage(submitError instanceof Error ? submitError.message : "保存失败"); }
    finally { setBusy(false); }
  }
  const fields: Record<keyof typeof values, { label: string; hint: string }> = {
    budget_usdc: { label: "执行钱包总预算", hint: "分配给自动跟单的总资金；必须不低于“必须保留的现金”。" },
    cash_reserve_usdc: { label: "必须保留的现金", hint: "这部分 USDC 不用于开仓，用于保证钱包始终保留可用余额。" },
    max_total_exposure_usdc: { label: "全部策略总持仓上限", hint: "所有目标钱包当前持仓成本的总和不能超过此金额。" },
    daily_buy_limit_usdc: { label: "今日全部策略最多买入", hint: "当天所有策略累计新买入的最高金额；达到后当天停止开新仓。" },
    daily_loss_limit_usdc: { label: "单日亏损暂停线", hint: "当天已实现亏损达到此金额后，系统停止当天的新买入。" },
  };
  return <form className="pcSettingsForm pcSystemSettingsForm" onSubmit={submit}><div className="pcSettingsIntro">这些资金限制由所有策略共享；数值越小，系统承担的风险越低。</div><div className="pcFormGrid settings">{(Object.keys(values) as Array<keyof typeof values>).map((key) => <label className="pcField" key={key}><span>{fields[key].label}</span><div className="pcUnitInput"><input type="number" min="0" step="0.01" value={values[key]} onChange={(event) => setValues((current) => ({ ...current, [key]: event.target.value }))} /><b>USDC</b></div><small>{fields[key].hint}</small></label>)}<label className="pcField"><span>市场结算后自动赎回</span><select value={autoRedeem ? "enabled" : "disabled"} onChange={(event) => setAutoRedeem(event.target.value === "enabled")}><option value="enabled">开启</option><option value="disabled">关闭</option></select><small>关闭后只保留可赎回仓位，不会由系统自动提交链上赎回交易。</small></label></div>{message && <p className={message.includes("已保存") ? "pcFormSuccess" : "pcFormError"}>{message}</p>}<div className="pcSettingsActions"><button className="pcButton primary" type="submit" disabled={busy}>{busy ? "正在保存…" : "保存资金风控"}</button></div></form>;
}

function AdvancedStrategyForm({ strategy, onSaved }: { strategy: Strategy; onSaved: () => void }) {
  const subscription = strategy.subscription;
  const [totalCap, setTotalCap] = useState(String(subscription.total_exposure_cap_usdc));
  const [slippage, setSlippage] = useState(String(subscription.market_slippage_cents));
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setMessage(null);
    try {
      await api(`/api/copy-trading/subscriptions/${subscription.id}`, { method: "PUT", body: JSON.stringify({ copy_ratio_percent: Number(subscription.copy_ratio_percent), position_cap_usdc: Number(subscription.position_cap_usdc), large_increase_threshold_usdc: Number(subscription.large_increase_threshold_usdc), total_exposure_cap_usdc: Number(totalCap), market_slippage_cents: Number(slippage) }) });
      setMessage("高级参数已保存。"); onSaved();
    } catch (submitError) { setMessage(submitError instanceof Error ? submitError.message : "保存失败"); }
    finally { setBusy(false); }
  }
  return <form className="pcSettingsForm pcSystemSettingsForm" onSubmit={submit}><div className="pcFormGrid two"><label className="pcField"><span>此目标钱包总投入上限</span><div className="pcUnitInput"><input type="number" min="0" step="0.01" value={totalCap} onChange={(event) => setTotalCap(event.target.value)} /><b>USDC</b></div><small>该目标钱包对应策略所有持仓成本的总和不能超过此金额。</small></label><label className="pcField"><span>可接受价格偏差（滑点）</span><div className="pcUnitInput"><input type="number" min="0" max="50" step="0.01" value={slippage} onChange={(event) => setSlippage(event.target.value)} /><b>¢</b></div><small>允许成交价比当前最优价最多差多少美分；超过时订单不会提交。</small></label></div>{message && <p className={message.includes("已保存") ? "pcFormSuccess" : "pcFormError"}>{message}</p>}<div className="pcSettingsActions"><button className="pcButton primary" type="submit" disabled={busy}>{busy ? "正在保存…" : "保存高级参数"}</button></div></form>;
}

function RehearsalForm({ enabled }: { enabled: boolean }) {
  const [marketUrl, setMarketUrl] = useState("");
  const [outcome, setOutcome] = useState("");
  const [maxTotalUsdc, setMaxTotalUsdc] = useState("1");
  const [preview, setPreview] = useState<{ confirmation_id: string; title: string; outcome: string; best_ask: Numeric; fee_rate_bps: number; max_total_usdc: Numeric } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function loadPreview(event: FormEvent) {
    event.preventDefault(); setBusy(true); setMessage(null); setPreview(null);
    try { setPreview(await api("/api/copy-trading/rehearsal/preview", { method: "POST", body: JSON.stringify({ market_url: marketUrl, outcome, max_total_usdc: Number(maxTotalUsdc) }) })); }
    catch (loadError) { setMessage(loadError instanceof Error ? loadError.message : "演练预览失败"); }
    finally { setBusy(false); }
  }
  async function execute() {
    if (!preview || !window.confirm(`确认真实花费最多 ${money(preview.max_total_usdc)} 执行单边买入演练？`)) return;
    setBusy(true); setMessage(null);
    try { const result = await api<{ filled_usdc: Numeric }>("/api/copy-trading/rehearsal/execute", { method: "POST", body: JSON.stringify({ confirmation_id: preview.confirmation_id, confirmation_text: "确认执行真实买入" }) }); setMessage(`演练已完成，实际成交 ${money(result.filled_usdc)}。`); setPreview(null); }
    catch (executeError) { setMessage(executeError instanceof Error ? executeError.message : "演练执行失败"); }
    finally { setBusy(false); }
  }
  return <form className="pcSettingsForm pcSystemSettingsForm" onSubmit={loadPreview}><div className="pcFormGrid three"><label className="pcField"><span>市场链接</span><input type="url" value={marketUrl} onChange={(event) => setMarketUrl(event.target.value)} placeholder="https://polymarket.com/event/…" disabled={!enabled || busy} required /></label><label className="pcField"><span>Outcome</span><input value={outcome} onChange={(event) => setOutcome(event.target.value)} placeholder="例如 Yes 或球队名称" disabled={!enabled || busy} required /></label><label className="pcField"><span>最高花费</span><div className="pcUnitInput"><input type="number" min="0.01" max="100" step="0.01" value={maxTotalUsdc} onChange={(event) => setMaxTotalUsdc(event.target.value)} disabled={!enabled || busy} required /><b>USDC</b></div></label></div>{!enabled && <p className="pcFormHint">完成执行钱包验证后才能进行真实下单演练。</p>}{preview && <div className="pcRehearsalPreview"><span><strong>{preview.title} · {preview.outcome}</strong><small>卖一 {price(preview.best_ask)} · 动态费用 {preview.fee_rate_bps} bps · 硬上限 {money(preview.max_total_usdc)}</small></span><button className="pcButton danger" type="button" onClick={execute} disabled={busy}>确认执行 {money(preview.max_total_usdc)} 买入</button></div>}{message && <p className={message.includes("已完成") ? "pcFormSuccess" : "pcFormError"}>{message}</p>}<div className="pcSettingsActions"><button className="pcButton ghost" type="submit" disabled={!enabled || busy}>{busy ? "正在检查…" : `预览 ${maxTotalUsdc || "0"} USDC 买入`}</button></div></form>;
}

function SettingsPage({
  overview,
  wallets,
  onReload,
  onAddSelf,
}: {
  overview: Overview;
  wallets: Wallet[];
  onReload: () => void;
  onAddSelf: () => void;
}) {
  const [strategyId, setStrategyId] = useState(String(overview.strategies[0]?.subscription.id ?? ""));
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const account = overview.account;
  const selfWallet = wallets.find((wallet) => wallet.wallet_role === "self") || null;
  const strategy = overview.strategies.find((item) => String(item.subscription.id) === strategyId) || overview.strategies[0] || null;

  async function bindAccount() {
    if (!selfWallet) return onAddSelf();
    setBusy(true); setMessage(null);
    try {
      await api("/api/copy-trading/account", { method: "PUT", body: JSON.stringify({ wallet_id: selfWallet.id, signer_address: selfWallet.address, funder_address: selfWallet.proxy_wallet, signature_type: 3, budget_usdc: 400, cash_reserve_usdc: 240, max_total_exposure_usdc: 160, daily_buy_limit_usdc: 80, daily_loss_limit_usdc: 40, auto_redeem: true }) });
      setMessage("执行钱包已绑定，请在钥匙串导入密钥后进行验证。"); onReload();
    } catch (bindError) { setMessage(bindError instanceof Error ? bindError.message : "执行钱包绑定失败"); }
    finally { setBusy(false); }
  }
  async function verify() {
    setBusy(true); setMessage(null);
    try { await api("/api/copy-trading/account/verify", { method: "POST" }); setMessage("执行钱包验证完成。"); onReload(); }
    catch (verifyError) { setMessage(verifyError instanceof Error ? verifyError.message : "账户验证失败"); }
    finally { setBusy(false); }
  }
  async function refreshBalance() {
    setBusy(true); setMessage(null);
    try { await api("/api/copy-trading/account/balance/refresh", { method: "POST" }); setMessage("执行钱包余额已刷新。"); onReload(); }
    catch (refreshError) { setMessage(refreshError instanceof Error ? refreshError.message : "执行钱包余额刷新失败"); }
    finally { setBusy(false); }
  }

  return <div className="pcSettingsStack pcSystemSettings">
    <section className="pcPanel"><header className="pcPanelHeader"><div><span className="pcEyebrow">EXECUTION ACCOUNT</span><h2>执行钱包</h2><p>唯一资金账户，为全部策略提供共享余额与风险边界。</p></div>{account && <Badge label={account.status === "ready" ? "已验证" : account.status === "insufficient_balance" ? "余额不足" : "待验证"} tone={account.status === "ready" ? "success" : "warning"} />}</header>{account ? <div className="pcAccountSummary"><div><span>签名地址</span><strong>{shortAddress(account.signer_address)}</strong></div><div><span>资金地址</span><strong>{shortAddress(account.funder_address)}</strong></div><div><span>当前余额</span><strong>{money(account.collateral_balance)}</strong><small>{account.last_balance_at ? `更新于 ${dateTime(account.last_balance_at)}` : "尚未刷新"}</small></div><div><span>密钥状态</span><strong>{account.credentials_configured ? "已配置" : "未配置"}</strong></div><button className="pcButton ghost" type="button" disabled={busy} onClick={refreshBalance}>{busy ? "正在刷新…" : "刷新余额"}</button><button className="pcButton ghost" type="button" disabled={busy} onClick={verify}>验证密钥与余额</button></div> : <EmptyState title="尚未绑定执行钱包" message="先设置“我的钱包”，再将其绑定为唯一执行账户。" action={<button className="pcButton primary" type="button" disabled={busy} onClick={bindAccount}>{selfWallet ? "绑定执行钱包" : "设置我的钱包"}</button>} />}{message && <p className={message.includes("完成") || message.includes("已绑定") || message.includes("已刷新") ? "pcFormSuccess pcPanelMessage" : "pcFormError pcPanelMessage"}>{message}</p>}</section>
    <section className="pcPanel"><header className="pcPanelHeader"><div><span className="pcEyebrow">CAPITAL RISK</span><h2>资金风控</h2><p>这些限制由所有目标钱包共享，修改后需要重新验证执行账户。</p></div></header>{account ? <AccountRiskForm account={account} onSaved={onReload} /> : <EmptyState title="等待执行钱包" message="绑定执行钱包后可配置预算、现金保留和每日风控。" />}</section>
    <section className="pcPanel"><header className="pcPanelHeader pcFilterHeader"><div><span className="pcEyebrow">ADVANCED STRATEGY</span><h2>策略高级参数</h2><p>常用的执行比例和单市场上限请在总览页快速调整。</p></div>{overview.strategies.length > 0 && <label className="pcSelect"><span>目标钱包</span><select value={strategyId} onChange={(event) => setStrategyId(event.target.value)}>{overview.strategies.map((item) => <option value={item.subscription.id} key={item.subscription.id}>{item.wallet.label}</option>)}</select></label>}</header>{strategy ? <AdvancedStrategyForm key={strategy.subscription.id} strategy={strategy} onSaved={onReload} /> : <EmptyState title="暂无已配置策略" message="从总览页为目标钱包创建策略后，可在这里调整高级参数。" />}</section>
    <section className="pcPanel"><header className="pcPanelHeader"><div><span className="pcEyebrow">DIAGNOSTICS</span><h2>账户诊断与下单演练</h2><p>低频维护工具集中在这里，不影响日常策略工作台。</p></div></header>{account?.signer_address && !account.credentials_configured && <div className="pcCommandHint"><span>导入执行密钥</span><code>uv run python -m backend.copy_cli set-key --account {account.signer_address}</code></div>}<RehearsalForm enabled={Boolean(overview.live_copy_enabled && account?.status === "ready")} /></section>
  </div>;
}

const viewCopy: Record<Exclude<WorkspaceView, "analysis">, { title: string; subtitle: string }> = {
  overview: { title: "总览", subtitle: "资金策略面板" },
  positions: { title: "持仓", subtitle: "查看全部目标钱包产生的实盘归因仓位" },
  records: { title: "记录", subtitle: "从信号到成交，保留每一次执行结果与原因" },
  settings: { title: "设置", subtitle: "管理执行钱包、风险边界与低频高级工具" },
};

export default function PolyCopyWorkspace({ view }: { view: Exclude<WorkspaceView, "analysis"> }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [wallets, setWallets] = useState<Wallet[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [quickWallet, setQuickWallet] = useState<Wallet | null>(null);
  const [walletModal, setWalletModal] = useState<"tracked" | "self" | null>(null);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [overviewData, walletData] = await Promise.all([
        api<Overview>("/api/copy-trading/overview"),
        api<Wallet[]>("/api/wallets"),
      ]);
      setOverview(overviewData);
      setWallets(walletData);
      setError(null);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "无法连接 PolyCopy 服务");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  useEffect(() => {
    const timer = window.setInterval(() => void load(true), 10_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const strategyForQuickWallet = useMemo(
    () => quickWallet && overview ? overview.strategies.find((strategy) => strategy.wallet.id === quickWallet.id) || null : null,
    [overview, quickWallet],
  );

  async function toggleStrategy(strategy: Strategy) {
    const subscription = strategy.subscription;
    const enabled = !subscription.enabled;
    if (enabled && !window.confirm(`确认恢复 ${strategy.wallet.label} 的真实资金自动策略？`)) return;
    setBusyId(subscription.id); setError(null);
    try {
      await api(`/api/copy-trading/subscriptions/${subscription.id}/enabled`, { method: "PUT", body: JSON.stringify({ enabled, confirm_live: enabled }) });
      await load(true);
    } catch (toggleError) { setError(toggleError instanceof Error ? toggleError.message : "策略开关操作失败"); }
    finally { setBusyId(null); }
  }

  async function closeStrategy(strategy: Strategy) {
    if (!window.confirm(`确认停止 ${strategy.wallet.label} 的策略并卖出全部归因持仓？此操作不可撤销。`)) return;
    setBusyId(strategy.subscription.id); setError(null);
    try {
      await api(`/api/copy-trading/subscriptions/${strategy.subscription.id}/action`, { method: "POST", body: JSON.stringify({ action: "close" }) });
      await load(true);
    } catch (closeError) { setError(closeError instanceof Error ? closeError.message : "无法关闭并清仓"); }
    finally { setBusyId(null); }
  }

  const copy = viewCopy[view];
  const actions = <><span className="pcAsOf"><span className={error ? "pcStatusDot error" : "pcStatusDot"} />{error ? "数据连接异常" : overview ? `更新于 ${dateTime(overview.as_of)}` : "正在连接"}</span>{view !== "settings" && <button className="pcButton primary" type="button" onClick={() => setWalletModal("tracked")}>＋ 添加目标</button>}</>;

  return (
    <PolyCopyShell active={view} title={copy.title} subtitle={copy.subtitle} actions={actions}>
      {loading && !overview ? <LoadingState /> : !overview ? <EmptyState title="无法读取策略工作台" message={error || "请确认本机 API 服务正在运行。"} action={<button className="pcButton primary" type="button" onClick={() => void load()}>重新连接</button>} /> : (
        <>
          {error && <div className="pcAlert danger pcGlobalError"><span>!</span><p><strong>部分数据可能不是最新</strong>{error}</p><button className="pcButton ghost" type="button" onClick={() => void load()}>重试</button></div>}
          {view === "overview" && <OverviewPage overview={overview} wallets={wallets} busyId={busyId} onToggle={(strategy) => void toggleStrategy(strategy)} onConfigure={setQuickWallet} onCloseStrategy={(strategy) => void closeStrategy(strategy)} />}
          {view === "positions" && <PositionsPage overview={overview} />}
          {view === "records" && <RecordsPage overview={overview} />}
          {view === "settings" && <SettingsPage overview={overview} wallets={wallets} onReload={() => void load(true)} onAddSelf={() => setWalletModal("self")} />}
        </>
      )}
      {quickWallet && overview && <QuickSettingsModal wallet={quickWallet} strategy={strategyForQuickWallet} account={overview.account} onClose={() => setQuickWallet(null)} onSaved={() => { setQuickWallet(null); void load(true); }} />}
      {walletModal && <WalletModal mode={walletModal} onClose={() => setWalletModal(null)} onSaved={() => { setWalletModal(null); void load(true); }} />}
    </PolyCopyShell>
  );
}
