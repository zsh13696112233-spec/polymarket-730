"use client";

import {
  FormEvent,
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8730"
).replace(/\/$/, "");

type Numeric = string | number;

type GlobalSettings = {
  copy_ratio_percent: Numeric;
};

type ExecutionAccount = {
  wallet_id: number | string;
  signer_address: string | null;
  funder_address?: string | null;
  signature_type?: number;
  status: string;
  credentials_configured: boolean;
  budget_usdc: Numeric;
  cash_reserve_usdc: Numeric;
  max_total_exposure_usdc: Numeric;
  daily_buy_limit_usdc: Numeric;
  daily_loss_limit_usdc: Numeric;
  auto_redeem: boolean;
  collateral_balance: Numeric | null;
  last_error: string | null;
};

type CopySubscription = {
  id: number | string;
  tracked_wallet_id: number | string;
  tracked_wallet_label: string | null;
  enabled: boolean;
  state:
    | "active"
    | "paused"
    | "exit_only"
    | "closing"
    | "disabled"
    | "error";
  copy_ratio_percent: Numeric;
  position_cap_usdc: Numeric;
  total_exposure_cap_usdc: Numeric;
  market_slippage_cents: Numeric;
  open_exposure_usdc: Numeric;
  daily_bought_usdc: Numeric;
  daily_realized_pnl: Numeric;
  last_error: string | null;
};

type CopyPosition = {
  id: number | string;
  title: string;
  outcome: string;
  event_slug: string | null;
  cycle_no: number;
  attributed_size: Numeric;
  attributed_cost: Numeric;
  realized_pnl: Numeric;
  status: string;
  updated_at: string;
  average_entry_price: Numeric | null;
  current_bid: Numeric | null;
  current_value: Numeric | null;
  unrealized_pnl: Numeric | null;
  unrealized_pnl_percent: Numeric | null;
  total_pnl: Numeric | null;
  lifetime_bought_size: Numeric;
  lifetime_bought_usdc: Numeric;
  lifetime_sold_size: Numeric;
  lifetime_sold_usdc: Numeric;
  lifetime_average_buy_price: Numeric | null;
  valuation_status: "ok" | "unavailable" | "not_applicable";
  valued_at: string | null;
};

type CopyPortfolioSummary = {
  open_cost_usdc: Numeric;
  market_value_usdc: Numeric | null;
  unrealized_pnl: Numeric | null;
  realized_pnl: Numeric;
  total_pnl: Numeric | null;
  valuation_complete: boolean;
  unpriced_positions: number;
  valued_at: string | null;
};

type CopyOrder = {
  id: number | string;
  side: "BUY" | "SELL";
  requested_usdc: Numeric;
  filled_size: Numeric;
  filled_usdc: Numeric;
  reference_price: Numeric | null;
  limit_price: Numeric;
  source: "copy" | "rehearsal";
  status: string;
  reason: string | null;
  created_at: string;
};

type RehearsalPreview = {
  confirmation_id: string;
  market_url: string;
  asset_id: string;
  condition_id: string;
  title: string;
  outcome: string;
  best_ask: Numeric;
  fee_rate_bps: number;
  max_total_usdc: Numeric;
  expires_at: string;
};

type CopyDashboard = {
  live_copy_enabled: boolean;
  account: ExecutionAccount | null;
  subscription: CopySubscription | null;
  positions: CopyPosition[];
  orders: CopyOrder[];
  portfolio?: CopyPortfolioSummary;
};

type CopyRecommendation = {
  action: "buy" | "sell";
  ratio_percent: Numeric;
  shares: Numeric;
  estimated_usdc: Numeric | null;
};

type Wallet = {
  id: number | string;
  address: string;
  proxy_wallet: string;
  label: string;
  wallet_role: "self" | "tracked";
  enabled: boolean;
  status: string;
  last_success_at: string | null;
  last_error: string | null;
  created_at: string;
};

type Position = {
  wallet_id: number | string;
  asset_id: string;
  condition_id: string;
  title: string;
  outcome: string;
  icon_url: string | null;
  event_slug: string | null;
  market_slug: string | null;
  size: Numeric;
  avg_price: Numeric;
  current_price: Numeric;
  initial_value: Numeric;
  current_value: Numeric;
  cash_pnl: Numeric;
  percent_pnl: Numeric;
  end_date: string | null;
  first_opened_at: string | null;
  first_opened_at_source: "trade" | "first_seen";
  opened_date: string | null;
  cycle_trades: PositionCycleTrade[];
  cycle_history_complete: boolean;
  purchase_lots?: PurchaseLot[];
};

type PositionCycleTrade = {
  id: number | string;
  type: "opened" | "increased" | "decreased";
  size: Numeric;
  price: Numeric;
  amount: Numeric;
  timestamp: string;
  transaction_hash: string | null;
};

type PurchaseLot = {
  purchase_date: string;
  size: Numeric;
  avg_price: Numeric;
  initial_value: Numeric;
  current_value: Numeric;
  cash_pnl: Numeric;
  percent_pnl: Numeric;
};

type PositionSummary = {
  current_value: Numeric;
  initial_value: Numeric;
  cash_pnl: Numeric;
  count: number;
};

type PositionResponse = {
  items: Position[];
  summary: PositionSummary;
  opened_dates?: string[];
  purchase_dates?: string[];
  purchase_history_complete?: boolean;
  purchase_history_error?: string | null;
  as_of: string | null;
  stale: boolean;
};

type PositionOverlap = {
  asset_id: string;
  condition_id: string;
  my_size: Numeric;
  tracked_size: Numeric;
  my_to_tracked_percent: Numeric;
  my_ratio: Numeric;
  tracked_ratio: Numeric;
};

type PositionOverlapsResponse = {
  my_wallet_id: number | string | null;
  tracked_wallet_id: number | string;
  items: PositionOverlap[];
  overlap_count: number;
  my_as_of: string | null;
  tracked_as_of: string | null;
  my_stale: boolean | null;
  tracked_stale: boolean;
};

type PositionOverlapDetail = {
  my_wallet: Wallet;
  tracked_wallet: Wallet;
  mine: Position;
  tracked: Position;
  my_to_tracked_percent: Numeric;
  my_ratio: Numeric;
  tracked_ratio: Numeric;
  my_stale: boolean;
  tracked_stale: boolean;
};

type PositionOverlapAlert = {
  id: number | string;
  my_wallet_id: number | string;
  tracked_wallet_id: number | string;
  asset_id: string;
  condition_id: string;
  title: string;
  outcome: string;
  event_slug: string | null;
  market_slug: string | null;
  type: "increased" | "decreased" | "closed";
  before_size: Numeric;
  after_size: Numeric;
  delta_size: Numeric;
  detected_at: string;
  created_at: string;
  read_at: string | null;
  copy_recommendation?: CopyRecommendation;
};

type PositionOverlapAlertsResponse = {
  items: PositionOverlapAlert[];
  unread_count: number;
};

type Fill = {
  id: number | string;
  side: string;
  size: Numeric;
  price: Numeric;
  amount: Numeric;
  timestamp: string;
  transaction_hash: string | null;
};

type PositionEvent = {
  id: number | string;
  wallet_id: number | string;
  asset_id: string;
  type: "opened" | "increased" | "decreased" | "closed" | "redeemed";
  title: string;
  outcome: string;
  event_slug: string | null;
  market_slug?: string | null;
  delta_size: Numeric;
  before_size: Numeric;
  after_size: Numeric;
  before_avg_price: Numeric | null;
  after_avg_price: Numeric | null;
  average_fill_price: Numeric | null;
  current_value: Numeric;
  reconciliation_status: string;
  first_detected_at: string;
  settled_at: string;
  payout_amount: Numeric | null;
  redemption_cost_basis: Numeric | null;
  redemption_entry_price: Numeric | null;
  redemption_price: Numeric | null;
  redemption_profit: Numeric | null;
  redemption_profit_percent: Numeric | null;
  redemption_cost_complete: boolean | null;
  transaction_hash: string | null;
  fills: Fill[];
  copy_recommendation?: CopyRecommendation | null;
};

type EventsResponse = {
  items: PositionEvent[];
  next_cursor: number | string | null;
};

type View = "positions" | "events";

const emptySummary: PositionSummary = {
  current_value: "0",
  initial_value: "0",
  cash_pnl: "0",
  count: 0,
};

const moneyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const redemptionMoneyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 6,
});

const compactNumberFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

function toNumber(value: string | number | null | undefined) {
  const number = Number(value ?? 0);
  return Number.isFinite(number) ? number : 0;
}

function formatMoney(value: string | number | null | undefined) {
  return moneyFormatter.format(toNumber(value));
}

function formatOptionalMoney(value: string | number | null | undefined) {
  return value === null || value === undefined ? "—" : formatMoney(value);
}

function formatOptionalPrice(value: string | number | null | undefined) {
  return value === null || value === undefined ? "—" : formatPrice(value);
}

function formatRedemptionMoney(
  value: string | number | null | undefined,
) {
  return redemptionMoneyFormatter.format(toNumber(value));
}

function formatRedemptionPrice(
  value: string | number | null | undefined,
) {
  return toNumber(value).toFixed(6);
}

function formatShares(value: string | number | null | undefined) {
  return compactNumberFormatter.format(toNumber(value));
}

function formatSuggestedShares(
  value: string | number | null | undefined,
) {
  const number = toNumber(value);
  if (number > 0 && number < 0.0001) return "<0.0001";
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 4,
  }).format(number);
}

function formatRatioSetting(value: Numeric) {
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(toNumber(value));
}

function formatPrice(value: string | number | null | undefined) {
  const cents = toNumber(value) * 100;
  const digits = cents > 0 && cents < 0.1 ? 2 : 1;
  return `${new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  }).format(cents)}¢`;
}

function formatPercent(value: string | number | null | undefined) {
  const number = toNumber(value);
  return `${number >= 0 ? "+" : ""}${number.toFixed(2)}%`;
}

function formatOverlapPercent(value: Numeric) {
  const number = toNumber(value);
  if (number > 0 && number < 0.01) return "<0.01%";
  return `${new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(number)}%`;
}

function formatRatioPart(value: Numeric) {
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(toNumber(value));
}

function formatDateTime(value: string | null | undefined) {
  if (!value) return "等待首次同步";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function formatPurchaseDate(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return value;
  return `${year}年${month}月${day}日`;
}

function formatPositionDateTime(value: string | null | undefined) {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "Asia/Shanghai",
  }).format(date);
}

function shortenAddress(address: string) {
  if (address.length < 14) return address;
  return `${address.slice(0, 6)}…${address.slice(-4)}`;
}

function marketUrl(
  eventSlug: string | null | undefined,
  marketSlug?: string | null,
) {
  if (!eventSlug) return "https://polymarket.com";
  const eventPath = encodeURIComponent(eventSlug);
  if (marketSlug) {
    return `https://polymarket.com/event/${eventPath}/${encodeURIComponent(marketSlug)}`;
  }
  return `https://polymarket.com/event/${eventPath}`;
}

function outcomeTone(outcome: string) {
  const normalized = outcome.trim().toLowerCase();
  if (["yes", "是", "上涨", "over"].includes(normalized)) return "positive";
  if (["no", "否", "下跌", "under"].includes(normalized)) return "negative";
  return "neutral";
}

function eventLabel(type: PositionEvent["type"]) {
  return {
    opened: "新建仓",
    increased: "加仓",
    decreased: "减仓",
    closed: "清仓",
    redeemed: "赎回",
  }[type];
}

function eventTone(type: PositionEvent["type"]) {
  if (type === "redeemed") return "redeemed";
  return type === "opened" || type === "increased" ? "positive" : "negative";
}

function syncStatus(status: string, streamConnected: boolean) {
  if (status === "syncing") {
    return { label: "正在同步", tone: "pending" };
  }
  if (status === "error") {
    return { label: "同步异常", tone: "error" };
  }
  if (!streamConnected) {
    return { label: "正在重连", tone: "pending" };
  }
  if (status === "pending" || status === "never_synced") {
    return { label: "等待同步", tone: "pending" };
  }
  return { label: "实时监控", tone: "live" };
}

async function readError(response: Response) {
  try {
    const payload = (await response.json()) as {
      detail?: string;
      message?: string;
    };
    return payload.detail || payload.message || `请求失败（${response.status}）`;
  } catch {
    return `请求失败（${response.status}）`;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(await readError(response));
  }
  const body = await response.text();
  return (body ? JSON.parse(body) : undefined) as T;
}

function PnlValue({
  value,
  percent,
  compact = false,
}: {
  value: Numeric;
  percent?: Numeric;
  compact?: boolean;
}) {
  const tone = toNumber(value) >= 0 ? "profit" : "loss";
  return (
    <span className={`pnlValue ${tone}`}>
      {formatMoney(value)}
      {percent !== undefined && (
        <small>{compact ? " " : <br />}{formatPercent(percent)}</small>
      )}
    </span>
  );
}

function MarketTitle({
  title,
  outcome,
  eventSlug,
  marketSlug,
}: {
  title: string;
  outcome: string;
  eventSlug: string | null;
  marketSlug?: string | null;
}) {
  return (
    <div className="marketIdentity">
      <span className={`outcomeBadge ${outcomeTone(outcome)}`}>{outcome}</span>
      <a
        className="marketTitle"
        href={marketUrl(eventSlug, marketSlug)}
        target="_blank"
        rel="noopener noreferrer"
        title="在 Polymarket 打开"
      >
        {title}
        <span aria-hidden="true" className="externalMark">
          ↗
        </span>
      </a>
    </div>
  );
}

function OverlapBadge({
  overlap,
  onOpen,
}: {
  overlap: PositionOverlap;
  onOpen: () => void;
}) {
  return (
    <button
      className="overlapBadge"
      type="button"
      onClick={onOpen}
      aria-label={`查看共同持仓对比，我的仓位是他的 ${formatOverlapPercent(
        overlap.my_to_tracked_percent,
      )}`}
    >
      <span aria-hidden="true">◆</span>
      共同持仓 · 我的/他的{" "}
      {formatOverlapPercent(overlap.my_to_tracked_percent)}
    </button>
  );
}

function ChangeAlertBadge({
  alert,
  onRead,
}: {
  alert: PositionOverlapAlert;
  onRead: () => void;
}) {
  const increased = alert.type === "increased";
  const action = increased ? "加仓" : "减仓";
  return (
    <button
      className={`changeAlertBadge ${increased ? "increased" : "decreased"}`}
      type="button"
      onClick={onRead}
      aria-label={`对方刚${action}，从 ${formatShares(
        alert.before_size,
      )} 变为 ${formatShares(alert.after_size)}，标为已读`}
    >
      <span aria-hidden="true">!</span>
      对方刚{action} {formatShares(alert.before_size)} →{" "}
      {formatShares(alert.after_size)}
    </button>
  );
}

