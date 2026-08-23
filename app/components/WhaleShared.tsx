"use client";

import { ReactNode, useEffect } from "react";

export const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8730"
).replace(/\/$/, "");

export type Numeric = number | string;
export type WhaleRule = "new_account" | "large_amount";

export type WhaleSettings = {
  enabled: boolean;
  window_hours: number;
  registration_window_days: number;
  new_account_threshold_usdc: Numeric;
  large_amount_threshold_usdc: Numeric;
  collect_filter_amount_usdc: Numeric;
  cumulative_threshold_usdc: Numeric;
  single_trade_threshold_usdc: Numeric;
  min_liquidity_usdc: Numeric;
  min_remaining_minutes: number;
  max_price_delta_cents: Numeric;
  max_follow_amount_usdc: Numeric;
  default_follow_amount_usdc: Numeric;
  scan_interval_seconds: number;
  last_scan_at: string | null;
  last_scan_error: string | null;
  consecutive_failures: number;
  tracked_trade_count: number;
  entry_count: number;
  market_count: number;
  new_account_active_count: number;
  new_account_history_count: number;
  large_amount_active_count: number;
  large_amount_history_count: number;
};

export type WhaleTag = {
  id: string;
  slug: string;
  label: string;
  market_count: number;
};

export type WhaleTrade = {
  id?: number;
  side: "BUY" | "SELL" | string;
  size: Numeric;
  price: Numeric;
  amount: Numeric;
  timestamp: string;
  transaction_hash?: string | null;
};

export type WhaleEntry = {
  entry_id: number;
  proxy_wallet: string;
  display_name: string | null;
  wallet_avatar_url: string | null;
  profile_url?: string | null;
  wallet_created_at: string | null;
  wallet_age_days: number | null;
  verified_badge: boolean;
  taker_tier_name: string | null;
  gross_buy_usdc: Numeric;
  gross_buy_size: Numeric;
  net_size: Numeric;
  current_value_usdc: Numeric | null;
  avg_buy_price: Numeric;
  max_single_usdc: Numeric;
  trade_count: number;
  first_buy_at: string;
  last_buy_at: string;
  status: "holding" | "reduced" | "exited" | string;
  net_ratio: Numeric;
  hedged: boolean;
  price_delta_cents: Numeric | null;
  price_delta_percent: Numeric | null;
  matched_rules: WhaleRule[];
  first_triggered_at: string;
  last_qualified_at: string;
  follow_eligible: boolean;
  follow_ineligible_reason: string | null;
  trades?: WhaleTrade[];
};

export type WhaleMarketSide = {
  outcome_index: number;
  outcome: string;
  asset_id: string;
  current_price: Numeric | null;
  best_bid: Numeric | null;
  best_ask: Numeric | null;
  side_total_usdc: Numeric;
  side_wallet_count: number;
  entries: WhaleEntry[];
};

export type WhaleMarket = {
  condition_id: string;
  title: string;
  icon_url: string | null;
  market_slug: string | null;
  event_slug: string | null;
  polymarket_url: string;
  tags: WhaleTag[];
  end_date: string | null;
  remaining_seconds: number | null;
  end_date_is_date_only: boolean;
  liquidity: Numeric;
  volume_24h: Numeric;
  total_whale_usdc: Numeric;
  whale_wallet_count: number;
  both_sides: boolean;
  dominant_outcome_index: number | null;
  side_imbalance_ratio: Numeric | null;
  sides: WhaleMarketSide[];
  trades?: WhaleTrade[];
};

export type WhaleMarketList = {
  generated_at: string;
  window_start: string;
  stale: boolean;
  total: number;
  items: WhaleMarket[];
};

export type WhaleRequestLog = {
  id: number;
  scan_id: string;
  status: "pending" | "success" | "failed";
  started_at: string;
  finished_at: string | null;
  method: string;
  url: string;
  query_params: Record<string, string | string[]>;
  http_status: number | null;
  duration_ms: number | null;
  error_type: string | null;
  error_message: string | null;
  response_excerpt: string | null;
};

