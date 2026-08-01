"use client";

import {
  FormEvent,
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
  purchase_lots?: PurchaseLot[];
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
  }).format(date);
}

function positionForPurchaseDate(
  position: Position,
  purchaseDate: string,
): Position | null {
  const lots = (position.purchase_lots ?? []).filter(
    (lot) => lot.purchase_date === purchaseDate,
  );
  if (lots.length === 0) return null;

  const size = lots.reduce((total, lot) => total + toNumber(lot.size), 0);
  const initialValue = lots.reduce(
    (total, lot) => total + toNumber(lot.initial_value),
    0,
  );
  const currentValue = lots.reduce(
    (total, lot) => total + toNumber(lot.current_value),
    0,
  );
  const cashPnl = currentValue - initialValue;
  return {
    ...position,
    size,
    avg_price: size > 0 ? initialValue / size : 0,
    initial_value: initialValue,
    current_value: currentValue,
    cash_pnl: cashPnl,
    percent_pnl: initialValue > 0 ? (cashPnl / initialValue) * 100 : 0,
    purchase_lots: lots,
  };
}

function purchaseDateLabel(position: Position) {
  const dates = [
    ...new Set((position.purchase_lots ?? []).map((lot) => lot.purchase_date)),
  ];
  if (dates.length === 0) return "日期待回填";
  if (dates.length === 1) return formatPurchaseDate(dates[0]);
  return `${formatPurchaseDate(dates[dates.length - 1])} 等 ${dates.length} 天`;
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
  return (
    <>
      <div className="tableWrap desktopPositions">
        <table className="positionsTable">
          <thead>
            <tr>
              <th>市场 / 方向</th>
              <th className="numberCell">平均买入</th>
              <th className="numberCell">当前价格</th>
              <th>买入批次</th>
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
              return (
                <tr
                  className={overlap ? "overlapRow" : ""}
                  key={`${position.asset_id}-${position.condition_id}`}
                >
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
                    {purchaseDateLabel(position)}
                  </td>
                  <td className="openedAtCell">
                    <time dateTime={position.first_opened_at ?? undefined}>
                      {formatPositionDateTime(position.first_opened_at)}
                    </time>
                    {position.first_opened_at_source === "first_seen" && (
                      <small>首次监测</small>
                    )}
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
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="positionCards">
        {positions.map((position) => {
          const overlap = overlaps.get(position.asset_id);
          const changeAlert = changeAlerts.get(position.asset_id);
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
                  <dt>买入批次</dt>
                  <dd>{purchaseDateLabel(position)}</dd>
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
  if (alerts.length === 0) return null;

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
      <div className="overlapActivityList">
        {alerts.map((alert) => {
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
    </section>
  );
}

export default function Home() {
  const [globalSettings, setGlobalSettings] = useState<GlobalSettings>({
    copy_ratio_percent: 10,
  });
  const [settingsModalOpen, setSettingsModalOpen] = useState(false);
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
  const [purchaseDates, setPurchaseDates] = useState<string[]>([]);
  const [purchaseDate, setPurchaseDate] = useState("all");
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
        const nextPurchaseDates = positionData.purchase_dates ?? [];
        setPurchaseDates(nextPurchaseDates);
        setPurchaseDate((current) =>
          current === "all" || nextPurchaseDates.includes(current)
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
    if (!activeWalletId) return;
    const timer = window.setTimeout(
      () => void loadContent(activeWalletId),
      0,
    );
    return () => window.clearTimeout(timer);
  }, [activeWalletId, loadContent]);

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
      const walletId = activeWalletRef.current;
      if (walletId) void loadContent(walletId, true);
    }, 30_000);
    return () => window.clearInterval(fallbackRefresh);
  }, [loadContent, loadWallets]);

  const activeWallet = wallets.find(
    (wallet) => String(wallet.id) === activeWalletId,
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
      purchaseDate === "all"
        ? positions
        : positions
            .map((position) =>
              positionForPurchaseDate(position, purchaseDate),
            )
            .filter((position): position is Position => position !== null);
    return [...selected].sort(
        (left, right) =>
          toNumber(right.current_value) - toNumber(left.current_value),
      );
  }, [positions, purchaseDate]);

  const displayedSummary = useMemo<PositionSummary>(() => {
    if (purchaseDate === "all") return summary;
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
  }, [displayedPositions, purchaseDate, summary]);

  const purchaseScopeLabel =
    purchaseDate === "all" ? "全部买入批次" : formatPurchaseDate(purchaseDate);

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
    setBusyAlertIds(new Set());
    setMarkingAllAlerts(false);
    setEvents([]);
    setSummary(emptySummary);
    setPurchaseDates([]);
    setPurchaseDate("all");
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
        setPurchaseDates([]);
        setPurchaseDate("all");
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
          <span className="readOnlyNote">只读监控 · 不连接钱包</span>
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
                    <span>购买日期</span>
                    <select
                      aria-label="按购买日期筛选持仓"
                      value={purchaseDate}
                      onChange={(event) =>
                        setPurchaseDate(event.target.value)
                      }
                    >
                      <option value="all">All · 全部批次</option>
                      {purchaseDates.map((date) => (
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
                    部分历史成交尚未完整回填，日期筛选暂时只包含已识别的买入批次
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
              ) : purchaseDate !== "all" ? (
                <div className="emptyState">
                  <span className="emptyMark" aria-hidden="true">
                    日期
                  </span>
                  <h2>这一天没有剩余持仓批次</h2>
                  <p>请选择其他购买日期，或切换到 All 查看全部持仓。</p>
                  <button
                    className="secondaryButton"
                    type="button"
                    onClick={() => setPurchaseDate("all")}
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
        <span>本机只读运行</span>
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