function CopyRecommendationView({
  recommendation,
  compact = false,
}: {
  recommendation: CopyRecommendation;
  compact?: boolean;
}) {
  const action = recommendation.action === "buy" ? "买入" : "卖出";
  return (
    <div className={`copyRecommendation ${compact ? "compact" : ""}`}>
      <span>
        {formatRatioSetting(recommendation.ratio_percent)}% 跟单建议
      </span>
      <strong className={recommendation.action}>
        {action} {formatSuggestedShares(recommendation.shares)} shares
      </strong>
      <small>
        {recommendation.estimated_usdc === null
          ? "成交价缺失，仅供份额参考"
          : `估算 ${formatRedemptionMoney(
              recommendation.estimated_usdc,
            )} USDC`}
      </small>
    </div>
  );
}

function CurrentPositionCopyTarget({
  position,
  ratioPercent,
}: {
  position: Position;
  ratioPercent: Numeric;
}) {
  const ratio = toNumber(ratioPercent);
  const shares = (toNumber(position.size) * ratio) / 100;
  const estimatedUsdc = shares * toNumber(position.current_price);
  return (
    <div className="currentCopyTarget">
      <span>{formatRatioSetting(ratioPercent)}% 跟单目标</span>
      <strong>{formatSuggestedShares(shares)} shares</strong>
      <small>
        {toNumber(position.current_price) > 0
          ? `按现价约 ${formatRedemptionMoney(estimatedUsdc)} USDC`
          : "当前价格缺失"}
      </small>
    </div>
  );
}