export type WhaleRequestLogList = {
  generated_at: string;
  total: number;
  items: WhaleRequestLog[];
};

export type WhaleHistory = {
  entry_id: number;
  rule_type: WhaleRule;
  matched_rules: WhaleRule[];
  proxy_wallet: string;
  display_name: string | null;
  wallet_created_at: string | null;
  wallet_age_days: number | null;
  title: string;
  outcome: string;
  market_slug: string | null;
  event_slug: string | null;
  gross_buy_usdc: Numeric;
  gross_buy_size: Numeric;
  avg_buy_price: Numeric;
  net_size: Numeric;
  first_buy_at: string;
  first_triggered_at: string;
  last_qualified_at: string;
  inactive_at: string | null;
  inactive_reason: string | null;
  threshold_usdc_snapshot: Numeric;
  registration_days_snapshot: number | null;
  settlement_price: Numeric | null;
  settled_at: string | null;
  hold_to_settlement_pnl_usdc: Numeric | null;
};

export type WhaleHistoryList = {
  total: number;
  items: WhaleHistory[];
};

export type WhaleStatisticsRange = "all" | "7d" | "30d" | "90d";
export type WhaleStatisticsRule = "all" | "new_account" | "large_amount" | "both";
export type WhaleStatisticsResult = "all" | "hit" | "miss" | "special";
export type WhaleStatisticsAmountBand = "all" | "lt_100k" | "100k_500k" | "500k_1m" | "gte_1m";
export type WhaleStatisticsSort = "settled_desc" | "amount_desc" | "pnl_desc" | "pnl_asc";

export type WhaleStatisticsMetrics = {
  settled_count: number;
  effective_sample_count: number;
  hit_count: number;
  miss_count: number;
  special_count: number;
  pending_count: number;
  hit_rate_percent: Numeric | null;
  theoretical_cost_usdc: Numeric;
  theoretical_payout_usdc: Numeric;
  theoretical_pnl_usdc: Numeric;
  theoretical_roi_percent: Numeric | null;
  weighted_avg_buy_price: Numeric | null;
  break_even_rate_percent: Numeric | null;
  edge_percentage_points: Numeric | null;
  wallet_count: number;
  market_count: number;
};

export type WhaleStatisticsSlice = {
  key: string;
  label: string;
  metrics: WhaleStatisticsMetrics;
};

export type WhaleStatistics = {
  generated_at: string;
  coverage_start: string | null;
  range: WhaleStatisticsRange;
  range_start: string | null;
  range_end: string;
  overall: WhaleStatisticsMetrics;
  new_account: WhaleStatisticsMetrics;
  large_amount: WhaleStatisticsMetrics;
  dual_match: WhaleStatisticsMetrics;
  trend: WhaleStatisticsSlice[];
  amount_bands: WhaleStatisticsSlice[];
};

export type WhaleStatisticsSignal = {
  entry_id: number;
  result: Exclude<WhaleStatisticsResult, "all">;
  matched_rules: WhaleRule[];
  proxy_wallet: string;
  display_name: string | null;
  profile_url: string;
  wallet_created_at: string | null;
  wallet_age_days_at_trigger: number | null;
  condition_id: string;
  title: string;
  outcome: string;
  market_slug: string | null;
  event_slug: string | null;
  polymarket_url: string;
  gross_buy_usdc: Numeric;
  gross_buy_size: Numeric;
  avg_buy_price: Numeric;
  settlement_price: Numeric;
  theoretical_payout_usdc: Numeric;
  theoretical_pnl_usdc: Numeric;
  theoretical_roi_percent: Numeric | null;
  first_triggered_at: string;
  settled_at: string;
};

export type WhaleStatisticsSignalList = {
  total: number;
  items: WhaleStatisticsSignal[];
};

