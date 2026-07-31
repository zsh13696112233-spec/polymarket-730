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

type Wallet = {
  id: number | string;
  address: string;
  proxy_wallet: string;
  label: string;
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
  type: "opened" | "increased" | "decreased" | "closed";
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
  fills: Fill[];
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

function formatShares(value: string | number | null | undefined) {
  return compactNumberFormatter.format(toNumber(value));
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
  }[type];
}

function eventTone(type: PositionEvent["type"]) {
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

function PositionTable({ positions }: { positions: Position[] }) {
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
              <th className="numberCell">持仓份额</th>
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
            {positions.map((position) => (
              <tr key={`${position.asset_id}-${position.condition_id}`}>
                <td className="marketCell">
                  <MarketTitle
                    title={position.title}
                    outcome={position.outcome}
                    eventSlug={position.event_slug}
                    marketSlug={position.market_slug}
                  />
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
                <td className="numberCell">
                  {formatShares(position.size)}
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
            ))}
          </tbody>
        </table>
      </div>

      <div className="positionCards">
        {positions.map((position) => (
          <article
            className="positionCard"
            key={`card-${position.asset_id}-${position.condition_id}`}
          >
            <MarketTitle
              title={position.title}
              outcome={position.outcome}
              eventSlug={position.event_slug}
              marketSlug={position.market_slug}
            />
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
        ))}
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
        return (
          <details className="eventCard" key={event.id}>
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
                <span>份额变化</span>
                <strong className={delta >= 0 ? "profit" : "loss"}>
                  {delta > 0 ? "+" : ""}
                  {formatShares(event.delta_size)}
                </strong>
              </div>

              <div className="eventPrice">
                <span>合并成交均价</span>
                <strong>
                  {event.average_fill_price
                    ? formatPrice(event.average_fill_price)
                    : "—"}
                </strong>
              </div>

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
                <ReconciliationStatus status={event.reconciliation_status} />
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
                          {fill.side.toUpperCase() === "BUY" ? "买入" : "卖出"}
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
      <h2>{type === "positions" ? "当前没有活跃持仓" : "还没有加减仓记录"}</h2>
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
  onClose,
  onCreated,
}: {
  open: boolean;
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
      const wallet = await request<Wallet>("/api/wallets", {
        method: "POST",
        body: JSON.stringify({
          address: trimmedAddress,
          ...(label.trim() ? { label: label.trim() } : {}),
        }),
      });
      onCreated(wallet);
    } catch (submitError) {
      setError(
        submitError instanceof Error ? submitError.message : "添加钱包失败",
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
        aria-labelledby="add-wallet-title"
      >
        <div className="modalHeader">
          <div>
            <span className="eyebrow">新增监控</span>
            <h2 id="add-wallet-title">添加 Polymarket 钱包</h2>
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
            只读取公开持仓，不连接钱包，不需要私钥。
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
              {submitting ? "正在添加…" : "开始监控"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

export default function Home() {
  const [wallets, setWallets] = useState<Wallet[]>([]);
  const [activeWalletId, setActiveWalletId] = useState<string | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
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
  const [addWalletOpen, setAddWalletOpen] = useState(false);
  const requestSequence = useRef(0);
  const activeWalletRef = useRef<string | null>(null);

  useEffect(() => {
    activeWalletRef.current = activeWalletId;
  }, [activeWalletId]);

  const loadWallets = useCallback(async () => {
    try {
      const walletList = await request<Wallet[]>("/api/wallets");
      const enabledWallets = walletList.filter(
        (wallet) => wallet.enabled !== false,
      );
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
        const [positionData, eventData] = await Promise.all([
          request<PositionResponse>(
            `/api/positions?wallet_id=${encodeURIComponent(walletId)}`,
          ),
          request<EventsResponse>(
            `/api/position-events?wallet_id=${encodeURIComponent(walletId)}`,
          ),
        ]);

        if (sequence !== requestSequence.current) return;
        const sortedPositions = [...positionData.items].sort(
          (left, right) =>
            toNumber(right.current_value) - toNumber(left.current_value),
        );
        setPositions(sortedPositions);
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
          eventWalletId === activeWalletRef.current &&
          (payload.type === "positions.updated" ||
            payload.type === "events.created" ||
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
      await request<Record<string, unknown>>(
        `/api/wallets/${encodeURIComponent(activeWalletId)}/sync`,
        { method: "POST" },
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

  function selectWallet(walletId: string) {
    if (walletId === activeWalletRef.current) return;
    requestSequence.current += 1;
    setPositions([]);
    setEvents([]);
    setSummary(emptySummary);
    setPurchaseDates([]);
    setPurchaseDate("all");
    setPurchaseHistoryComplete(false);
    setPurchaseHistoryError(null);
    setAsOf(null);
    setStale(false);
    setError(null);
    setLoadingContent(true);
    setActiveWalletId(walletId);
  }

  function walletCreated(wallet: Wallet) {
    setAddWalletOpen(false);
    setWallets((current) => {
      const next = current.filter(
        (item) => String(item.id) !== String(wallet.id),
      );
      return [...next, wallet];
    });
    selectWallet(String(wallet.id));
    void loadWallets();
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
            className="primaryButton addWalletButton"
            type="button"
            onClick={() => setAddWalletOpen(true)}
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
            只展示真实持仓和已经发生的加减仓，不把挂单和行情波动变成杂乱消息。
          </p>
          {error && (
            <p className="welcomeError" role="alert">
              后端暂时无法连接：{error}
            </p>
          )}
          <button
            className="primaryButton welcomeButton"
            type="button"
            onClick={() => setAddWalletOpen(true)}
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
                onClick={() => setAddWalletOpen(true)}
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
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={view === "events"}
                  className={view === "events" ? "active" : ""}
                  onClick={() => setView("events")}
                >
                  加减仓明细
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

            {loadingContent && positions.length === 0 && events.length === 0 ? (
              <LoadingState label="正在同步公开持仓…" />
            ) : view === "positions" ? (
              displayedPositions.length > 0 ? (
                <PositionTable positions={displayedPositions} />
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
        key={addWalletOpen ? "open" : "closed"}
        open={addWalletOpen}
        onClose={() => setAddWalletOpen(false)}
        onCreated={walletCreated}
      />
    </main>
  );
}