function PositionCycleDetails({ position }: { position: Position }) {
  return (
    <div className="positionCycleDetails">
      <div className="positionCycleHeading">
        <div>
          <span>当前建仓周期</span>
          <strong>{formatPurchaseDate(position.opened_date ?? "")}</strong>
        </div>
        <small>以下明细均按北京时间显示</small>
      </div>
      {!position.cycle_history_complete && (
        <p className="cycleHistoryWarning">
          历史成交尚未完整回填，建仓时间以首次监测为准，下面仅展示已识别成交。
        </p>
      )}
      {position.cycle_trades.length > 0 ? (
        <div className="cycleTradeList">
          {position.cycle_trades.map((trade) => (
            <div className="cycleTradeRow" key={trade.id}>
              <span className={`cycleTradeType ${trade.type}`}>
                {trade.type === "opened"
                  ? "建仓"
                  : trade.type === "increased"
                    ? "加仓"
                    : "减仓"}
              </span>
              <time dateTime={trade.timestamp}>
                {formatPositionDateTime(trade.timestamp)}
              </time>
              <span>{formatShares(trade.size)} 份</span>
              <span>× {formatPrice(trade.price)}</span>
              <strong>{formatMoney(trade.amount)}</strong>
              {trade.transaction_hash ? (
                <a
                  className="cycleTxLink"
                  href={`https://polygonscan.com/tx/${trade.transaction_hash}`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {shortenAddress(trade.transaction_hash)}
                  <span aria-hidden="true">↗</span>
                </a>
              ) : (
                <span className="cycleTxLink empty">无交易哈希</span>
              )}
            </div>
          ))}
        </div>
      ) : (
        <p className="emptyCycleTrades">暂未匹配到本周期逐笔成交。</p>
      )}
    </div>
  );
}

function PositionTable({
  positions,
  overlaps,
  changeAlerts,
  copyRatioPercent,
  onOpenComparison,
  onReadAlert,
}: {
  positions: Position[];
  overlaps: Map<string, PositionOverlap>;
  changeAlerts: Map<string, PositionOverlapAlert>;
  copyRatioPercent: Numeric;
  onOpenComparison: (assetId: string) => void;
  onReadAlert: (alertId: number | string) => void;
}) {
  const [expandedPositions, setExpandedPositions] = useState<Set<string>>(
    new Set(),
  );

  function positionKey(position: Position) {
    return `${position.wallet_id}-${position.asset_id}`;
  }

  function toggleDetails(position: Position) {
    const key = positionKey(position);
    setExpandedPositions((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <>
      <div className="tableWrap desktopPositions">
        <table className="positionsTable">
          <thead>
            <tr>
              <th>市场 / 方向</th>
              <th className="numberCell">平均买入</th>
              <th className="numberCell">当前价格</th>
              <th>建仓日期</th>
              <th>初次建仓</th>
              <th className="numberCell">持仓份额</th>
              <th>跟单目标</th>
              <th className="numberCell">持仓成本</th>
              <th className="numberCell sortedColumn">
                当前市值 <span aria-hidden="true">↓</span>
              </th>
              <th className="numberCell">浮动盈亏</th>
              <th>
                <span className="srOnly">操作</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {positions.map((position) => {
              const overlap = overlaps.get(position.asset_id);
              const changeAlert = changeAlerts.get(position.asset_id);
              const key = positionKey(position);
              const expanded = expandedPositions.has(key);
              const detailId = `position-cycle-desktop-${key}`;
              return (
                <Fragment key={`${position.asset_id}-${position.condition_id}`}>
                  <tr className={overlap ? "overlapRow" : ""}>
                  <td className="marketCell">
                    <div className="marketPositionIdentity">
                      <MarketTitle
                        title={position.title}
                        outcome={position.outcome}
                        eventSlug={position.event_slug}
                        marketSlug={position.market_slug}
                      />
                      {overlap && (
                        <OverlapBadge
                          overlap={overlap}
                          onOpen={() => onOpenComparison(position.asset_id)}
                        />
                      )}
                      {overlap && changeAlert && (
                        <ChangeAlertBadge
                          alert={changeAlert}
                          onRead={() => onReadAlert(changeAlert.id)}
                        />
                      )}
                    </div>
                  </td>
                  <td className="numberCell mutedNumber">
                    {formatPrice(position.avg_price)}
                  </td>
                  <td className="numberCell">
                    {formatPrice(position.current_price)}
                  </td>
                  <td className="purchaseDateCell">
                    {position.opened_date
                      ? formatPurchaseDate(position.opened_date)
                      : "日期待回填"}
                  </td>
                  <td className="openedAtCell">
                    <time dateTime={position.first_opened_at ?? undefined}>
                      {formatPositionDateTime(position.first_opened_at)}
                    </time>
                    {position.first_opened_at_source === "first_seen" && (
                      <small>首次监测</small>
                    )}
                    <button
                      className="positionDetailToggle"
                      type="button"
                      aria-expanded={expanded}
                      aria-controls={detailId}
                      onClick={() => toggleDetails(position)}
                    >
                      {expanded ? "收起明细" : "查看明细"}
                      <span aria-hidden="true">⌄</span>
                    </button>
                  </td>
                  <td className="numberCell">
                    {formatShares(position.size)}
                  </td>
                  <td className="copyTargetCell">
                    <CurrentPositionCopyTarget
                      position={position}
                      ratioPercent={copyRatioPercent}
                    />
                  </td>
                  <td className="numberCell mutedNumber">
                    {formatMoney(position.initial_value)}
                  </td>
                  <td className="numberCell strongNumber">
                    {formatMoney(position.current_value)}
                  </td>
                  <td className="numberCell">
                    <PnlValue
                      value={position.cash_pnl}
                      percent={position.percent_pnl}
                    />
                  </td>
                  <td className="actionCell">
                    <a
                      className="marketButton"
                      href={marketUrl(
                        position.event_slug,
                        position.market_slug,
                      )}
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label={`前往 ${position.title} 市场`}
                    >
                      去市场
                    </a>
                  </td>
                  </tr>
                  {expanded && (
                    <tr className="positionDetailRow">
                      <td colSpan={11} id={detailId}>
                        <PositionCycleDetails position={position} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="positionCards">
        {positions.map((position) => {
          const overlap = overlaps.get(position.asset_id);
          const changeAlert = changeAlerts.get(position.asset_id);
          const key = positionKey(position);
          const expanded = expandedPositions.has(key);
          const detailId = `position-cycle-mobile-${key}`;
          return (
            <article
              className={`positionCard ${overlap ? "overlapCard" : ""}`}
              key={`card-${position.asset_id}-${position.condition_id}`}
            >
              <div className="marketPositionIdentity">
                <MarketTitle
                  title={position.title}
                  outcome={position.outcome}
                  eventSlug={position.event_slug}
                  marketSlug={position.market_slug}
                />
                {overlap && (
                  <OverlapBadge
                    overlap={overlap}
                    onOpen={() => onOpenComparison(position.asset_id)}
                  />
                )}
                {overlap && changeAlert && (
                  <ChangeAlertBadge
                    alert={changeAlert}
                    onRead={() => onReadAlert(changeAlert.id)}
                  />
                )}
              </div>
              <div className="cardHeroValue">
                <span>当前市值</span>
                <strong>{formatMoney(position.current_value)}</strong>
              </div>
              <dl className="positionFacts">
                <div>
                  <dt>平均买入</dt>
                  <dd>{formatPrice(position.avg_price)}</dd>
                </div>
                <div>
                  <dt>当前价格</dt>
                  <dd>{formatPrice(position.current_price)}</dd>
                </div>
                <div>
                  <dt>建仓日期</dt>
                  <dd>
                    {position.opened_date
                      ? formatPurchaseDate(position.opened_date)
                      : "日期待回填"}
                  </dd>
                </div>
                <div>
                  <dt>初次建仓</dt>
                  <dd className="cardOpenedAt">
                    <time dateTime={position.first_opened_at ?? undefined}>
                      {formatPositionDateTime(position.first_opened_at)}
                    </time>
                    {position.first_opened_at_source === "first_seen" && (
                      <small>首次监测</small>
                    )}
                  </dd>
                </div>
                <div>
                  <dt>持仓份额</dt>
                  <dd>{formatShares(position.size)}</dd>
                </div>
                <div>
                  <dt>持仓成本</dt>
                  <dd>{formatMoney(position.initial_value)}</dd>
                </div>
                <div className="cardPnl">
                  <dt>浮动盈亏</dt>
                  <dd>
                    <PnlValue
                      compact
                      value={position.cash_pnl}
                      percent={position.percent_pnl}
                    />
                  </dd>
                </div>
              </dl>
              <button
                className="positionDetailToggle mobile"
                type="button"
                aria-expanded={expanded}
                aria-controls={detailId}
                onClick={() => toggleDetails(position)}
              >
                {expanded ? "收起建仓明细" : "查看建仓明细"}
                <span aria-hidden="true">⌄</span>
              </button>
              {expanded && (
                <div id={detailId}>
                  <PositionCycleDetails position={position} />
                </div>
              )}
              <CurrentPositionCopyTarget
                position={position}
                ratioPercent={copyRatioPercent}
              />
              <a
                className="mobileMarketButton"
                href={marketUrl(position.event_slug, position.market_slug)}
                target="_blank"
                rel="noopener noreferrer"
              >
                前往 Polymarket 下单
                <span aria-hidden="true">↗</span>
              </a>
            </article>
          );
        })}
      </div>
    </>
  );
}

function ReconciliationStatus({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  const aligned = ["matched", "aligned", "complete", "reconciled"].includes(
    normalized,
  );
  return (
    <span className={`reconcileStatus ${aligned ? "aligned" : "partial"}`}>
      {aligned ? "成交已对齐" : "成交明细未完全对齐"}
    </span>
  );
}

function EventList({ events }: { events: PositionEvent[] }) {
  return (
    <div className="eventList">
      {events.map((event) => {
        const delta = toNumber(event.delta_size);
        const redeemed = event.type === "redeemed";
        const redemptionCostComplete =
          redeemed && event.redemption_cost_complete === true;
        return (
          <details
            className={`eventCard ${redeemed ? "redemptionEventCard" : ""}`}
            key={event.id}
          >
            <summary>
              <div className="eventSummaryMain">
                <div className="eventBadges">
                  <span className={`eventType ${eventTone(event.type)}`}>
                    {eventLabel(event.type)}
                  </span>
                  <span
                    className={`outcomeBadge ${outcomeTone(event.outcome)}`}
                  >
                    {event.outcome}
                  </span>
                </div>
                <a
                  href={marketUrl(event.event_slug, event.market_slug)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="eventMarketTitle"
                  onClick={(eventClick) => eventClick.stopPropagation()}
                >
                  {event.title}
                  <span aria-hidden="true">↗</span>
                </a>
              </div>

              <div className="eventDelta">
                <span>{redeemed ? "赎回份额" : "份额变化"}</span>
                <strong
                  className={
                    redeemed ? "redeemedValue" : delta >= 0 ? "profit" : "loss"
                  }
                >
                  {redeemed
                    ? formatShares(event.before_size)
                    : `${delta > 0 ? "+" : ""}${formatShares(event.delta_size)}`}
                </strong>
              </div>

              <div className="eventPrice">
                <span>{redeemed ? "入手价 → 赎回价" : "合并成交均价"}</span>
                <strong>
                  {redeemed
                    ? `${
                        redemptionCostComplete &&
                        event.redemption_entry_price != null
                          ? formatRedemptionPrice(
                              event.redemption_entry_price,
                            )
                          : "—"
                      } → ${
                        event.redemption_price != null
                          ? formatRedemptionPrice(event.redemption_price)
                          : "—"
                      }`
                    : event.average_fill_price
                    ? formatPrice(event.average_fill_price)
                    : "—"}
                </strong>
              </div>

              {!redeemed && event.copy_recommendation && (
                <CopyRecommendationView
                  recommendation={event.copy_recommendation}
                  compact
                />
              )}

              {redeemed && (
                <div className="eventProfit">
                  <span>本次赎回盈利</span>
                  {redemptionCostComplete &&
                  event.redemption_profit != null ? (
                    <strong
                      className={
                        toNumber(event.redemption_profit) >= 0
                          ? "profit"
                          : "loss"
                      }
                    >
                      {formatRedemptionMoney(event.redemption_profit)}
                      {event.redemption_profit_percent != null && (
                        <small>
                          {formatPercent(event.redemption_profit_percent)}
                        </small>
                      )}
                    </strong>
                  ) : (
                    <strong className="costUnavailable">—</strong>
                  )}
                </div>
              )}

              <div className="eventTime">
                <time dateTime={event.settled_at}>
                  {formatDateTime(event.settled_at)}
                </time>
                <span className="expandHint">
                  <span className="expandClosed">查看明细</span>
                  <span className="expandOpen">收起明细</span>
                  <b aria-hidden="true">⌄</b>
                </span>
              </div>
            </summary>

            <div className="eventDetail">
              {redeemed ? (
                <>
                  <div className="eventFacts redemptionFacts">
                    <div>
                      <span>赎回份额</span>
                      <strong>{formatShares(event.before_size)}</strong>
                    </div>
                    <div>
                      <span>入手价</span>
                      <strong>
                        {redemptionCostComplete &&
                        event.redemption_entry_price != null
                          ? `${formatRedemptionPrice(
                              event.redemption_entry_price,
                            )} USDC`
                          : "—"}
                      </strong>
                    </div>
                    <div>
                      <span>赎回价</span>
                      <strong>
                        {event.redemption_price != null
                          ? `${formatRedemptionPrice(
                              event.redemption_price,
                            )} USDC`
                          : "—"}
                      </strong>
                    </div>
                    <div>
                      <span>持仓成本</span>
                      <strong>
                        {redemptionCostComplete &&
                        event.redemption_cost_basis != null
                          ? formatRedemptionMoney(
                              event.redemption_cost_basis,
                            )
                          : "—"}
                      </strong>
                    </div>
                    <div>
                      <span>到账 USDC</span>
                      <strong>
                        {event.payout_amount !== null
                          ? formatRedemptionMoney(event.payout_amount)
                          : "—"}
                      </strong>
                    </div>
                    <div>
                      <span>本次赎回盈利</span>
                      {redemptionCostComplete &&
                      event.redemption_profit != null ? (
                        <strong
                          className={
                            toNumber(event.redemption_profit) >= 0
                              ? "profit"
                              : "loss"
                          }
                        >
                          {formatRedemptionMoney(event.redemption_profit)}
                          {event.redemption_profit_percent != null && (
                            <small>
                              {formatPercent(
                                event.redemption_profit_percent,
                              )}
                            </small>
                          )}
                        </strong>
                      ) : (
                        <strong className="costUnavailable">
                          成本数据不完整
                        </strong>
                      )}
                    </div>
                    <span className="reconcileStatus aligned">链上已确认</span>
                  </div>

                  <div className="fillsBlock">
                    <div className="fillsHeading">
                      <h3>链上赎回</h3>
                      <span>1 笔</span>
                    </div>
                    <div className="fillRows">
                      <div className="fillRow redemptionRow">
                        <span className="fillSide redeemed">赎回</span>
                        <time dateTime={event.settled_at}>
                          {formatDateTime(event.settled_at)}
                        </time>
                        <span>{formatShares(event.before_size)} 份</span>
                        <strong>
                          {event.payout_amount !== null
                            ? formatRedemptionMoney(event.payout_amount)
                            : "—"}
                        </strong>
                        {event.transaction_hash ? (
                          <a
                            href={`https://polygonscan.com/tx/${event.transaction_hash}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="txLink"
                          >
                            {shortenAddress(event.transaction_hash)}
                            <span aria-hidden="true">↗</span>
                          </a>
                        ) : (
                          <span className="txLink empty">无交易哈希</span>
                        )}
                      </div>
                    </div>
                  </div>
                </>
              ) : (
                <>
                  <div className="eventFacts">
                    <div>
                      <span>变化前份额</span>
                      <strong>{formatShares(event.before_size)}</strong>
                    </div>
                    <span className="factArrow" aria-hidden="true">
                      →
                    </span>
                    <div>
                      <span>变化后份额</span>
                      <strong>{formatShares(event.after_size)}</strong>
                    </div>
                    <div>
                      <span>变化后市值</span>
                      <strong>{formatMoney(event.current_value)}</strong>
                    </div>
                    {event.copy_recommendation && (
                      <CopyRecommendationView
                        recommendation={event.copy_recommendation}
                      />
                    )}
                    <ReconciliationStatus
                      status={event.reconciliation_status}
                    />
                  </div>

                  <div className="fillsBlock">
                    <div className="fillsHeading">
                      <h3>逐笔成交</h3>
                      <span>{event.fills.length} 笔</span>
                    </div>
                    {event.fills.length === 0 ? (
                      <p className="noFills">
                        暂未匹配到逐笔成交，以持仓份额变化为准。
                      </p>
                    ) : (
                      <div className="fillRows">
                        {event.fills.map((fill) => (
                          <div className="fillRow" key={fill.id}>
                            <span
                              className={`fillSide ${
                                fill.side.toUpperCase() === "BUY"
                                  ? "positive"
                                  : "negative"
                              }`}
                            >
                              {fill.side.toUpperCase() === "BUY"
                                ? "买入"
                                : "卖出"}
                            </span>
                            <time dateTime={fill.timestamp}>
                              {formatDateTime(fill.timestamp)}
                            </time>
                            <span>
                              {formatShares(fill.size)} 份 ×{" "}
                              {formatPrice(fill.price)}
                            </span>
                            <strong>{formatMoney(fill.amount)}</strong>
                            {fill.transaction_hash ? (
                              <a
                                href={`https://polygonscan.com/tx/${fill.transaction_hash}`}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="txLink"
                              >
                                {shortenAddress(fill.transaction_hash)}
                                <span aria-hidden="true">↗</span>
                              </a>
                            ) : (
                              <span className="txLink empty">无交易哈希</span>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          </details>
        );
      })}
    </div>
  );
}

function LoadingState({ label }: { label: string }) {
  return (
    <div className="loadingState" role="status">
      <span className="loadingDot" />
      <span>{label}</span>
    </div>
  );
}

function EmptyState({
  type,
  onRefresh,
}: {
  type: "positions" | "events";
  onRefresh?: () => void;
}) {
  return (
    <div className="emptyState">
      <span className="emptyMark" aria-hidden="true">
        {type === "positions" ? "0" : "·"}
      </span>
      <h2>{type === "positions" ? "当前没有活跃持仓" : "还没有仓位变动记录"}</h2>
      <p>
        {type === "positions"
          ? "已结算和可领取仓位不会显示在这里。"
          : "首次同步只建立持仓基线，之后的份额变化会安静地记录在这里。"}
      </p>
      {onRefresh && (
        <button className="secondaryButton" type="button" onClick={onRefresh}>
          重新同步
        </button>
      )}
    </div>
  );
}

function AddWalletModal({
  open,
  mode,
  onClose,
  onCreated,
}: {
  open: boolean;
  mode: "self" | "tracked";
  onClose: () => void;
  onCreated: (wallet: Wallet) => void;
}) {
  const [address, setAddress] = useState("");
  const [label, setLabel] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const addressInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    addressInput.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !submitting) onClose();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [onClose, open, submitting]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedAddress = address.trim();
    if (!trimmedAddress) {
      setError("请输入钱包地址或 Polymarket 个人页链接");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const wallet = await request<Wallet>(
        mode === "self" ? "/api/my-wallet" : "/api/wallets",
        {
          method: mode === "self" ? "PUT" : "POST",
          body: JSON.stringify({
            address: trimmedAddress,
            ...(label.trim() ? { label: label.trim() } : {}),
          }),
        },
      );
      onCreated(wallet);
    } catch (submitError) {
      setError(
        submitError instanceof Error
          ? submitError.message
          : mode === "self"
            ? "设置我的钱包失败"
            : "添加钱包失败",
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) return null;

  return (
    <div
      className="modalBackdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !submitting) onClose();
      }}
    >
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${mode}-wallet-title`}
      >
        <div className="modalHeader">
          <div>
            <span className="eyebrow">
              {mode === "self" ? "只读对比基准" : "新增监控"}
            </span>
            <h2 id={`${mode}-wallet-title`}>
              {mode === "self" ? "设置我的钱包" : "添加 Polymarket 钱包"}
            </h2>
          </div>
          <button
            className="closeButton"
            type="button"
            aria-label="关闭"
            onClick={onClose}
            disabled={submitting}
          >
            ×
          </button>
        </div>
        <form onSubmit={submit}>
          <label className="field">
            <span>钱包地址或个人页链接</span>
            <input
              ref={addressInput}
              value={address}
              onChange={(event) => setAddress(event.target.value)}
              placeholder="0x… 或 polymarket.com/profile/0x…"
              autoComplete="off"
              spellCheck={false}
              disabled={submitting}
            />
          </label>
          <label className="field">
            <span>
              钱包备注 <small>可选</small>
            </span>
            <input
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              placeholder="例如：主观察钱包"
              autoComplete="off"
              disabled={submitting}
            />
          </label>
          <p className="privacyNote">
            {mode === "self"
              ? "该地址仅作为共同持仓的比较基准。只读取公开数据，不连接钱包，不需要私钥。"
              : "只读取公开持仓，不连接钱包，不需要私钥。"}
          </p>
          {error && (
            <p className="formError" role="alert">
              {error}
            </p>
          )}
          <div className="modalActions">
            <button
              className="secondaryButton"
              type="button"
              onClick={onClose}
              disabled={submitting}
            >
              取消
            </button>
            <button
              className="primaryButton"
              type="submit"
              disabled={submitting}
            >
              {submitting
                ? mode === "self"
                  ? "正在设置…"
                  : "正在添加…"
                : mode === "self"
                  ? "保存并同步"
                  : "开始监控"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function CopySettingsModal({
  open,
  settings,
  initialError,
  onClose,
  onSaved,
}: {
  open: boolean;
  settings: GlobalSettings;
  initialError: string | null;
  onClose: () => void;
  onSaved: (settings: GlobalSettings) => void;
}) {
  const [ratio, setRatio] = useState(
    formatRatioSetting(settings.copy_ratio_percent),
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(initialError);
  const ratioInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    ratioInput.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !submitting) onClose();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [onClose, open, submitting]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = Number(ratio);
    if (
      !Number.isFinite(parsed) ||
      parsed < 1 ||
      parsed > 100 ||
      !/^\d+(?:\.\d{1,2})?$/.test(ratio.trim())
    ) {
      setError("请输入 1–100 之间、最多两位小数的比例");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const updated = await request<GlobalSettings>("/api/settings", {
        method: "PUT",
        body: JSON.stringify({ copy_ratio_percent: parsed }),
      });
      onSaved(updated);
    } catch (submitError) {
      setError(
        submitError instanceof Error
          ? submitError.message
          : "保存跟单比例失败",
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) return null;

  return (
    <div
      className="modalBackdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !submitting) onClose();
      }}
    >
      <section
        className="modal copySettingsModal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="copy-settings-title"
      >
        <div className="modalHeader">
          <div>
            <span className="eyebrow">全局计算口径</span>
            <h2 id="copy-settings-title">设置跟单比例</h2>
          </div>
          <button
            className="closeButton"
            type="button"
            aria-label="关闭"
            onClick={onClose}
            disabled={submitting}
          >
            ×
          </button>
        </div>
        <form onSubmit={submit}>
          <label className="field ratioField" htmlFor="copy-ratio-percent">
            <span>全局跟单比例</span>
            <div>
              <input
                id="copy-ratio-percent"
                aria-label="全局跟单比例"
                ref={ratioInput}
                type="number"
                min="1"
                max="100"
                step="0.01"
                inputMode="decimal"
                value={ratio}
                onChange={(event) => setRatio(event.target.value)}
                disabled={submitting}
              />
              <b>%</b>
            </div>
          </label>
          <p className="privacyNote">
            对方每次净买入或卖出 100 shares 时，按当前比例计算你的建议份额。这里只提供只读建议，不会自动下单。
          </p>
          {error && (
            <p className="formError" role="alert">
              {error}
            </p>
          )}
          <div className="modalActions">
            <button
              className="secondaryButton"
              type="button"
              onClick={onClose}
              disabled={submitting}
            >
              取消
            </button>
            <button
              className="primaryButton"
              type="submit"
              disabled={submitting}
            >
              {submitting ? "正在保存…" : "保存比例"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function DeleteWalletModal({
  wallet,
  onClose,
  onConfirm,
}: {
  wallet: Wallet | null;
  onClose: () => void;
  onConfirm: (wallet: Wallet) => Promise<void>;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirmButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!wallet) return;
    confirmButton.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !submitting) onClose();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [onClose, submitting, wallet]);

  async function confirm() {
    if (!wallet || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await onConfirm(wallet);
    } catch (deleteError) {
      setError(
        deleteError instanceof Error ? deleteError.message : "删除观测钱包失败",
      );
      setSubmitting(false);
    }
  }

  if (!wallet) return null;

  const walletName = wallet.label || shortenAddress(wallet.address);
  return (
    <div
      className="modalBackdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !submitting) onClose();
      }}
    >
      <section
        className="modal deleteWalletModal"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="delete-wallet-title"
        aria-describedby="delete-wallet-description"
      >
        <div className="modalHeader">
          <div>
            <span className="eyebrow dangerEyebrow">停止监控</span>
            <h2 id="delete-wallet-title">删除观测钱包？</h2>
          </div>
          <button
            className="closeButton"
            type="button"
            aria-label="关闭"
            onClick={onClose}
            disabled={submitting}
          >
            ×
          </button>
        </div>
        <p id="delete-wallet-description" className="deleteWalletDescription">
          “{walletName}”将从观测列表移除并停止自动同步。
        </p>
        <p className="deleteWalletNote">
          已保存的持仓和变动历史不会被清除；以后重新添加该地址即可继续观测。
        </p>
        {error && (
          <p className="formError" role="alert">
            {error}
          </p>
        )}
        <div className="modalActions">
          <button
            className="secondaryButton"
            type="button"
            onClick={onClose}
            disabled={submitting}
          >
            取消
          </button>
          <button
            ref={confirmButton}
            className="dangerButton"
            type="button"
            onClick={confirm}
            disabled={submitting}
          >
            {submitting ? "正在删除…" : "确认删除"}
          </button>
        </div>
      </section>
    </div>
  );
}

function PositionComparisonPanel({
  title,
  wallet,
  position,
  tone,
}: {
  title: string;
  wallet: Wallet;
  position: Position;
  tone: "mine" | "tracked";
}) {
  return (
    <section className={`comparisonPanel ${tone}`}>
      <div className="comparisonWalletHeader">
        <div>
          <span>{title}</span>
          <strong>{wallet.label || shortenAddress(wallet.address)}</strong>
        </div>
        <small>{shortenAddress(wallet.proxy_wallet || wallet.address)}</small>
      </div>
      <dl className="comparisonFacts">
        <div>
          <dt>持仓份额</dt>
          <dd>{formatShares(position.size)}</dd>
        </div>
        <div>
          <dt>平均买入</dt>
          <dd>{formatPrice(position.avg_price)}</dd>
        </div>
        <div>
          <dt>当前价格</dt>
          <dd>{formatPrice(position.current_price)}</dd>
        </div>
        <div>
          <dt>持仓成本</dt>
          <dd>{formatMoney(position.initial_value)}</dd>
        </div>
        <div>
          <dt>当前市值</dt>
          <dd>{formatMoney(position.current_value)}</dd>
        </div>
        <div>
          <dt>浮动盈亏</dt>
          <dd>
            <PnlValue
              compact
              value={position.cash_pnl}
              percent={position.percent_pnl}
            />
          </dd>
        </div>
      </dl>
      <div className="comparisonLots">
        <h3>剩余买入批次</h3>
        {(position.purchase_lots ?? []).length > 0 ? (
          <div className="comparisonLotList">
            {(position.purchase_lots ?? []).map((lot, index) => (
              <div
                className="comparisonLot"
                key={`${lot.purchase_date}-${index}`}
              >
                <span>{formatPurchaseDate(lot.purchase_date)}</span>
                <strong>{formatShares(lot.size)} shares</strong>
                <small>
                  均价 {formatPrice(lot.avg_price)} · 成本{" "}
                  {formatMoney(lot.initial_value)}
                </small>
              </div>
            ))}
          </div>
        ) : (
          <p>历史成交尚未完整回填，暂无可展示批次。</p>
        )}
      </div>
    </section>
  );
}

function ComparisonModal({
  assetId,
  detail,
  loading,
  error,
  onClose,
  onRetry,
}: {
  assetId: string | null;
  detail: PositionOverlapDetail | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onRetry: () => void;
}) {
  useEffect(() => {
    if (!assetId) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [assetId, onClose]);

  if (!assetId) return null;

  return (
    <div
      className="modalBackdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="modal comparisonModal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="comparison-title"
      >
        <div className="modalHeader comparisonModalHeader">
          <div>
            <span className="eyebrow">共同持仓对比</span>
            <h2 id="comparison-title">
              {detail?.tracked.title ?? "正在读取持仓…"}
            </h2>
            {detail && (
              <div className="comparisonMarketMeta">
                <span
                  className={`outcomeBadge ${outcomeTone(
                    detail.tracked.outcome,
                  )}`}
                >
                  {detail.tracked.outcome}
                </span>
                <span>
                  我的数据 {formatDateTime(detail.my_wallet.last_success_at)}
                </span>
                <span>
                  对方数据{" "}
                  {formatDateTime(detail.tracked_wallet.last_success_at)}
                </span>
              </div>
            )}
          </div>
          <button
            className="closeButton"
            type="button"
            aria-label="关闭共同持仓对比"
            onClick={onClose}
          >
            ×
          </button>
        </div>

        {loading ? (
          <LoadingState label="正在读取双方持仓…" />
        ) : error ? (
          <div className="comparisonError" role="alert">
            <p>{error}</p>
            <button className="secondaryButton" type="button" onClick={onRetry}>
              重新加载
            </button>
          </div>
        ) : detail ? (
          <>
            <section className="ratioHero" aria-label="持仓份额比例">
              <div>
                <span>我的仓位是他的</span>
                <strong>
                  {formatOverlapPercent(detail.my_to_tracked_percent)}
                </strong>
              </div>
              <div>
                <span>我 : 他</span>
                <strong>
                  {formatRatioPart(detail.my_ratio)} :{" "}
                  {formatRatioPart(detail.tracked_ratio)}
                </strong>
              </div>
              <p>
                {formatShares(detail.mine.size)} ÷{" "}
                {formatShares(detail.tracked.size)} shares
              </p>
            </section>
            {(detail.my_stale || detail.tracked_stale) && (
              <div className="comparisonStale" role="status">
                当前至少一方数据不是最新同步结果，比例可能暂时存在偏差。
              </div>
            )}
            <div className="comparisonGrid">
              <PositionComparisonPanel
                title="我的钱包"
                wallet={detail.my_wallet}
                position={detail.mine}
                tone="mine"
              />
              <PositionComparisonPanel
                title="跟踪钱包"
                wallet={detail.tracked_wallet}
                position={detail.tracked}
                tone="tracked"
              />
            </div>
          </>
        ) : null}
      </section>
    </div>
  );
}

function OverlapActivity({
  alerts,
  unreadCount,
  busyAlertIds,
  markingAll,
  onRead,
  onReadAll,
}: {
  alerts: PositionOverlapAlert[];
  unreadCount: number;
  busyAlertIds: Set<string>;
  markingAll: boolean;
  onRead: (alertId: number | string) => void;
  onReadAll: () => void;
}) {
  const [showRead, setShowRead] = useState(false);
  if (alerts.length === 0) return null;

  const readCount = alerts.filter((alert) => alert.read_at !== null).length;
  const visibleAlerts = alerts.filter(
    (alert) => alert.read_at === null || showRead,
  );

  return (
    <section className="overlapActivity" aria-label="共同持仓动态">
      <div className="overlapActivityHeader">
        <div>
          <span className="activityIcon" aria-hidden="true">
            !
          </span>
          <div>
            <h2>共同持仓动态</h2>
            <p>
              对方加仓、减仓和清仓提醒
              {unreadCount > 0 ? ` · ${unreadCount} 条未读` : " · 已全部读过"}
            </p>
          </div>
        </div>
        <div className="overlapActivityHeaderActions">
          {readCount > 0 && (
            <button
              type="button"
              aria-expanded={showRead}
              onClick={() => setShowRead((current) => !current)}
            >
              {showRead ? "收起已读" : `展开已读 (${readCount})`}
            </button>
          )}
          {unreadCount > 0 && (
            <button
              type="button"
              onClick={onReadAll}
              disabled={markingAll}
            >
              {markingAll ? "处理中…" : "全部已读"}
            </button>
          )}
        </div>
      </div>
      {visibleAlerts.length === 0 ? (
        <button
          className="overlapActivityCollapsed"
          type="button"
          onClick={() => setShowRead(true)}
        >
          {readCount} 条已读提醒已收起 · 点击展开
        </button>
      ) : (
        <div className="overlapActivityList">
        {visibleAlerts.map((alert) => {
          const unread = alert.read_at === null;
          const busy = busyAlertIds.has(String(alert.id));
          const alertLabel =
            alert.type === "closed"
              ? "清仓"
              : alert.type === "increased"
                ? "加仓"
                : "减仓";
          const alertMessage =
            alert.type === "closed" ? "对方已清仓" : `对方${alertLabel}`;
          return (
            <article
              className={`overlapActivityItem ${unread ? "unread" : "read"}`}
              key={alert.id}
            >
              <span
                className={`activityType ${alert.type}`}
              >
                {alertLabel}
              </span>
              <div className="activityMain">
                <div className="activityTitleRow">
                  <a
                    href={marketUrl(alert.event_slug, alert.market_slug)}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {alert.title}
                    <span aria-hidden="true">↗</span>
                  </a>
                  <span
                    className={`outcomeBadge ${outcomeTone(alert.outcome)}`}
                  >
                    {alert.outcome}
                  </span>
                </div>
                <p>
                  {alertMessage}{" "}
                  <strong>{formatShares(alert.before_size)}</strong>
                  <span aria-hidden="true"> → </span>
                  <strong>{formatShares(alert.after_size)}</strong>
                  <small>
                    （{formatShares(alert.delta_size)} shares）
                  </small>
                </p>
                {alert.copy_recommendation && (
                  <CopyRecommendationView
                    recommendation={alert.copy_recommendation}
                    compact
                  />
                )}
              </div>
              <div className="activityMeta">
                <time dateTime={alert.detected_at}>
                  {formatDateTime(alert.detected_at)}
                </time>
                {unread ? (
                  <button
                    type="button"
                    onClick={() => onRead(alert.id)}
                    disabled={busy}
                  >
                    {busy ? "处理中…" : "标为已读"}
                  </button>
                ) : (
                  <span>已读</span>
                )}
              </div>
            </article>
          );
        })}
        </div>
      )}
    </section>
  );
}

const copyStateLabels: Record<CopySubscription["state"], string> = {
  active: "运行中",
  paused: "已暂停",
  exit_only: "仅退出",
  closing: "清仓中",
  disabled: "未启动",
  error: "已熔断",
};

function copyTabStatus(subscription: CopySubscription | undefined) {
  if (!subscription) return { label: "未配置", tone: "unconfigured" };
  if (subscription.state === "active") return { label: "实盘开", tone: "enabled" };
  if (subscription.state === "exit_only") return { label: "已关闭 · 仅退出", tone: "exit" };
  if (subscription.state === "closing") return { label: "清仓中", tone: "closing" };
  if (subscription.state === "error") return { label: "已熔断", tone: "error" };
  return { label: "已关闭", tone: "disabled" };
}

const copyPositionStatusLabels: Record<string, string> = {
  opening: "建仓中",
  open: "持仓中",
  closed: "已清仓",
  redeemed: "已赎回",
  redeeming: "赎回中",
  manual_exit: "需手工处理余仓",
  not_opened: "未成交",
};

function CopyPnlValue({
  value,
  percent,
}: {
  value: Numeric | null;
  percent?: Numeric | null;
}) {
  if (value === null || value === undefined) return <span className="copyValueMissing">—</span>;
  const tone = toNumber(value) >= 0 ? "profit" : "loss";
  return (
    <span className={`copyPnlValue ${tone}`}>
      {formatMoney(value)}
      {percent !== null && percent !== undefined && <small>{formatPercent(percent)}</small>}
    </span>
  );
}

function CopyTradingPositions({ dashboard }: { dashboard: CopyDashboard }) {
  const [view, setView] = useState<"open" | "history">("open");
  const openPositions = dashboard.positions.filter(
    (position) => toNumber(position.attributed_size) > 0,
  );
  const historicalPositions = dashboard.positions.filter(
    (position) =>
      toNumber(position.attributed_size) <= 0 &&
      toNumber(position.lifetime_bought_size) > 0,
  );
  const displayed = view === "open" ? openPositions : historicalPositions;
  const portfolio = dashboard.portfolio ?? {
    open_cost_usdc: dashboard.subscription?.open_exposure_usdc ?? 0,
    market_value_usdc: null,
    unrealized_pnl: null,
    realized_pnl: dashboard.subscription?.daily_realized_pnl ?? 0,
    total_pnl: null,
    valuation_complete: false,
    unpriced_positions: openPositions.length,
    valued_at: null,
  };

  return (
    <section className="copyPositionSection" aria-label="实盘跟单持仓明细">
      <div className="copyPositionHeader">
        <div>
          <strong>
            实盘归因持仓
          </strong>
          <span>按当前买一价估算可卖出价值 · 实盘成本计入已回报的实际费用</span>
        </div>
        <div className="copyPositionTabs" role="tablist" aria-label="跟单持仓范围">
          <button
            className={view === "open" ? "active" : ""}
            type="button"
            role="tab"
            aria-selected={view === "open"}
            onClick={() => setView("open")}
          >
            当前持仓 <span>{openPositions.length}</span>
          </button>
          <button
            className={view === "history" ? "active" : ""}
            type="button"
            role="tab"
            aria-selected={view === "history"}
            onClick={() => setView("history")}
          >
            历史记录 <span>{historicalPositions.length}</span>
          </button>
        </div>
      </div>
      <div className="copyPortfolioSummary">
        <div>
          <span>当前成本</span>
          <strong>{formatMoney(portfolio.open_cost_usdc)}</strong>
        </div>
        <div>
          <span>可卖出市值</span>
          <strong>{formatOptionalMoney(portfolio.market_value_usdc)}</strong>
        </div>
        <div>
          <span>浮动盈亏</span>
          <CopyPnlValue value={portfolio.unrealized_pnl} />
        </div>
        <div>
          <span>已实现盈亏</span>
          <CopyPnlValue value={portfolio.realized_pnl} />
        </div>
        <div>
          <span>总盈亏</span>
          <CopyPnlValue value={portfolio.total_pnl} />
        </div>
      </div>
      {!portfolio.valuation_complete && openPositions.length > 0 && (
        <p className="copyValuationWarning">
          {portfolio.unpriced_positions} 个当前持仓暂时没有买一价，组合市值与总盈亏暂不展示。
        </p>
      )}
      {displayed.length === 0 ? (
        <p className="copyPositionEmpty">
          {view === "open" ? "当前还没有实盘归因持仓。" : "暂时没有已清仓或已赎回记录。"}
        </p>
      ) : (
        <>
          <div className="copyPositionTableWrap">
            <table className="copyPositionTable">
              <thead>
                {view === "open" ? (
                  <tr>
                    <th>市场 / 方向</th>
                    <th className="numberCell">份额</th>
                    <th className="numberCell">成本 / 均价</th>
                    <th className="numberCell">当前买一</th>
                    <th className="numberCell">当前市值</th>
                    <th className="numberCell">浮动盈亏</th>
                    <th className="numberCell">已实现 / 总盈亏</th>
                  </tr>
                ) : (
                  <tr>
                    <th>市场 / 方向</th>
                    <th>状态</th>
                    <th className="numberCell">累计买入份额</th>
                    <th className="numberCell">累计投入 / 均价</th>
                    <th className="numberCell">累计卖出</th>
                    <th className="numberCell">最终已实现盈亏</th>
                  </tr>
                )}
              </thead>
              <tbody>
                {displayed.map((position) =>
                  view === "open" ? (
                    <tr key={position.id}>
                      <td>
                        <MarketTitle
                          title={position.title}
                          outcome={position.outcome}
                          eventSlug={position.event_slug}
                        />
                        <small className="copyPositionUpdated">
                          {position.valuation_status === "ok"
                            ? `估值 ${formatDateTime(position.valued_at)}`
                            : "当前盘口不可用"}
                        </small>
                      </td>
                      <td className="numberCell">{formatShares(position.attributed_size)}</td>
                      <td className="numberCell">
                        <strong>{formatMoney(position.attributed_cost)}</strong>
                        <small>{formatOptionalPrice(position.average_entry_price)}</small>
                      </td>
                      <td className="numberCell">{formatOptionalPrice(position.current_bid)}</td>
                      <td className="numberCell">{formatOptionalMoney(position.current_value)}</td>
                      <td className="numberCell">
                        <CopyPnlValue
                          value={position.unrealized_pnl}
                          percent={position.unrealized_pnl_percent}
                        />
                      </td>
                      <td className="numberCell">
                        <small>{formatMoney(position.realized_pnl)}</small>
                        <CopyPnlValue value={position.total_pnl} />
                      </td>
                    </tr>
                  ) : (
                    <tr key={position.id}>
                      <td>
                        <MarketTitle
                          title={position.title}
                          outcome={position.outcome}
                          eventSlug={position.event_slug}
                        />
                        <small className="copyPositionUpdated">
                          更新于 {formatDateTime(position.updated_at)}
                        </small>
                      </td>
                      <td>{copyPositionStatusLabels[position.status] ?? position.status}</td>
                      <td className="numberCell">{formatShares(position.lifetime_bought_size)}</td>
                      <td className="numberCell">
                        <strong>{formatMoney(position.lifetime_bought_usdc)}</strong>
                        <small>{formatOptionalPrice(position.lifetime_average_buy_price)}</small>
                      </td>
                      <td className="numberCell">{formatMoney(position.lifetime_sold_usdc)}</td>
                      <td className="numberCell">
                        <CopyPnlValue value={position.realized_pnl} />
                      </td>
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </div>
          <div className="copyPositionCards">
            {displayed.map((position) => (
              <article className="copyPositionCard" key={position.id}>
                <MarketTitle
                  title={position.title}
                  outcome={position.outcome}
                  eventSlug={position.event_slug}
                />
                {view === "open" ? (
                  <dl>
                    <div><dt>持仓份额</dt><dd>{formatShares(position.attributed_size)}</dd></div>
                    <div><dt>持仓成本</dt><dd>{formatMoney(position.attributed_cost)}</dd></div>
                    <div><dt>平均买入</dt><dd>{formatOptionalPrice(position.average_entry_price)}</dd></div>
                    <div><dt>当前买一</dt><dd>{formatOptionalPrice(position.current_bid)}</dd></div>
                    <div><dt>当前市值</dt><dd>{formatOptionalMoney(position.current_value)}</dd></div>
                    <div><dt>浮动盈亏</dt><dd><CopyPnlValue value={position.unrealized_pnl} percent={position.unrealized_pnl_percent} /></dd></div>
                    <div><dt>已实现盈亏</dt><dd><CopyPnlValue value={position.realized_pnl} /></dd></div>
                    <div><dt>总盈亏</dt><dd><CopyPnlValue value={position.total_pnl} /></dd></div>
                  </dl>
                ) : (
                  <dl>
                    <div><dt>状态</dt><dd>{copyPositionStatusLabels[position.status] ?? position.status}</dd></div>
                    <div><dt>累计买入份额</dt><dd>{formatShares(position.lifetime_bought_size)}</dd></div>
                    <div><dt>累计投入</dt><dd>{formatMoney(position.lifetime_bought_usdc)}</dd></div>
                    <div><dt>历史买入均价</dt><dd>{formatOptionalPrice(position.lifetime_average_buy_price)}</dd></div>
                    <div><dt>累计卖出</dt><dd>{formatMoney(position.lifetime_sold_usdc)}</dd></div>
                    <div><dt>已实现盈亏</dt><dd><CopyPnlValue value={position.realized_pnl} /></dd></div>
                  </dl>
                )}
              </article>
            ))}
          </div>
        </>
      )}
    </section>
  );
}

function RehearsalPanel({ enabled }: { enabled: boolean }) {
  const [marketUrl, setMarketUrl] = useState("");
  const [outcome, setOutcome] = useState("");
  const [preview, setPreview] = useState<RehearsalPreview | null>(null);
  const [result, setResult] = useState<CopyOrder | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadPreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setPreview(
        await request<RehearsalPreview>("/api/copy-trading/rehearsal/preview", {
          method: "POST",
          body: JSON.stringify({ market_url: marketUrl, outcome, max_total_usdc: 1 }),
        }),
      );
    } catch (previewError) {
      setError(previewError instanceof Error ? previewError.message : "无法生成演练预览");
    } finally {
      setBusy(false);
    }
  }

  async function execute() {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      const order = await request<CopyOrder>("/api/copy-trading/rehearsal/execute", {
        method: "POST",
        body: JSON.stringify({
          confirmation_id: preview.confirmation_id,
          confirmation_text: "确认执行1美元演练",
        }),
      });
      setResult(order);
      setPreview(null);
    } catch (executeError) {
      setError(executeError instanceof Error ? executeError.message : "演练执行失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="copyTradingFieldGroup" aria-label="1 美元手工市场演练">
      <h3>$1 真实下单演练</h3>
      <p>会真实花费资金并买入一次，不自动卖回，也不会计入自动跟单持仓。</p>
      <form className="copyTradingFormGrid" onSubmit={loadPreview}>
        <label className="field copyTradingField">
          <span>市场链接</span>
          <input
            type="url"
            value={marketUrl}
            onChange={(event) => setMarketUrl(event.target.value)}
            placeholder="https://polymarket.com/event/..."
            required
            disabled={!enabled || busy}
          />
        </label>
        <label className="field copyTradingField">
          <span>Outcome</span>
          <input
            value={outcome}
            onChange={(event) => setOutcome(event.target.value)}
            placeholder="例如 Yes 或球队名称"
            required
            disabled={!enabled || busy}
          />
        </label>
        <button type="submit" disabled={!enabled || busy}>
          {busy ? "检查中…" : "预览 $1 买入"}
        </button>
      </form>
      {!enabled && <small>先验证 V2 执行钱包后才能演练。</small>}
      {preview && (
        <div className="privacyNote">
          <strong>{preview.title} · {preview.outcome}</strong>
          <span>
            当前卖一 {formatPrice(preview.best_ask)}，动态费率 {preview.fee_rate_bps} bps，
            含费用硬上限 {formatMoney(preview.max_total_usdc)}。
          </span>
          <button className="dangerButton" type="button" onClick={execute} disabled={busy}>
            二次确认：执行单边 FAK BUY
          </button>
        </div>
      )}
      {result && (
        <p className="privacyNote">
          演练成功：成交 {formatShares(result.filled_size)}，支出 {formatMoney(result.filled_usdc)}。
        </p>
      )}
      {error && <p className="copyTradingError" role="alert">{error}</p>}
    </section>
  );
}

function CopyTradingPanel({
  dashboard,
  error,
  busy,
  onConfigure,
  onToggle,
  onClosePositions,
  onSetupAccount,
  onConfigureAccountRisk,
  onVerifyAccount,
}: {
  dashboard: CopyDashboard | null;
  error: string | null;
  busy: boolean;
  onConfigure: () => void;
  onToggle: (enabled: boolean) => void;
  onClosePositions: () => void;
  onSetupAccount: () => void;
  onConfigureAccountRisk: () => void;
  onVerifyAccount: () => void;
}) {
  const subscription = dashboard?.subscription ?? null;
  const account = dashboard?.account ?? null;
  const liveCopyEnabled = dashboard?.live_copy_enabled ?? false;
  const openPositions =
    dashboard?.positions.filter(
      (position) => toNumber(position.attributed_size) > 0,
    ).length ?? 0;

  return (
    <section className="copyTradingPanel" aria-label="自动跟单">
      <div className="copyTradingLead">
        <span className="eyebrow">单执行钱包 · V2</span>
        <h2>实盘自动跟单</h2>
        <p>
          每 15 秒确认一次持仓变化：只跟随建仓、清仓和赎回；加仓与减仓仅记录，不自动下单。
        </p>
      </div>
      {!subscription ? (
        <div className="copyTradingEmpty">
          <strong>当前钱包尚未配置实盘跟单</strong>
          <span>默认按建仓成本的 10% 跟单，单仓最多 $20，总敞口最多 $160。</span>
          <button className="primaryButton" type="button" onClick={onConfigure}>
            配置实盘跟单
          </button>
        </div>
      ) : (
        <>
          <div className="copyTradingMetrics">
            <div>
              <span>实盘状态</span>
              <strong>{copyStateLabels[subscription.state]}</strong>
            </div>
            <div>
              <span>当前归因敞口</span>
              <strong>{formatMoney(subscription.open_exposure_usdc)}</strong>
            </div>
            <div>
              <span>今日跟单买入</span>
              <strong>{formatMoney(subscription.daily_bought_usdc)}</strong>
            </div>
            <div>
              <span>归因持仓</span>
              <strong>{openPositions} 个</strong>
            </div>
            <div>
              <span>跟单规则</span>
              <strong>建仓一次 · 归零清仓 · 赎回一次</strong>
            </div>
          </div>
          <div className="copyTradingActions">
            <button type="button" onClick={onConfigure} disabled={busy}>
              风控设置
            </button>
            <button
              className={subscription.enabled ? "liveToggleButton enabled" : "primaryButton"}
              type="button"
              onClick={() => onToggle(!subscription.enabled)}
              disabled={
                busy ||
                subscription.state === "closing" ||
                (!subscription.enabled &&
                  (!liveCopyEnabled || account?.status !== "ready"))
              }
            >
              {busy
                ? "处理中…"
                : subscription.enabled
                  ? "关闭实盘跟单"
                  : "开启实盘跟单"}
            </button>
            <button
              className="dangerButton"
              type="button"
              onClick={onClosePositions}
              disabled={busy || subscription.state === "closing"}
            >
              关闭并清仓
            </button>
          </div>
          {!liveCopyEnabled && (
            <p className="keychainHint">自动实盘已被系统紧急停用，当前不能开启新买入。</p>
          )}
          {liveCopyEnabled && account?.status !== "ready" && !subscription.enabled && (
            <p className="keychainHint">先完成执行钱包验证并确保余额高于现金保留额，才能开启实盘。</p>
          )}
          {dashboard && <CopyTradingPositions dashboard={dashboard} />}
          <p className="privacyNote">
            关闭开关后停止新买入，但保留归因仓位并继续跟随清仓和赎回。盘口保护 ±{formatRatioPart(subscription.market_slippage_cents)}¢。
          </p>
        </>
      )}
      <div className="executionAccountRow">
        <div>
          <span>执行钱包</span>
          <strong>
            {!account
              ? "尚未配置"
              : account.status === "ready"
                ? `已验证 · ${formatMoney(account.collateral_balance)}`
                : account.status === "insufficient_balance"
                  ? `已验证 · 余额 ${formatMoney(account.collateral_balance)} · 低于现金保留额`
                  : account.status === "error"
                    ? "验证失败"
                : account.credentials_configured
                  ? "密钥已配置，等待余额验证"
                  : "等待导入钥匙串密钥"}
          </strong>
        </div>
        <div className="executionAccountActions">
          {!account ? (
            <button type="button" onClick={onSetupAccount} disabled={busy}>
              绑定我的钱包
            </button>
          ) : (
            <>
              <button type="button" onClick={onConfigureAccountRisk} disabled={busy}>
                资金风控
              </button>
              {account.status !== "ready" && (
                <button type="button" onClick={onVerifyAccount} disabled={busy}>
                  {busy ? "正在验证…" : account.status === "insufficient_balance" ? "重新验证" : "验证密钥与余额"}
                </button>
              )}
            </>
          )}
        </div>
      </div>
      {account?.status === "insufficient_balance" && (
        <p className="keychainHint">
          钱包验证已经通过；当前余额 {formatMoney(account.collateral_balance)} 低于现金保留额 {formatMoney(account.cash_reserve_usdc)}。请补充余额或调整资金参数。
        </p>
      )}
      {account && !account.credentials_configured && account.signer_address && (
        <p className="keychainHint">
          在终端运行：
          <code>
            uv run python -m backend.copy_cli set-key --account {account.signer_address}
          </code>
        </p>
      )}
      {account && account.credentials_configured && account.signer_address && account.signature_type === 1 && (
        <p className="keychainHint">
          Proxy 钱包自动赎回还需 Builder 凭证：
          <code>
            uv run python -m backend.copy_cli set-builder-creds --account {account.signer_address}
          </code>
        </p>
      )}
      {account && account.credentials_configured && account.signature_type === 3 && (
        <p className="keychainHint">
          Deposit Wallet 自动赎回需要 Relayer API 凭证；普通余额验证和下单不受影响。
        </p>
      )}
      {(error || subscription?.last_error || account?.last_error) && (
        <p className="copyTradingError" role="alert">
          {error || subscription?.last_error || account?.last_error}
        </p>
      )}
      <RehearsalPanel enabled={liveCopyEnabled && account?.status === "ready"} />
    </section>
  );
}

function ExecutionRiskModal({
  open,
  account,
  onClose,
  onSaved,
}: {
  open: boolean;
  account: ExecutionAccount | null;
  onClose: () => void;
  onSaved: (account: ExecutionAccount) => void;
}) {
  const [values, setValues] = useState({
    budget_usdc: String(account?.budget_usdc ?? 400),
    cash_reserve_usdc: String(account?.cash_reserve_usdc ?? 240),
    max_total_exposure_usdc: String(account?.max_total_exposure_usdc ?? 160),
    daily_buy_limit_usdc: String(account?.daily_buy_limit_usdc ?? 80),
    daily_loss_limit_usdc: String(account?.daily_loss_limit_usdc ?? 40),
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open || !account) return null;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!account) return;
    const numbers = Object.fromEntries(
      Object.entries(values).map(([key, value]) => [key, Number(value)]),
    ) as Record<keyof typeof values, number>;
    if (Object.values(numbers).some((value) => !Number.isFinite(value) || value < 0)) {
      setError("资金参数必须是大于或等于 0 的有效金额");
      return;
    }
    if (numbers.cash_reserve_usdc > numbers.budget_usdc) {
      setError("现金保留额不能高于钱包预算");
      return;
    }
    if (
      numbers.max_total_exposure_usdc >
      numbers.budget_usdc - numbers.cash_reserve_usdc
    ) {
      setError("总敞口不能高于预算扣除现金保留额");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const updated = await request<ExecutionAccount>("/api/copy-trading/account", {
        method: "PUT",
        body: JSON.stringify({
          wallet_id: Number(account.wallet_id),
          signer_address: account.signer_address,
          funder_address: account.funder_address,
          signature_type: account.signature_type ?? 3,
          ...numbers,
          auto_redeem: account.auto_redeem ?? true,
        }),
      });
      onSaved(updated);
    } catch (submitError) {
      setError(
        submitError instanceof Error ? submitError.message : "无法保存执行钱包资金风控",
      );
    } finally {
      setSubmitting(false);
    }
  }

  const fields: Array<{
    key: keyof typeof values;
    label: string;
    description: string;
  }> = [
    {
      key: "budget_usdc",
      label: "钱包预算",
      description: "自动跟单可以纳入计算的资金总额。",
    },
    {
      key: "cash_reserve_usdc",
      label: "现金保留额",
      description: "这部分余额不会用于自动跟单；当前限制 $240 在这里调整。",
    },
    {
      key: "max_total_exposure_usdc",
      label: "账户总敞口上限",
      description: "所有观察钱包的自动跟单仓位合计上限。",
    },
    {
      key: "daily_buy_limit_usdc",
      label: "每日买入上限",
      description: "所有观察钱包每天累计允许买入的金额。",
    },
    {
      key: "daily_loss_limit_usdc",
      label: "每日亏损熔断",
      description: "所有观察钱包当天累计实现亏损达到该值后停止新买入。",
    },
  ];
  const currentBalance =
    account.collateral_balance === null
      ? null
      : toNumber(account.collateral_balance);
  const estimatedCapacity =
    currentBalance === null
      ? null
      : Math.max(
          0,
          Math.min(
            toNumber(values.max_total_exposure_usdc),
            Math.min(toNumber(values.budget_usdc), currentBalance) -
              toNumber(values.cash_reserve_usdc),
          ),
        );

  return (
    <div className="modalBackdrop" role="presentation">
      <section
        className="modal copyTradingModal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="execution-risk-title"
      >
        <div className="modalHeader">
          <div>
            <span className="eyebrow">唯一执行钱包 · 全局共享</span>
            <h2 id="execution-risk-title">执行钱包资金风控</h2>
          </div>
          <button className="closeButton" type="button" onClick={onClose}>×</button>
        </div>
        <form onSubmit={submit}>
          <div className="copyTradingFormGrid executionRiskGrid">
            {fields.map((field) => (
              <label className="field copyTradingField" key={field.key}>
                <span>{field.label}</span>
                <div className="unitInput">
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    required
                    value={values[field.key]}
                    onChange={(event) =>
                      setValues((current) => ({
                        ...current,
                        [field.key]: event.target.value,
                      }))
                    }
                    disabled={submitting}
                  />
                  <b>USDC</b>
                </div>
                <small>{field.description}</small>
              </label>
            ))}
          </div>
          {currentBalance !== null && (
            <p className="executionRiskPreview">
              当前余额 {formatMoney(currentBalance)} · 按以上参数最多可分配{" "}
              <strong>{formatMoney(estimatedCapacity)}</strong>
            </p>
          )}
          <p className="privacyNote">
            保存后需要重新验证执行钱包。可分配额度还需覆盖已开启钱包的固定额度和已关闭钱包的实际持仓；系统不会自动调整金额或充值。
          </p>
          {error && <p className="formError" role="alert">{error}</p>}
          <div className="modalActions">
            <button className="secondaryButton" type="button" onClick={onClose}>取消</button>
            <button className="primaryButton" type="submit" disabled={submitting}>
              {submitting ? "正在保存…" : "保存资金风控"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function CopyTradingModal({
  open,
  trackedWalletId,
  subscription,
  onClose,
  onSaved,
}: {
  open: boolean;
  trackedWalletId: string | null;
  subscription: CopySubscription | null;
  onClose: () => void;
  onSaved: (subscription: CopySubscription) => void;
}) {
  const defaults = {
    copy_ratio_percent: String(subscription?.copy_ratio_percent ?? 10),
    position_cap_usdc: String(subscription?.position_cap_usdc ?? 20),
    total_exposure_cap_usdc: String(subscription?.total_exposure_cap_usdc ?? 160),
    market_slippage_cents: String(subscription?.market_slippage_cents ?? 5),
  };
  const [values, setValues] = useState(defaults);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!trackedWalletId) return;
    setSubmitting(true);
    setError(null);
    try {
      const payload = Object.fromEntries(
        Object.entries(values).map(([key, value]) => [key, Number(value)]),
      );
      const updated = await request<CopySubscription>(
        subscription
          ? `/api/copy-trading/subscriptions/${subscription.id}`
          : "/api/copy-trading/subscriptions",
        {
          method: subscription ? "PUT" : "POST",
          body: JSON.stringify(
            subscription
              ? payload
              : { ...payload, tracked_wallet_id: Number(trackedWalletId) },
          ),
        },
      );
      onSaved(updated);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "无法保存跟单设置");
    } finally {
      setSubmitting(false);
    }
  }

  type FieldConfig = {
    key: keyof typeof values;
    label: string;
    unit: string;
    description: string;
    min: string;
    max?: string;
    step?: string;
  };
  const fieldGroups: Array<{ title: string; fields: FieldConfig[] }> = [
    {
      title: "低频跟单设置",
      fields: [
        {
          key: "copy_ratio_percent",
          label: "跟单比例",
          unit: "%",
          description: "观察钱包首次建仓时，按其建仓成本的一定比例执行一次买入。",
          min: "0.01",
          max: "100",
        },
        {
          key: "position_cap_usdc",
          label: "单仓最大投入",
          unit: "USDC",
          description: "每个市场周期首次建仓允许投入的最高金额。",
          min: "0.01",
        },
        {
          key: "total_exposure_cap_usdc",
          label: "钱包固定额度",
          unit: "USDC",
          description: "该观察钱包运行时预留的最高额度；多个钱包的额度共享执行账户总上限。",
          min: "0",
        },
        {
          key: "market_slippage_cents",
          label: "盘口保护",
          unit: "¢",
          description: "FAK 买入/卖出相对当前最优价格允许的最差偏移，超出范围不成交。",
          min: "0",
          max: "50",
        },
      ],
    },
  ];

  return (
    <div className="modalBackdrop" role="presentation">
      <section className="modal copyTradingModal" role="dialog" aria-modal="true">
        <div className="modalHeader">
          <div>
            <span className="eyebrow">按观察钱包独立配置</span>
            <h2>{subscription ? "修改实盘跟单风控" : "配置实盘跟单"}</h2>
          </div>
          <button className="closeButton" type="button" onClick={onClose}>×</button>
        </div>
        <form onSubmit={submit}>
          <div className="copyTradingFieldGroups">
            {fieldGroups.map((group) => (
              <section className="copyTradingFieldGroup" key={group.title}>
                <h3>{group.title}</h3>
                <div className="copyTradingFormGrid">
                  {group.fields.map((field) => (
                    <label className="field copyTradingField" key={field.key}>
                      <span>{field.label}</span>
                      <div className="unitInput">
                        <input
                          type="number"
                          min={field.min}
                          max={field.max}
                          step={field.step ?? "0.01"}
                          value={values[field.key]}
                          onChange={(event) =>
                            setValues((current) => ({
                              ...current,
                              [field.key]: event.target.value,
                            }))
                          }
                          disabled={submitting}
                        />
                        <b>{field.unit}</b>
                      </div>
                      <small>{field.description}</small>
                    </label>
                  ))}
                </div>
              </section>
            ))}
          </div>
          <p className="privacyNote">
            保存配置不会自动开启。每次开启实盘都需要再次确认；系统仅处理建仓、清仓、赎回三类稳定持仓事件。
          </p>
          {error && <p className="formError" role="alert">{error}</p>}
          <div className="modalActions">
            <button className="secondaryButton" type="button" onClick={onClose}>取消</button>
            <button className="primaryButton" type="submit" disabled={submitting}>
              {submitting ? "正在保存…" : "保存风控"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

export default function Home() {
  const [globalSettings, setGlobalSettings] = useState<GlobalSettings>({
    copy_ratio_percent: 10,
  });
  const [settingsModalOpen, setSettingsModalOpen] = useState(false);
  const [copyTradingModalOpen, setCopyTradingModalOpen] = useState(false);
  const [executionRiskModalOpen, setExecutionRiskModalOpen] = useState(false);
  const [copyDashboard, setCopyDashboard] = useState<CopyDashboard | null>(null);
  const [copySubscriptions, setCopySubscriptions] = useState<CopySubscription[]>([]);
  const [copyTradingError, setCopyTradingError] = useState<string | null>(null);
  const [copyTradingBusy, setCopyTradingBusy] = useState(false);
  const [settingsLoadError, setSettingsLoadError] = useState<string | null>(
    null,
  );
  const [wallets, setWallets] = useState<Wallet[]>([]);
  const [myWallet, setMyWallet] = useState<Wallet | null>(null);
  const [activeWalletId, setActiveWalletId] = useState<string | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [overlaps, setOverlaps] = useState<PositionOverlap[]>([]);
  const [overlapError, setOverlapError] = useState<string | null>(null);
  const [overlapAlerts, setOverlapAlerts] = useState<
    PositionOverlapAlert[]
  >([]);
  const [unreadAlertCount, setUnreadAlertCount] = useState(0);
  const [alertError, setAlertError] = useState<string | null>(null);
  const [busyAlertIds, setBusyAlertIds] = useState<Set<string>>(new Set());
  const [markingAllAlerts, setMarkingAllAlerts] = useState(false);
  const [summary, setSummary] = useState<PositionSummary>(emptySummary);
  const [openedDates, setOpenedDates] = useState<string[]>([]);
  const [openedDate, setOpenedDate] = useState("all");
  const [purchaseHistoryComplete, setPurchaseHistoryComplete] =
    useState(false);
  const [purchaseHistoryError, setPurchaseHistoryError] = useState<
    string | null
  >(null);
  const [events, setEvents] = useState<PositionEvent[]>([]);
  const [nextCursor, setNextCursor] = useState<number | string | null>(null);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [view, setView] = useState<View>("positions");
  const [loadingWallets, setLoadingWallets] = useState(true);
  const [loadingContent, setLoadingContent] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [streamConnected, setStreamConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [walletModalMode, setWalletModalMode] = useState<
    "self" | "tracked" | null
  >(null);
  const [walletPendingDeletion, setWalletPendingDeletion] =
    useState<Wallet | null>(null);
  const [comparisonAssetId, setComparisonAssetId] = useState<string | null>(
    null,
  );
  const [comparisonDetail, setComparisonDetail] =
    useState<PositionOverlapDetail | null>(null);
  const [comparisonLoading, setComparisonLoading] = useState(false);
  const [comparisonError, setComparisonError] = useState<string | null>(null);
  const requestSequence = useRef(0);
  const comparisonSequence = useRef(0);
  const activeWalletRef = useRef<string | null>(null);
  const myWalletRef = useRef<Wallet | null>(null);

  useEffect(() => {
    activeWalletRef.current = activeWalletId;
  }, [activeWalletId]);

  useEffect(() => {
    myWalletRef.current = myWallet;
  }, [myWallet]);

  const loadGlobalSettings = useCallback(async () => {
    try {
      const loaded = await request<GlobalSettings>("/api/settings");
      setGlobalSettings(loaded);
      setSettingsLoadError(null);
    } catch (settingsError) {
      setSettingsLoadError(
        settingsError instanceof Error
          ? settingsError.message
          : "无法读取跟单比例",
      );
    }
  }, []);

  const loadWallets = useCallback(async () => {
    try {
      const walletList = await request<Wallet[]>("/api/wallets");
      const enabledWallets = walletList.filter(
        (wallet) =>
          wallet.enabled !== false && wallet.wallet_role !== "self",
      );
      const nextMyWallet =
        walletList.find(
          (wallet) => wallet.enabled !== false && wallet.wallet_role === "self",
        ) ?? null;
      setMyWallet(nextMyWallet);
      myWalletRef.current = nextMyWallet;
      setWallets(enabledWallets);
      setActiveWalletId((current) => {
        if (
          current &&
          enabledWallets.some((wallet) => String(wallet.id) === current)
        ) {
          return current;
        }
        return enabledWallets[0] ? String(enabledWallets[0].id) : null;
      });
      setError(null);
      return enabledWallets;
    } catch (walletError) {
      setError(
        walletError instanceof Error
          ? walletError.message
          : "无法读取监控钱包",
      );
      return [];
    } finally {
      setLoadingWallets(false);
    }
  }, []);

  const loadCopyDashboard = useCallback(async (walletId: string) => {
    try {
      const dashboard = await request<CopyDashboard>(
        `/api/copy-trading/dashboard?tracked_wallet_id=${encodeURIComponent(walletId)}`,
      );
      if (activeWalletRef.current !== walletId) return;
      setCopyDashboard(dashboard);
      if (dashboard.subscription) {
        setCopySubscriptions((current) => [
          ...current.filter(
            (item) => String(item.id) !== String(dashboard.subscription?.id),
          ),
          dashboard.subscription as CopySubscription,
        ]);
      }
      setCopyTradingError(null);
    } catch (dashboardError) {
      if (activeWalletRef.current !== walletId) return;
      setCopyTradingError(
        dashboardError instanceof Error
          ? dashboardError.message
          : "无法读取自动跟单状态",
      );
    }
  }, []);

  const loadCopySubscriptions = useCallback(async () => {
    try {
      setCopySubscriptions(
        await request<CopySubscription[]>("/api/copy-trading/subscriptions"),
      );
    } catch {
      // The selected wallet dashboard still shows the detailed error.
    }
  }, []);

  const loadContent = useCallback(
    async (walletId: string, quiet = false) => {
      const sequence = ++requestSequence.current;
      if (!quiet) setLoadingContent(true);
      try {
        const emptyOverlapResponse: PositionOverlapsResponse = {
          my_wallet_id: myWalletRef.current?.id ?? null,
          tracked_wallet_id: walletId,
          items: [],
          overlap_count: 0,
          my_as_of: myWalletRef.current?.last_success_at ?? null,
          tracked_as_of: null,
          my_stale: null,
          tracked_stale: false,
        };
        const comparisonRequest = myWalletRef.current
          ? request<PositionOverlapsResponse>(
              `/api/position-overlaps?tracked_wallet_id=${encodeURIComponent(
                walletId,
              )}`,
            )
              .then((data) => ({ data, error: null as string | null }))
              .catch((overlapRequestError) => ({
                data: emptyOverlapResponse,
                error:
                  overlapRequestError instanceof Error
                    ? overlapRequestError.message
                    : "共同持仓比较暂时不可用",
              }))
          : Promise.resolve({
              data: emptyOverlapResponse,
              error: null as string | null,
            });
        const emptyAlertResponse: PositionOverlapAlertsResponse = {
          items: [],
          unread_count: 0,
        };
        const alertRequest = myWalletRef.current
          ? request<PositionOverlapAlertsResponse>(
              `/api/overlap-alerts?tracked_wallet_id=${encodeURIComponent(
                walletId,
              )}`,
            )
              .then((data) => ({ data, error: null as string | null }))
              .catch((alertRequestError) => ({
                data: emptyAlertResponse,
                error:
                  alertRequestError instanceof Error
                    ? alertRequestError.message
                    : "共同持仓提醒暂时不可用",
              }))
          : Promise.resolve({
              data: emptyAlertResponse,
              error: null as string | null,
            });
        const [positionData, eventData, overlapResult, alertResult] =
          await Promise.all([
          request<PositionResponse>(
            `/api/positions?wallet_id=${encodeURIComponent(walletId)}`,
          ),
          request<EventsResponse>(
            `/api/position-events?wallet_id=${encodeURIComponent(walletId)}`,
          ),
          comparisonRequest,
          alertRequest,
        ]);

        if (sequence !== requestSequence.current) return;
        const sortedPositions = [...positionData.items].sort(
          (left, right) =>
            toNumber(right.current_value) - toNumber(left.current_value),
        );
        setPositions(sortedPositions);
        setOverlaps(overlapResult.data.items);
        setOverlapError(overlapResult.error);
        setOverlapAlerts(alertResult.data.items);
        setUnreadAlertCount(alertResult.data.unread_count);
        setAlertError(alertResult.error);
        setSummary(positionData.summary ?? emptySummary);
        const nextOpenedDates = positionData.opened_dates ?? [];
        setOpenedDates(nextOpenedDates);
        setOpenedDate((current) =>
          current === "all" || nextOpenedDates.includes(current)
            ? current
            : "all",
        );
        setPurchaseHistoryComplete(
          Boolean(positionData.purchase_history_complete),
        );
        setPurchaseHistoryError(positionData.purchase_history_error ?? null);
        setAsOf(positionData.as_of);
        setStale(Boolean(positionData.stale));
        setEvents(eventData.items);
        setNextCursor(eventData.next_cursor);
        setError(null);
      } catch (contentError) {
        if (sequence !== requestSequence.current) return;
        setStale(true);
        setError(
          contentError instanceof Error
            ? contentError.message
            : "同步暂时中断",
        );
      } finally {
        if (sequence === requestSequence.current) setLoadingContent(false);
      }
    },
    [],
  );

  useEffect(() => {
    const timer = window.setTimeout(() => void loadWallets(), 0);
    return () => window.clearTimeout(timer);
  }, [loadWallets]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadGlobalSettings(), 0);
    return () => window.clearTimeout(timer);
  }, [loadGlobalSettings]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadCopySubscriptions(), 0);
    return () => window.clearTimeout(timer);
  }, [loadCopySubscriptions]);

  useEffect(() => {
    if (!activeWalletId) return;
    const timer = window.setTimeout(
      () => {
        void loadContent(activeWalletId);
        void loadCopyDashboard(activeWalletId);
      },
      0,
    );
    return () => window.clearTimeout(timer);
  }, [activeWalletId, loadContent, loadCopyDashboard]);

  useEffect(() => {
    let refreshTimer: number | undefined;
    const stream = new EventSource(`${API_BASE}/api/stream`);

    stream.onopen = () => setStreamConnected(true);
    stream.onerror = () => setStreamConnected(false);
    stream.onmessage = (message) => {
      try {
        const payload = JSON.parse(message.data) as {
          type?: string;
          wallet_id?: string | number;
        };
        const eventWalletId =
          payload.wallet_id === undefined ? null : String(payload.wallet_id);
        if (payload.type === "sync.status") void loadWallets();
        if (
          eventWalletId &&
          (eventWalletId === activeWalletRef.current ||
            eventWalletId ===
              (myWalletRef.current
                ? String(myWalletRef.current.id)
                : null)) &&
          (payload.type === "positions.updated" ||
            payload.type === "events.created" ||
            payload.type === "overlap-alerts.created" ||
            payload.type === "sync.status")
        ) {
          window.clearTimeout(refreshTimer);
          refreshTimer = window.setTimeout(() => {
            const walletId = activeWalletRef.current;
            if (walletId) void loadContent(walletId, true);
          }, 300);
        }
      } catch {
        // Ignore keep-alive or future event formats.
      }
    };

    return () => {
      window.clearTimeout(refreshTimer);
      stream.close();
    };
  }, [loadContent, loadWallets]);

  useEffect(() => {
    const fallbackRefresh = window.setInterval(() => {
        void loadWallets();
        void loadCopySubscriptions();
      const walletId = activeWalletRef.current;
      if (walletId) {
        void loadContent(walletId, true);
        void loadCopyDashboard(walletId);
      }
    }, 30_000);
    return () => window.clearInterval(fallbackRefresh);
  }, [loadContent, loadCopyDashboard, loadCopySubscriptions, loadWallets]);

  const activeWallet = wallets.find(
    (wallet) => String(wallet.id) === activeWalletId,
  );
  const copySubscriptionMap = useMemo(
    () =>
      new Map(
        copySubscriptions.map((subscription) => [
          String(subscription.tracked_wallet_id),
          subscription,
        ]),
      ),
    [copySubscriptions],
  );
  const status = syncStatus(activeWallet?.status ?? "pending", streamConnected);
  const overlapMap = useMemo(
    () => new Map(overlaps.map((overlap) => [overlap.asset_id, overlap])),
    [overlaps],
  );
  const unreadChangeAlerts = useMemo(() => {
    const byAsset = new Map<string, PositionOverlapAlert>();
    for (const alert of overlapAlerts) {
      if (
        alert.read_at === null &&
        (alert.type === "increased" || alert.type === "decreased") &&
        !byAsset.has(alert.asset_id)
      ) {
        byAsset.set(alert.asset_id, alert);
      }
    }
    return byAsset;
  }, [overlapAlerts]);

  const displayedPositions = useMemo(() => {
    const selected =
      openedDate === "all"
        ? positions
        : positions.filter((position) => position.opened_date === openedDate);
    return [...selected].sort(
        (left, right) =>
          toNumber(right.current_value) - toNumber(left.current_value),
      );
  }, [positions, openedDate]);

  const displayedSummary = useMemo<PositionSummary>(() => {
    if (openedDate === "all") return summary;
    return {
      current_value: displayedPositions.reduce(
        (total, position) => total + toNumber(position.current_value),
        0,
      ),
      initial_value: displayedPositions.reduce(
        (total, position) => total + toNumber(position.initial_value),
        0,
      ),
      cash_pnl: displayedPositions.reduce(
        (total, position) => total + toNumber(position.cash_pnl),
        0,
      ),
      count: displayedPositions.length,
    };
  }, [displayedPositions, openedDate, summary]);

  const purchaseScopeLabel =
    openedDate === "all" ? "全部建仓" : formatPurchaseDate(openedDate);

  async function refresh() {
    if (!activeWalletId || refreshing) return;
    setRefreshing(true);
    try {
      const walletIds = new Set([activeWalletId]);
      if (myWalletRef.current) walletIds.add(String(myWalletRef.current.id));
      await Promise.all(
        [...walletIds].map((walletId) =>
          request<Record<string, unknown>>(
            `/api/wallets/${encodeURIComponent(walletId)}/sync`,
            { method: "POST" },
          ),
        ),
      );
      await Promise.all([
        loadContent(activeWalletId, true),
        loadCopyDashboard(activeWalletId),
        loadWallets(),
      ]);
    } catch (refreshError) {
      setStale(true);
      setError(
        refreshError instanceof Error ? refreshError.message : "手动同步失败",
      );
    } finally {
      setRefreshing(false);
    }
  }

  async function loadMoreEvents() {
    if (!activeWalletId || !nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await request<EventsResponse>(
        `/api/position-events?wallet_id=${encodeURIComponent(
          activeWalletId,
        )}&cursor=${encodeURIComponent(nextCursor)}`,
      );
      setEvents((current) => {
        const knownIds = new Set(current.map((event) => event.id));
        return [
          ...current,
          ...page.items.filter((event) => !knownIds.has(event.id)),
        ];
      });
      setNextCursor(page.next_cursor);
    } catch (loadMoreError) {
      setError(
        loadMoreError instanceof Error
          ? loadMoreError.message
          : "无法读取更多记录",
      );
    } finally {
      setLoadingMore(false);
    }
  }

  function selectWallet(walletId: string | null) {
    if (walletId === activeWalletRef.current) return;
    requestSequence.current += 1;
    setPositions([]);
    setOverlaps([]);
    setOverlapError(null);
    setOverlapAlerts([]);
    setUnreadAlertCount(0);
    setAlertError(null);
    setCopyDashboard(null);
    setCopyTradingError(null);
    setBusyAlertIds(new Set());
    setMarkingAllAlerts(false);
    setEvents([]);
    setSummary(emptySummary);
    setOpenedDates([]);
    setOpenedDate("all");
    setPurchaseHistoryComplete(false);
    setPurchaseHistoryError(null);
    setAsOf(null);
    setStale(false);
    setError(null);
    setLoadingContent(Boolean(walletId));
    closeComparison();
    activeWalletRef.current = walletId;
    setActiveWalletId(walletId);
  }

  function walletCreated(wallet: Wallet) {
    setWalletModalMode(null);
    if (wallet.wallet_role === "self") {
      setMyWallet(wallet);
      myWalletRef.current = wallet;
      const remainingWallets = wallets.filter(
        (item) => String(item.id) !== String(wallet.id),
      );
      setWallets(remainingWallets);
      if (activeWalletRef.current === String(wallet.id)) {
        requestSequence.current += 1;
        const nextActiveWalletId = remainingWallets[0]
          ? String(remainingWallets[0].id)
          : null;
        activeWalletRef.current = nextActiveWalletId;
        setPositions([]);
        setOverlaps([]);
        setOverlapAlerts([]);
        setUnreadAlertCount(0);
        setAlertError(null);
        setEvents([]);
        setSummary(emptySummary);
        setOpenedDates([]);
        setOpenedDate("all");
        setPurchaseHistoryComplete(false);
        setPurchaseHistoryError(null);
        setAsOf(null);
        setStale(false);
        setError(null);
        setActiveWalletId(nextActiveWalletId);
      } else if (activeWalletRef.current) {
        void loadContent(activeWalletRef.current, true);
      }
      void loadWallets();
      return;
    }
    setWallets((current) => {
      const next = current.filter(
        (item) => String(item.id) !== String(wallet.id),
      );
      return [...next, wallet];
    });
    selectWallet(String(wallet.id));
    void loadWallets();
  }

  function copySettingsSaved(settings: GlobalSettings) {
    setGlobalSettings(settings);
    setSettingsLoadError(null);
    setSettingsModalOpen(false);
    const walletId = activeWalletRef.current;
    if (walletId) void loadContent(walletId, true);
  }

  async function deleteObservedWallet(wallet: Wallet) {
    await request<void>(
      `/api/wallets/${encodeURIComponent(String(wallet.id))}`,
      { method: "DELETE" },
    );

    const remainingWallets = wallets.filter(
      (item) => String(item.id) !== String(wallet.id),
    );
    setWallets(remainingWallets);
    if (activeWalletRef.current === String(wallet.id)) {
      selectWallet(
        remainingWallets[0] ? String(remainingWallets[0].id) : null,
      );
    }
    setWalletPendingDeletion(null);
    setError(null);
    void loadWallets();
  }

  async function markOverlapAlertRead(alertId: number | string) {
    const alertKey = String(alertId);
    if (busyAlertIds.has(alertKey)) return;
    setBusyAlertIds((current) => new Set(current).add(alertKey));
    setAlertError(null);
    try {
      const updated = await request<PositionOverlapAlert>(
        `/api/overlap-alerts/${encodeURIComponent(alertKey)}/read`,
        { method: "POST" },
      );
      setOverlapAlerts((current) =>
        current.map((alert) =>
          String(alert.id) === alertKey ? updated : alert,
        ),
      );
      setUnreadAlertCount((current) => Math.max(0, current - 1));
    } catch (markError) {
      setAlertError(
        markError instanceof Error ? markError.message : "无法更新提醒状态",
      );
    } finally {
      setBusyAlertIds((current) => {
        const next = new Set(current);
        next.delete(alertKey);
        return next;
      });
    }
  }

  async function markAllOverlapAlertsRead() {
    if (!activeWalletId || markingAllAlerts || unreadAlertCount === 0) return;
    setMarkingAllAlerts(true);
    setAlertError(null);
    try {
      await request<void>(
        `/api/overlap-alerts/read-all?tracked_wallet_id=${encodeURIComponent(
          activeWalletId,
        )}`,
        { method: "POST" },
      );
      const readAt = new Date().toISOString();
      setOverlapAlerts((current) =>
        current.map((alert) =>
          alert.read_at === null ? { ...alert, read_at: readAt } : alert,
        ),
      );
      setUnreadAlertCount(0);
    } catch (markError) {
      setAlertError(
        markError instanceof Error ? markError.message : "无法全部标为已读",
      );
    } finally {
      setMarkingAllAlerts(false);
    }
  }

  async function loadComparison(assetId: string) {
    if (!activeWalletRef.current) return;
    const sequence = ++comparisonSequence.current;
    setComparisonAssetId(assetId);
    setComparisonDetail(null);
    setComparisonError(null);
    setComparisonLoading(true);
    try {
      const detail = await request<PositionOverlapDetail>(
        `/api/position-overlaps/${encodeURIComponent(
          assetId,
        )}?tracked_wallet_id=${encodeURIComponent(activeWalletRef.current)}`,
      );
      if (sequence !== comparisonSequence.current) return;
      setComparisonDetail(detail);
    } catch (comparisonRequestError) {
      if (sequence !== comparisonSequence.current) return;
      setComparisonError(
        comparisonRequestError instanceof Error
          ? comparisonRequestError.message
          : "无法读取共同持仓详情",
      );
    } finally {
      if (sequence === comparisonSequence.current) {
        setComparisonLoading(false);
      }
    }
  }

  function closeComparison() {
    comparisonSequence.current += 1;
    setComparisonAssetId(null);
    setComparisonDetail(null);
    setComparisonError(null);
    setComparisonLoading(false);
  }

  async function setupExecutionAccount() {
    if (!myWalletRef.current) {
      setCopyTradingError("请先在右上角设置“我的钱包”");
      return;
    }
    setCopyTradingBusy(true);
    setCopyTradingError(null);
    try {
      const signerAddress = window.prompt(
        "签名钱包地址（持有私钥的 EOA）",
        myWalletRef.current.address,
      );
      if (!signerAddress) return;
      const funderAddress = window.prompt(
        "Polymarket Proxy 资金钱包地址",
        myWalletRef.current.proxy_wallet || signerAddress,
      );
      if (!funderAddress) return;
      const existingAccount = copyDashboard?.account;
      await request<ExecutionAccount>("/api/copy-trading/account", {
        method: "PUT",
        body: JSON.stringify({
          wallet_id: Number(myWalletRef.current.id),
          signer_address: signerAddress,
          funder_address: funderAddress,
          signature_type: existingAccount?.signature_type ?? 3,
          budget_usdc: Number(existingAccount?.budget_usdc ?? 400),
          cash_reserve_usdc: Number(existingAccount?.cash_reserve_usdc ?? 240),
          max_total_exposure_usdc: Number(
            existingAccount?.max_total_exposure_usdc ?? 160,
          ),
          daily_buy_limit_usdc: Number(existingAccount?.daily_buy_limit_usdc ?? 80),
          daily_loss_limit_usdc: Number(existingAccount?.daily_loss_limit_usdc ?? 40),
          auto_redeem: existingAccount?.auto_redeem ?? true,
        }),
      });
      if (activeWalletRef.current) {
        await loadCopyDashboard(activeWalletRef.current);
      }
    } catch (accountError) {
      setCopyTradingError(
        accountError instanceof Error ? accountError.message : "无法配置执行钱包",
      );
    } finally {
      setCopyTradingBusy(false);
    }
  }

  async function verifyExecutionAccount() {
    setCopyTradingBusy(true);
    setCopyTradingError(null);
    try {
      await request<ExecutionAccount>("/api/copy-trading/account/verify", {
        method: "POST",
      });
      if (activeWalletRef.current) {
        await loadCopyDashboard(activeWalletRef.current);
      }
    } catch (accountError) {
      setCopyTradingError(
        accountError instanceof Error ? accountError.message : "执行钱包验证失败",
      );
    } finally {
      setCopyTradingBusy(false);
    }
  }

  async function setCopyTradingEnabled(enabled: boolean) {
    const subscription = copyDashboard?.subscription;
    if (!subscription) return;
    if (enabled && !window.confirm(
      "确认开启真实资金自动跟单？系统将按当前钱包配置和共享资金上限自动下单。",
    )) {
      return;
    }
    setCopyTradingBusy(true);
    setCopyTradingError(null);
    try {
      const updated = await request<CopySubscription>(
        `/api/copy-trading/subscriptions/${subscription.id}/enabled`,
        {
          method: "PUT",
          body: JSON.stringify({ enabled, confirm_live: enabled }),
        },
      );
      setCopySubscriptions((current) => [
        ...current.filter((item) => String(item.id) !== String(updated.id)),
        updated,
      ]);
      if (activeWalletRef.current) {
        await loadCopyDashboard(activeWalletRef.current);
      }
    } catch (toggleError) {
      setCopyTradingError(
        toggleError instanceof Error ? toggleError.message : "实盘开关操作失败",
      );
    } finally {
      setCopyTradingBusy(false);
    }
  }

  async function closeCopyTradingPositions() {
    const subscription = copyDashboard?.subscription;
    if (!subscription) return;
    if (!window.confirm("确认停止跟单并卖出全部自动跟单归因持仓？")) {
      return;
    }
    setCopyTradingBusy(true);
    setCopyTradingError(null);
    try {
      const updated = await request<CopySubscription>(
        `/api/copy-trading/subscriptions/${subscription.id}/action`,
        {
          method: "POST",
          body: JSON.stringify({ action: "close" }),
        },
      );
      setCopySubscriptions((current) => [
        ...current.filter((item) => String(item.id) !== String(updated.id)),
        updated,
      ]);
      if (activeWalletRef.current) {
        await loadCopyDashboard(activeWalletRef.current);
      }
    } catch (closeError) {
      setCopyTradingError(
        closeError instanceof Error ? closeError.message : "无法关闭并清仓",
      );
    } finally {
      setCopyTradingBusy(false);
    }
  }

  return (
    <main className="appShell">
      <header className="appHeader">
        <div className="brand">
          <span className="brandMark" aria-hidden="true">
            P
          </span>
          <div>
            <h1>仓位观察</h1>
            <p>看清持仓，安静跟随。</p>
          </div>
        </div>
        <div className="headerActions">
          <span className="readOnlyNote">公开监控 · 自动跟单独立风控</span>
          <button
            className="copyRatioButton"
            type="button"
            onClick={() => setSettingsModalOpen(true)}
            aria-label={`跟单比例 ${formatRatioSetting(
              globalSettings.copy_ratio_percent,
            )}%，修改`}
          >
            <span>跟单比例</span>
            <strong>
              {formatRatioSetting(globalSettings.copy_ratio_percent)}%
            </strong>
            <small>全局</small>
          </button>
          <button
            className={`myWalletButton ${myWallet ? "configured" : ""}`}
            type="button"
            onClick={() => setWalletModalMode("self")}
          >
            <span>我的钱包</span>
            <strong>
              {myWallet
                ? myWallet.label || shortenAddress(myWallet.address)
                : "尚未设置"}
            </strong>
            <small>{myWallet ? "更换" : "设置地址"}</small>
          </button>
          <button
            className="primaryButton addWalletButton"
            type="button"
            onClick={() => setWalletModalMode("tracked")}
          >
            <span aria-hidden="true">＋</span>
            添加钱包
          </button>
        </div>
      </header>

      {loadingWallets ? (
        <section className="initialLoading">
          <LoadingState label="正在读取监控钱包…" />
        </section>
      ) : wallets.length === 0 ? (
        <section className="welcomePanel">
          <span className="welcomeMark" aria-hidden="true">
            0x
          </span>
          <span className="eyebrow">从一个公开地址开始</span>
          <h2>把关心的钱包，变成清晰的持仓账本。</h2>
          <p>
            只展示真实持仓和已经发生的仓位变动，不把挂单和行情波动变成杂乱消息。
          </p>
          {error && (
            <p className="welcomeError" role="alert">
              后端暂时无法连接：{error}
            </p>
          )}
          <button
            className="primaryButton welcomeButton"
            type="button"
            onClick={() => setWalletModalMode("tracked")}
          >
            添加第一个钱包
          </button>
        </section>
      ) : (
        <>
          <section className="walletSection" aria-label="监控钱包">
            <div className="walletTabs" role="tablist" aria-label="选择钱包">
              {wallets.map((wallet) => {
                const selected = String(wallet.id) === activeWalletId;
                const copyStatus = copyTabStatus(
                  copySubscriptionMap.get(String(wallet.id)),
                );
                return (
                  <button
                    type="button"
                    role="tab"
                    aria-selected={selected}
                    className={`walletTab ${selected ? "active" : ""}`}
                    key={wallet.id}
                    onClick={() => selectWallet(String(wallet.id))}
                  >
                    <span>{wallet.label || shortenAddress(wallet.address)}</span>
                    <small>
                      {shortenAddress(wallet.proxy_wallet || wallet.address)}
                    </small>
                    <b className={`walletCopyStatus ${copyStatus.tone}`}>
                      {copyStatus.label}
                    </b>
                  </button>
                );
              })}
              <button
                className="addTab"
                type="button"
                onClick={() => setWalletModalMode("tracked")}
                aria-label="添加钱包"
              >
                ＋
              </button>
            </div>

            <div className="walletMeta">
              <div className={`syncBadge ${status.tone}`}>
                <span className="statusDot" />
                {status.label}
              </div>
              <span className="lastUpdated">
                最后成功更新：{formatDateTime(asOf || activeWallet?.last_success_at)}
              </span>
              <button
                className="refreshButton"
                type="button"
                onClick={refresh}
                disabled={refreshing}
              >
                <span
                  className={refreshing ? "spinning" : ""}
                  aria-hidden="true"
                >
                  ↻
                </span>
                {refreshing ? "同步中" : "立即刷新"}
              </button>
              <button
                className="deleteWalletButton"
                type="button"
                onClick={() => {
                  if (activeWallet) setWalletPendingDeletion(activeWallet);
                }}
                disabled={!activeWallet || refreshing}
              >
                删除观测
              </button>
            </div>
          </section>

          {(stale || activeWallet?.status === "error") && (
            <div className="staleBanner" role="status">
              <span aria-hidden="true">!</span>
              <p>
                <strong>数据更新暂时中断</strong>
                当前显示最后一次成功同步的数据
                {activeWallet?.last_error || error
                  ? `：${activeWallet?.last_error || error}`
                  : "。"}
              </p>
              <button type="button" onClick={refresh} disabled={refreshing}>
                重试
              </button>
            </div>
          )}

          <CopyTradingPanel
            dashboard={copyDashboard}
            error={copyTradingError}
            busy={copyTradingBusy}
            onConfigure={() => setCopyTradingModalOpen(true)}
            onToggle={(enabled) => void setCopyTradingEnabled(enabled)}
            onClosePositions={() => void closeCopyTradingPositions()}
            onSetupAccount={() => void setupExecutionAccount()}
            onConfigureAccountRisk={() => setExecutionRiskModalOpen(true)}
            onVerifyAccount={() => void verifyExecutionAccount()}
          />

          <section className="summaryGrid" aria-label="钱包总览">
            <article className="summaryCard primaryMetric">
              <span>当前市值</span>
              <strong>{formatMoney(displayedSummary.current_value)}</strong>
              <small>{purchaseScopeLabel}</small>
            </article>
            <article className="summaryCard">
              <span>持仓成本</span>
              <strong>{formatMoney(displayedSummary.initial_value)}</strong>
              <small>{purchaseScopeLabel}</small>
            </article>
            <article className="summaryCard">
              <span>浮动盈亏</span>
              <strong
                className={
                  toNumber(displayedSummary.cash_pnl) >= 0
                    ? "profit"
                    : "loss"
                }
              >
                {formatMoney(displayedSummary.cash_pnl)}
              </strong>
              <small>{purchaseScopeLabel}</small>
            </article>
            <article className="summaryCard">
              <span>持仓数量</span>
              <strong>{displayedSummary.count}</strong>
              <small>个活跃 outcome</small>
            </article>
          </section>

          <section className="contentPanel">
            <div className="viewHeader">
              <div className="viewTabs" role="tablist" aria-label="查看内容">
                <button
                  type="button"
                  role="tab"
                  aria-selected={view === "positions"}
                  className={view === "positions" ? "active" : ""}
                  onClick={() => setView("positions")}
                >
                  当前持仓
                  <span>{displayedSummary.count}</span>
                  {myWallet && (
                    <span className="overlapTabCount">
                      共同 {overlaps.length}
                    </span>
                  )}
                  {unreadAlertCount > 0 && (
                    <span className="alertTabCount">
                      提醒 {unreadAlertCount}
                    </span>
                  )}
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={view === "events"}
                  className={view === "events" ? "active" : ""}
                  onClick={() => setView("events")}
                >
                  仓位变动明细
                </button>
              </div>
              {view === "positions" && positions.length > 0 && (
                <div className="positionTools">
                  <label className="purchaseDateFilter">
                    <span>建仓日期</span>
                    <select
                      aria-label="按建仓日期筛选持仓"
                      value={openedDate}
                      onChange={(event) =>
                        setOpenedDate(event.target.value)
                      }
                    >
                      <option value="all">All · 全部建仓</option>
                      {openedDates.map((date) => (
                        <option key={date} value={date}>
                          {formatPurchaseDate(date)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <p className="sortNote">已按持仓价值从高到低排列</p>
                </div>
              )}
            </div>

            {view === "positions" &&
              positions.length > 0 &&
              !purchaseHistoryComplete && (
                <div className="purchaseHistoryNotice" role="status">
                  <span aria-hidden="true">i</span>
                  <p>
                    部分历史成交尚未完整回填，标记“首次监测”的持仓将按系统首次发现日期归类
                    {purchaseHistoryError
                      ? `：${purchaseHistoryError}`
                      : "。"}
                  </p>
                </div>
              )}

            {view === "positions" && positions.length > 0 && !myWallet && (
              <div className="comparisonPrompt" role="status">
                <span aria-hidden="true">◆</span>
                <p>设置“我的钱包”后，即可标出你和当前钱包的共同持仓。</p>
                <button
                  type="button"
                  onClick={() => setWalletModalMode("self")}
                >
                  现在设置
                </button>
              </div>
            )}

            {view === "positions" && overlapError && (
              <div className="comparisonPrompt error" role="alert">
                <span aria-hidden="true">!</span>
                <p>共同持仓比较暂时不可用：{overlapError}</p>
                <button
                  type="button"
                  onClick={() => {
                    if (activeWalletId) void loadContent(activeWalletId, true);
                  }}
                >
                  重试
                </button>
              </div>
            )}

            {view === "positions" && alertError && (
              <div className="comparisonPrompt error" role="alert">
                <span aria-hidden="true">!</span>
                <p>共同持仓提醒暂时不可用：{alertError}</p>
                <button
                  type="button"
                  onClick={() => {
                    if (activeWalletId) void loadContent(activeWalletId, true);
                  }}
                >
                  重试
                </button>
              </div>
            )}

            {view === "positions" && (
              <OverlapActivity
                key={activeWalletId ?? "no-wallet"}
                alerts={overlapAlerts}
                unreadCount={unreadAlertCount}
                busyAlertIds={busyAlertIds}
                markingAll={markingAllAlerts}
                onRead={(alertId) => void markOverlapAlertRead(alertId)}
                onReadAll={() => void markAllOverlapAlertsRead()}
              />
            )}

            {loadingContent && positions.length === 0 && events.length === 0 ? (
              <LoadingState label="正在同步公开持仓…" />
            ) : view === "positions" ? (
              displayedPositions.length > 0 ? (
                <PositionTable
                  positions={displayedPositions}
                  overlaps={overlapMap}
                  changeAlerts={unreadChangeAlerts}
                  copyRatioPercent={globalSettings.copy_ratio_percent}
                  onOpenComparison={(assetId) => void loadComparison(assetId)}
                  onReadAlert={(alertId) =>
                    void markOverlapAlertRead(alertId)
                  }
                />
              ) : openedDate !== "all" ? (
                <div className="emptyState">
                  <span className="emptyMark" aria-hidden="true">
                    日期
                  </span>
                  <h2>这一天没有当前持仓</h2>
                  <p>请选择其他建仓日期，或切换到 All 查看全部持仓。</p>
                  <button
                    className="secondaryButton"
                    type="button"
                    onClick={() => setOpenedDate("all")}
                  >
                    查看全部持仓
                  </button>
                </div>
              ) : (
                <EmptyState type="positions" onRefresh={refresh} />
              )
            ) : events.length > 0 ? (
              <>
                <EventList events={events} />
                {nextCursor && (
                  <div className="loadMoreWrap">
                    <button
                      className="secondaryButton"
                      type="button"
                      onClick={loadMoreEvents}
                      disabled={loadingMore}
                    >
                      {loadingMore ? "正在读取…" : "加载更早记录"}
                    </button>
                  </div>
                )}
              </>
            ) : (
              <EmptyState type="events" />
            )}
          </section>
        </>
      )}

      <footer className="appFooter">
        <span>每 15 秒检测公开持仓</span>
        <span aria-hidden="true">·</span>
        <span>挂单不会产生消息</span>
        <span aria-hidden="true">·</span>
        <span>本机运行 · 实盘密钥仅存钥匙串</span>
      </footer>

      <AddWalletModal
        key={`wallet-${walletModalMode ?? "closed"}`}
        open={walletModalMode !== null}
        mode={walletModalMode ?? "tracked"}
        onClose={() => setWalletModalMode(null)}
        onCreated={walletCreated}
      />
      <CopySettingsModal
        key={
          settingsModalOpen
            ? `copy-open-${globalSettings.copy_ratio_percent}`
            : "copy-closed"
        }
        open={settingsModalOpen}
        settings={globalSettings}
        initialError={settingsLoadError}
        onClose={() => setSettingsModalOpen(false)}
        onSaved={copySettingsSaved}
      />
      <CopyTradingModal
        key={
          copyTradingModalOpen
            ? `auto-copy-${copyDashboard?.subscription?.id ?? activeWalletId}`
            : "auto-copy-closed"
        }
        open={copyTradingModalOpen}
        trackedWalletId={activeWalletId}
        subscription={copyDashboard?.subscription ?? null}
        onClose={() => setCopyTradingModalOpen(false)}
        onSaved={(subscription) => {
          setCopyTradingModalOpen(false);
          setCopyTradingError(null);
          setCopySubscriptions((current) => [
            ...current.filter((item) => String(item.id) !== String(subscription.id)),
            subscription,
          ]);
          setCopyDashboard((current) =>
            current ? { ...current, subscription } : current,
          );
          if (activeWalletRef.current) {
            void loadCopyDashboard(activeWalletRef.current);
          }
        }}
      />
      <ExecutionRiskModal
        key={
          executionRiskModalOpen
            ? `execution-risk-${copyDashboard?.account?.cash_reserve_usdc ?? "none"}`
            : "execution-risk-closed"
        }
        open={executionRiskModalOpen}
        account={copyDashboard?.account ?? null}
        onClose={() => setExecutionRiskModalOpen(false)}
        onSaved={(account) => {
          setExecutionRiskModalOpen(false);
          setCopyTradingError(null);
          setCopyDashboard((current) =>
            current ? { ...current, account } : current,
          );
        }}
      />
      <DeleteWalletModal
        key={
          walletPendingDeletion
            ? `delete-${String(walletPendingDeletion.id)}`
            : "delete-closed"
        }
        wallet={walletPendingDeletion}
        onClose={() => setWalletPendingDeletion(null)}
        onConfirm={deleteObservedWallet}
      />
      <ComparisonModal
        assetId={comparisonAssetId}
        detail={comparisonDetail}
        loading={comparisonLoading}
        error={comparisonError}
        onClose={closeComparison}
        onRetry={() => {
          if (comparisonAssetId) void loadComparison(comparisonAssetId);
        }}
      />
    </main>
  );
}