export type WhaleFollowPreview = {
  confirmation_id: string;
  expires_at: string;
  asset_id: string;
  condition_id: string;
  title: string;
  outcome: string;
  outcome_index: number | null;
  neg_risk: boolean;
  amount_usdc: Numeric;
  best_ask: Numeric;
  worst_price: Numeric;
  tick_size: Numeric;
  minimum_order_usdc: Numeric;
  estimated_shares: Numeric;
  estimated_fee_usdc: Numeric;
  total_cost_usdc: Numeric;
  profit_ratio_percent: Numeric;
  max_loss_usdc: Numeric;
  winning_payout_usdc: Numeric;
  winning_profit_usdc: Numeric;
  immediate_exit_price: Numeric | null;
  immediate_exit_proceeds_usdc: Numeric | null;
  immediate_exit_fee_usdc: Numeric | null;
  immediate_exit_pnl_usdc: Numeric | null;
  immediate_exit_pnl_percent: Numeric | null;
  immediate_exit_unavailable_reason: string | null;
  whale_avg_price: Numeric | null;
  whale_profit_ratio_percent: Numeric | null;
  profit_ratio_gap_percent: Numeric | null;
  price_delta_cents: Numeric | null;
  price_delta_warning: boolean;
  reserve_warning: boolean;
  available_balance_usdc: Numeric;
};

export type WhaleOrder = {
  id: number;
  asset_id: string;
  condition_id: string;
  title: string;
  outcome: string;
  side: "BUY" | "SELL" | string;
  requested_size: Numeric;
  requested_usdc: Numeric;
  reference_price: Numeric | null;
  limit_price: Numeric;
  filled_size: Numeric;
  filled_usdc: Numeric;
  fee_usdc: Numeric;
  status: string;
  reason: string | null;
  external_order_id?: string | null;
  external_trade_id?: string | null;
  created_at: string;
};

export type WhalePosition = {
  id: number;
  asset_id: string;
  condition_id: string;
  title: string;
  outcome: string;
  outcome_index: number | null;
  market_slug: string | null;
  event_slug: string | null;
  icon_url: string | null;
  source_wallet: string | null;
  source_whale_avg_price: Numeric | null;
  size: Numeric;
  avg_cost_price: Numeric | null;
  cost_usdc: Numeric;
  current_price: Numeric | null;
  market_value_usdc: Numeric | null;
  unrealized_pnl: Numeric | null;
  realized_pnl: Numeric;
  total_pnl: Numeric | null;
  lifetime_bought_size: Numeric;
  lifetime_bought_usdc: Numeric;
  lifetime_sold_size: Numeric;
  lifetime_sold_usdc: Numeric;
  lifetime_fee_usdc: Numeric;
  status: string;
  opened_at: string | null;
  closed_at: string | null;
  valuation_status: "ok" | "unavailable" | "not_applicable" | string;
  ledger?: WhaleRecord[];
  records?: WhaleRecord[];
};

export type WhaleRecord = {
  id: number;
  position_id: number;
  order_id: number | null;
  type: "buy" | "sell" | "redeem" | "resolved_loss" | string;
  title: string;
  outcome: string;
  market_slug?: string | null;
  event_slug?: string | null;
  size: Numeric;
  price: Numeric | null;
  amount_usdc: Numeric;
  fee_usdc: Numeric;
  realized_pnl: Numeric;
  transaction_hash: string | null;
  detail: string | null;
  timestamp: string;
};

export type WhaleRecordSummary = {
  total_invested_usdc: Numeric;
  total_proceeds_usdc: Numeric;
  total_fee_usdc: Numeric;
  realized_pnl: Numeric;
  unrealized_pnl: Numeric | null;
  total_pnl: Numeric | null;
  open_position_count: number;
  closed_position_count: number;
  win_count: number;
  loss_count: number;
  win_rate_percent: Numeric | null;
  average_profit_ratio_percent: Numeric | null;
};

export type WhaleRecordList = {
  items: WhaleRecord[];
  total?: number;
  summary: WhaleRecordSummary;
};

export type WhalePositionList = {
  items: WhalePosition[];
  total?: number;
};

export type WhaleSellPreview = {
  confirmation_id: string;
  expires_at: string;
  size: Numeric;
  best_bid: Numeric;
  worst_price: Numeric;
  minimum_order_size: Numeric;
  estimated_proceeds_usdc: Numeric;
  estimated_fee_usdc: Numeric;
  cost_basis_usdc: Numeric;
  estimated_pnl_usdc: Numeric;
  estimated_pnl_percent: Numeric;
};

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function whaleApi<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as
      | { detail?: string | { message?: string }; message?: string }
      | null;
    const detail = payload?.detail;
    const message =
      (typeof detail === "string" ? detail : detail?.message) ||
      payload?.message ||
      `请求失败（${response.status}）`;
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function numeric(value: Numeric | null | undefined): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function formatUsdc(value: Numeric | null | undefined, fallback = "—"): string {
  if (value === null || value === undefined) return fallback;
  return `${new Intl.NumberFormat("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(numeric(value))} USDC`;
}

export function formatCompactUsdc(value: Numeric | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const amount = numeric(value);
  if (Math.abs(amount) >= 1_000_000) return `${(amount / 1_000_000).toFixed(2)}M USDC`;
  if (Math.abs(amount) >= 1_000) return `${(amount / 1_000).toFixed(1)}K USDC`;
  return formatUsdc(amount);
}

export function formatPrice(value: Numeric | null | undefined): string {
  if (value === null || value === undefined) return "—";
  // 保留 4 位：0.9995 这类贴顶报价四舍五入到 3 位会显示成 1，看上去像已结算。
  return numeric(value).toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}

export function formatPercent(value: Numeric | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const amount = numeric(value);
  return `${amount > 0 ? "+" : ""}${amount.toFixed(1)}%`;
}

export function formatSigned(value: Numeric | null | undefined, suffix = ""): string {
  if (value === null || value === undefined) return "—";
  const amount = numeric(value);
  return `${amount > 0 ? "+" : ""}${amount.toFixed(2)}${suffix}`;
}

export function pnlClass(value: Numeric | null | undefined): "profit" | "loss" | "" {
  const amount = numeric(value);
  return amount > 0 ? "profit" : amount < 0 ? "loss" : "";
}

export function formatBeijing(value: string | null | undefined, withSeconds = false): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    ...(withSeconds ? { second: "2-digit" } : {}),
    hour12: false,
  }).format(date);
}

export function remainingLabel(seconds: number | null, dateOnly: boolean): string {
  if (dateOnly) return "结束时间待确认";
  if (seconds === null) return "结束时间未知";
  if (seconds <= 0) return "即将结束";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟后结束`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} 小时 ${minutes % 60} 分后结束`;
  return `${Math.floor(hours / 24)} 天 ${hours % 24} 小时后结束`;
}

export function shortAddress(value: string | null | undefined): string {
  if (!value) return "未知钱包";
  return value.length > 14 ? `${value.slice(0, 7)}…${value.slice(-5)}` : value;
}

export function marketUrl(slug: string | null | undefined, explicit?: string | null): string {
  if (explicit) return explicit;
  return slug ? `https://polymarket.com/event/${slug}` : "https://polymarket.com";
}

export function profileUrl(address: string, explicit?: string | null): string {
  return explicit || `https://polymarket.com/profile/${address}`;
}

export function transactionUrl(hash: string): string {
  return `https://polygonscan.com/tx/${hash}`;
}

export function ModalShell({
  title,
  eyebrow,
  onClose,
  children,
  footer,
  className = "",
}: {
  title: string;
  eyebrow: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
}) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div className="pcModalBackdrop whaleModalBackdrop" role="presentation" onMouseDown={onClose}>
      <section
        className={`pcModal whaleModal ${className}`.trim()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="whale-modal-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span className="pcEyebrow">{eyebrow}</span>
            <h2 id="whale-modal-title">{title}</h2>
          </div>
          <button className="pcIconButton" type="button" aria-label="关闭" onClick={onClose}>
            ×
          </button>
        </header>
        <div className="whaleModalBody">{children}</div>
        {footer && <footer className="whaleModalFooter">{footer}</footer>}
      </section>
    </div>
  );
}
