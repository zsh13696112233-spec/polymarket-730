"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PolyCopyShell } from "./PolyCopyShell";
import { WhaleSettingsPanel } from "./WhaleSettingsWorkspace";
import { WhaleRequestMonitorPanel } from "./WhaleRequestMonitorPanel";
import { WhaleStatisticsPanel } from "./WhaleStatisticsPanel";
import {
  ApiError,
  ModalShell,
  WhaleEntry,
  WhaleFollowPreview,
  WhaleHistory,
  WhaleHistoryList,
  WhaleMarket,
  WhaleMarketList,
  WhaleMarketSide,
  WhaleOrder,
  WhaleSettings,
  WhaleRule,
  formatBeijing,
  formatCompactUsdc,
  formatPercent,
  formatPrice,
  formatSigned,
  formatUsdc,
  marketUrl,
  numeric,
  profileUrl,
  shortAddress,
  whaleApi,
} from "./WhaleShared";

type WalletSort = "value" | "recent";

const RULE_LABELS: Record<WhaleRule, string> = {
  new_account: "新号大额",
  large_amount: "全量超大额",
};

type WalletHolding = {
  market: WhaleMarket;
  side: WhaleMarketSide;
  entry: WhaleEntry;
  dualSided: boolean;
};

type WalletGroup = {
  address: string;
  displayName: string;
  avatarUrl: string | null;
  profileUrl: string | null | undefined;
  createdAt: string | null;
  verified: boolean;
  holdings: WalletHolding[];
  totalValue: number;
  latestBuyAt: string;
};

function formatRegistrationDate(value: string | null): string {
  if (!value) return "未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未知";
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

function holdingValue(entry: WhaleEntry): number {
  return entry.current_value_usdc === null || entry.current_value_usdc === undefined
    ? 0
    : numeric(entry.current_value_usdc);
}

export function buildWalletGroups(
  markets: WhaleMarket[],
  sort: WalletSort,
): WalletGroup[] {
  const walletMarketSides = new Map<string, Set<string>>();

  for (const market of markets) {
    for (const side of market.sides) {
      for (const entry of side.entries) {
        if (numeric(entry.net_size) <= 0) continue;
        const key = `${entry.proxy_wallet.toLowerCase()}:${market.condition_id}`;
        const sides = walletMarketSides.get(key) ?? new Set<string>();
        sides.add(side.asset_id);
        walletMarketSides.set(key, sides);
      }
    }
  }

  const groups = new Map<string, WalletGroup>();
  for (const market of markets) {
    for (const side of market.sides) {
      for (const entry of side.entries) {
        if (numeric(entry.net_size) <= 0) continue;
        const addressKey = entry.proxy_wallet.toLowerCase();
        const group = groups.get(addressKey) ?? {
          address: entry.proxy_wallet,
          displayName: entry.display_name || shortAddress(entry.proxy_wallet),
          avatarUrl: entry.wallet_avatar_url,
          profileUrl: entry.profile_url,
          createdAt: entry.wallet_created_at,
          verified: entry.verified_badge,
          holdings: [],
          totalValue: 0,
          latestBuyAt: entry.last_buy_at,
        };
        group.holdings.push({
          market,
          side,
          entry,
          dualSided: (walletMarketSides.get(`${addressKey}:${market.condition_id}`)?.size ?? 0) > 1,
        });
        group.totalValue += holdingValue(entry);
        if (entry.last_buy_at > group.latestBuyAt) group.latestBuyAt = entry.last_buy_at;
        groups.set(addressKey, group);
      }
    }
  }

  const values = Array.from(groups.values());
  for (const group of values) {
    group.holdings.sort((left, right) => holdingValue(right.entry) - holdingValue(left.entry));
  }
  values.sort((left, right) =>
    sort === "recent"
      ? right.latestBuyAt.localeCompare(left.latestBuyAt)
      : right.totalValue - left.totalValue,
  );
  return values;
}

export default function WhaleDiscoveryWorkspace() {
  const [settings, setSettings] = useState<WhaleSettings | null>(null);
  const [markets, setMarkets] = useState<WhaleMarketList | null>(null);
  const [history, setHistory] = useState<WhaleHistoryList | null>(null);
  const [rule, setRule] = useState<WhaleRule>("new_account");
  const [statisticsVisible, setStatisticsVisible] = useState(false);
  const [statisticsRefreshToken, setStatisticsRefreshToken] = useState(0);
  const [sort, setSort] = useState<WalletSort>("value");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [followTarget, setFollowTarget] = useState<{
    market: WhaleMarket;
    side: WhaleMarketSide;
    entry: WhaleEntry;
  } | null>(null);

  const loadSettings = useCallback(async () => {
    try {
      setSettings(await whaleApi<WhaleSettings>("/api/whales/settings"));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "巨鲸配置加载失败");
    }
  }, []);

  const loadMarkets = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setMarkets(await whaleApi<WhaleMarketList>(
        `/api/whales/markets?rule=${rule}&include_exited=false&include_hedged=true&limit=100&offset=0`,
      ));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "巨鲸持仓加载失败");
    } finally {
      setLoading(false);
    }
  }, [rule]);

  const loadHistory = useCallback(async () => {
    try {
      setHistory(await whaleApi<WhaleHistoryList>(`/api/whales/history?rule=${rule}&limit=100&offset=0`));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "巨鲸历史记录加载失败");
    }
  }, [rule]);

  useEffect(() => {
    const timer = window.setTimeout(() => void Promise.all([loadSettings(), loadMarkets(), loadHistory()]), 0);
    return () => window.clearTimeout(timer);
  }, [loadHistory, loadMarkets, loadSettings]);

  const scanNow = async () => {
    setRefreshing(true);
    setError(null);
    try {
      const scan = await whaleApi<{ status: string }>("/api/whales/scan", { method: "POST" });
      if (statisticsVisible) {
        await loadSettings();
        setStatisticsRefreshToken((current) => current + 1);
      } else {
        await Promise.all([loadSettings(), loadMarkets(), loadHistory()]);
      }
      if (scan.status !== "ok") setError("本轮刷新没有完成，当前展示的是上一次成功获取的数据。");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "刷新巨鲸持仓失败");
    } finally {
      setRefreshing(false);
    }
  };

  const reloadWorkspace = useCallback(async () => {
    await Promise.all([loadSettings(), loadMarkets(), loadHistory()]);
    setStatisticsRefreshToken((current) => current + 1);
  }, [loadHistory, loadMarkets, loadSettings]);

  const walletGroups = useMemo(
    () => buildWalletGroups(markets?.items ?? [], sort),
    [markets, sort],
  );
  const visibleHoldingCount = walletGroups.reduce(
    (total, group) => total + group.holdings.length,
    0,
  );

  return (
    <PolyCopyShell
      active="whales"
      title="巨鲸监测"
      subtitle="分别监测新号大额买入和全量超大额买入，永久保留触发历史。"
      actions={
        <>
          <Link className="pcButton ghost" href="/whales/records">我的跟单</Link>
          <button className="pcButton primary" type="button" onClick={scanNow} disabled={refreshing}>
            <span className={refreshing ? "spinning" : ""}>↻</span>
            {refreshing ? "刷新中" : "刷新数据"}
          </button>
        </>
      }
    >
      <nav className="whaleRuleTabs" aria-label="巨鲸监控规则">
        {(["new_account", "large_amount"] as WhaleRule[]).map((item) => (
          <button
            key={item}
            type="button"
            className={!statisticsVisible && rule === item ? "active" : ""}
            aria-pressed={!statisticsVisible && rule === item}
            onClick={() => { setRule(item); setStatisticsVisible(false); }}
          >
            <strong>{RULE_LABELS[item]}</strong>
            <span>
              当前 {item === "new_account" ? settings?.new_account_active_count ?? 0 : settings?.large_amount_active_count ?? 0}
              {" · "}历史 {item === "new_account" ? settings?.new_account_history_count ?? 0 : settings?.large_amount_history_count ?? 0}
            </span>
          </button>
        ))}
        <button
          type="button"
          className={statisticsVisible ? "active" : ""}
          aria-pressed={statisticsVisible}
          onClick={() => setStatisticsVisible(true)}
        >
          <strong>统计</strong>
          <span>命中率 · 理论收益 · 结算明细</span>
        </button>
      </nav>

      {!statisticsVisible && <div id="whale-monitor-settings" className="whaleInlineSettings">
        <WhaleSettingsPanel
          settings={settings}
          onSettingsChange={setSettings}
          onReload={reloadWorkspace}
        />
      </div>}

      {Boolean(settings?.last_scan_error || settings?.consecutive_failures) && (
        <div className="whaleHealthBanner danger">
          <span aria-hidden="true">!</span>
          <div>
            <strong>最近刷新未完成</strong>
            <p>{settings?.last_scan_error || "当前展示的是最后一次成功获取的数据。"}</p>
          </div>
          <button type="button" onClick={scanNow} disabled={refreshing}>重试</button>
        </div>
      )}

      {!statisticsVisible && <WhaleRequestMonitorPanel />}

      {statisticsVisible ? (
        <WhaleStatisticsPanel refreshToken={statisticsRefreshToken} />
      ) : <><section className="pcPanel whaleSimpleToolbar" aria-label="巨鲸持仓工具栏">
        <label className="whaleSimpleSort">
          <span>排序</span>
          <select
            aria-label="巨鲸钱包排序"
            value={sort}
            onChange={(event) => setSort(event.target.value as WalletSort)}
          >
            <option value="value">持仓价值最高</option>
            <option value="recent">最近加仓</option>
          </select>
        </label>
        <div className="whaleSimpleMeta">
          <strong>{walletGroups.length} 个钱包 · {visibleHoldingCount} 个持仓</strong>
          <span>
            {rule === "new_account" ? `注册 ≤ ${settings?.registration_window_days ?? 7} 天 · ` : "不限账号年龄 · "}
            近 {settings?.window_hours ?? 24} 小时买入 ≥ {formatCompactUsdc(rule === "new_account" ? settings?.new_account_threshold_usdc : settings?.large_amount_threshold_usdc)}
            {" · "}最后更新 {formatBeijing(settings?.last_scan_at, true)}
          </span>
        </div>
      </section>

      {error && (
        <div className="pcAlert danger whalePageError" role="alert">
          <strong>请求未完成</strong>
          <p>{error}</p>
          <button type="button" onClick={() => void loadMarkets()}>重试</button>
        </div>
      )}

      {loading && !markets ? (
        <div className="pcPanel pcLoading whaleLoading">正在汇总巨鲸持仓…</div>
      ) : walletGroups.length ? (
        <div className={`whaleWalletList ${loading ? "refreshing" : ""}`} aria-live="polite">
          {walletGroups.map((group) => (
            <WhaleWalletCard
              key={group.address}
              group={group}
              onFollow={({ market, side, entry }) => setFollowTarget({ market, side, entry })}
            />
          ))}
        </div>
      ) : (
        <section className="pcPanel pcEmptyState whaleEmptyState">
          <div className="pcEmptyIcon">◈</div>
          <h2>暂未发现仍在持有的巨鲸钱包</h2>
          <p>刷新数据后，新发现的大额持仓会出现在这里。</p>
        </section>
      )}

      <WhaleHistorySection rule={rule} history={history} />
      </>}

      {!statisticsVisible && followTarget && (
        <WhaleFollowModal
          {...followTarget}
          maxAmount={numeric(settings?.max_follow_amount_usdc ?? 200)}
          defaultAmount={numeric(settings?.default_follow_amount_usdc ?? 20)}
          onClose={() => setFollowTarget(null)}
          onCompleted={() => void loadMarkets()}
        />
      )}
    </PolyCopyShell>
  );
}

const INACTIVE_REASON_LABELS: Record<string, string> = {
  position_exited: "触发后退出",
  market_closed: "市场已结算",
  below_threshold: "滚出24小时门槛",
  account_age_exceeded: "账号超过新号窗口",
  profile_unavailable: "账号资料不可用",
  position_check_failed: "持仓核验失败",
  market_metadata_unavailable: "市场资料暂不可用",
  orders_not_accepted: "市场暂停接单",
  low_liquidity: "流动性不足",
  ending_soon: "临近结束",
  rule_inactive: "规则已转入历史",
};

function WhaleRuleBadges({ rules }: { rules: WhaleRule[] }) {
  return (
    <span className="whaleRuleBadges">
      {rules.map((item) => <span className={`pcBadge ${item === "new_account" ? "warning" : "danger"}`} key={item}>{RULE_LABELS[item]}</span>)}
    </span>
  );
}

function WhaleHistorySection({ rule, history }: { rule: WhaleRule; history: WhaleHistoryList | null }) {
  return (
    <section className="pcPanel whaleHistoryPanel" aria-label={`${RULE_LABELS[rule]}历史记录`}>
      <header className="pcPanelHeader">
        <div>
          <span className="pcEyebrow">TRIGGER HISTORY</span>
          <h2>历史触发记录</h2>
          <p>金额达标后永久保留；理论结算盈亏按触发窗口买入份额全部持有到结算计算。</p>
        </div>
        <strong>{history?.total ?? 0} 条</strong>
      </header>
      {history?.items.length ? (
        <div className="whaleHistoryTableWrap">
          <table className="whaleHistoryTable">
            <thead><tr><th>钱包 / 规则</th><th>市场 / 方向</th><th>24小时买入</th><th>均价</th><th>监测建仓 / 触发 / 失效</th><th>状态</th><th>结算价 / 理论盈亏</th></tr></thead>
            <tbody>
              {history.items.map((item) => <WhaleHistoryRow key={`${item.entry_id}:${item.rule_type}`} item={item} />)}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="whaleHistoryEmpty">该规则暂无历史触发记录。</div>
      )}
    </section>
  );
}

function WhaleHistoryRow({ item }: { item: WhaleHistory }) {
  const link = marketUrl(item.event_slug || item.market_slug, null);
  const reason = INACTIVE_REASON_LABELS[item.inactive_reason || ""] || item.inactive_reason || "已失效";
  return (
    <tr>
      <td><a className="whaleHistoryWalletLink" href={profileUrl(item.proxy_wallet)} target="_blank" rel="noreferrer"><strong>{item.display_name || shortAddress(item.proxy_wallet)} ↗</strong><small>{shortAddress(item.proxy_wallet)} · 注册 {formatRegistrationDate(item.wallet_created_at)}</small></a><WhaleRuleBadges rules={item.matched_rules} /></td>
      <td><a href={link} target="_blank" rel="noreferrer">{item.title} ↗</a><small>{item.outcome}</small></td>
      <td className="numeric"><strong>{formatCompactUsdc(item.gross_buy_usdc)}</strong><small>{numeric(item.gross_buy_size).toFixed(2)} 份</small></td>
      <td className="numeric">{formatPrice(item.avg_buy_price)}</td>
      <td><strong>建仓 {formatBeijing(item.first_buy_at)}</strong><small>触发 {formatBeijing(item.first_triggered_at)}</small><small>失效 {formatBeijing(item.inactive_at || item.last_qualified_at)}</small></td>
      <td><span className="pcBadge muted">{reason}</span></td>
      <td className="numeric"><strong>{item.settlement_price == null ? "待结算" : formatPrice(item.settlement_price)}</strong><small className={item.hold_to_settlement_pnl_usdc != null && numeric(item.hold_to_settlement_pnl_usdc) >= 0 ? "profit" : "loss"}>{item.hold_to_settlement_pnl_usdc == null ? "—" : formatSigned(item.hold_to_settlement_pnl_usdc, " USDC")}</small></td>
    </tr>
  );
}

function WhaleWalletCard({
  group,
  onFollow,
}: {
  group: WalletGroup;
  onFollow: (holding: WalletHolding) => void;
}) {
  return (
    <article className="pcPanel whaleWalletCard">
      <header className="whaleWalletHeader">
        <a
          href={profileUrl(group.address, group.profileUrl)}
          target="_blank"
          rel="noreferrer"
          className="whaleWalletIdentity"
        >
          <WhaleWalletAvatar
            key={group.avatarUrl || "fallback"}
            address={group.address}
            name={group.displayName}
            url={group.avatarUrl}
          />
          <div>
            <strong>{group.displayName}</strong>
            <span>{shortAddress(group.address)} ↗</span>
            <small>注册日期 {formatRegistrationDate(group.createdAt)}{group.verified ? " · 已认证" : ""}</small>
          </div>
        </a>
        <div className="whaleWalletTotal">
          <span>当前持仓估值</span>
          <strong>{formatCompactUsdc(group.totalValue)}</strong>
          <small>{group.holdings.length} 个持仓</small>
        </div>
      </header>
      <div className="whaleHoldingHeader" aria-hidden="true">
        <span>市场 / 方向</span><span>24小时买入</span><span>剩余持仓</span><span>当前估值</span><span>买入均价</span><span>当前价</span><span>建仓 / 加仓 / 触发</span><span>操作</span>
      </div>
      <div className="whaleHoldingRows">
        {group.holdings.map((holding) => (
          <WhaleHoldingRow
            key={`${holding.market.condition_id}:${holding.side.asset_id}:${holding.entry.entry_id}`}
            holding={holding}
            onFollow={() => onFollow(holding)}
          />
        ))}
      </div>
    </article>
  );
}

function walletAvatarInitials(name: string, address: string): string {
  const cleanName = name.trim();
  if (!cleanName || cleanName.toLowerCase() === shortAddress(address).toLowerCase()) {
    return address.slice(2, 4).toUpperCase();
  }
  const words = cleanName.split(/\s+/).filter(Boolean);
  return (words.length > 1 ? `${words[0][0]}${words[1][0]}` : cleanName.slice(0, 2)).toUpperCase();
}

function walletAvatarHue(address: string): number {
  return Array.from(address.toLowerCase()).reduce(
    (hash, character) => (hash * 31 + character.charCodeAt(0)) % 360,
    0,
  );
}

function WhaleWalletAvatar({
  address,
  name,
  url,
}: {
  address: string;
  name: string;
  url: string | null;
}) {
  const [failed, setFailed] = useState(false);

  if (!url || failed) {
    const hue = walletAvatarHue(address);
    return (
      <span
        className="whaleWalletAvatar whaleWalletAvatarFallback"
        style={{
          background: `linear-gradient(145deg, hsl(${hue} 58% 34%), hsl(${(hue + 42) % 360} 62% 16%))`,
        }}
        title="该钱包未设置公开头像"
        aria-label={`${name} 钱包默认头像`}
      >
        {walletAvatarInitials(name, address)}
      </span>
    );
  }
  return (
    // Wallet images are user-provided remote URLs, so the optimized image host list is not bounded.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      className="whaleWalletAvatar"
      src={url}
      alt={`${name} 钱包头像`}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
    />
  );
}

function WhaleHoldingRow({ holding, onFollow }: { holding: WalletHolding; onFollow: () => void }) {
  const { market, side, entry } = holding;
  const currentPrice = side.current_price === null ? null : numeric(side.current_price);
  const disabledReason = !entry.follow_eligible
    ? INACTIVE_REASON_LABELS[entry.follow_ineligible_reason || ""] || entry.follow_ineligible_reason || "当前不可跟买"
    : !side.asset_id
    ? "市场缺少交易标识"
    : currentPrice === null || currentPrice <= 0 || currentPrice >= 1
      ? "当前没有有效报价"
      : null;

  return (
    <div className="whaleHoldingRow">
      <div className="whaleHoldingMarket">
        {market.icon_url ? (
          // Gamma market artwork is served from multiple remote hosts.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={market.icon_url} alt="" className="whaleHoldingMarketIcon" />
        ) : (
          <span className="whaleHoldingMarketIcon fallback" aria-hidden="true">◇</span>
        )}
        <div className="whaleHoldingMarketContent">
          <a
            href={marketUrl(market.event_slug || market.market_slug, market.polymarket_url)}
            target="_blank"
            rel="noreferrer"
          >
            {market.title} <span aria-hidden="true">↗</span>
          </a>
          <div>
            <span className={`whaleOutcomeMark ${side.outcome_index === 0 ? "positive" : "negative"}`}>{side.outcome}</span>
            {holding.dualSided && <span className="pcBadge warning">双向持仓</span>}
            <WhaleRuleBadges rules={entry.matched_rules} />
          </div>
        </div>
      </div>
      <div className="whaleHoldingMetric"><strong>{formatCompactUsdc(entry.gross_buy_usdc)}</strong><small>{entry.trade_count} 笔累计</small></div>
      <div className="whaleHoldingMetric"><strong>{numeric(entry.net_size).toFixed(2)}</strong><small>份</small></div>
      <div className="whaleHoldingMetric"><strong>{entry.current_value_usdc == null ? "—" : formatCompactUsdc(entry.current_value_usdc)}</strong><small>按现价估算</small></div>
      <div className="whaleHoldingMetric"><strong>{formatPrice(entry.avg_buy_price)}</strong><small>USDC</small></div>
      <div className="whaleHoldingMetric"><strong>{formatPrice(side.current_price)}</strong><small>USDC</small></div>
      <div className="whaleHoldingMetric whaleHoldingTimes"><strong>{formatBeijing(entry.first_buy_at)}</strong><small>监测建仓</small><small>最近加仓 {formatBeijing(entry.last_buy_at)}</small><small>首次触发 {formatBeijing(entry.first_triggered_at)}</small></div>
      <div className="whaleHoldingAction">
        <button className="pcButton primary" type="button" onClick={onFollow} disabled={Boolean(disabledReason)}>跟单</button>
        {disabledReason && <small>{disabledReason}</small>}
      </div>
    </div>
  );
}

function WhaleFollowModal({
  market,
  side,
  entry,
  maxAmount,
  defaultAmount,
  onClose,
  onCompleted,
}: {
  market: WhaleMarket;
  side: WhaleMarketSide;
  entry: WhaleEntry;
  maxAmount: number;
  defaultAmount: number;
  onClose: () => void;
  onCompleted: () => void;
}) {
  const initialAmount = Math.min(Math.max(defaultAmount, 0.01), maxAmount);
  const [amount, setAmount] = useState(String(initialAmount));
  const [preview, setPreview] = useState<WhaleFollowPreview | null>(null);
  const [previewing, setPreviewing] = useState(true);
  const [executing, setExecuting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [order, setOrder] = useState<WhaleOrder | null>(null);
  const requestSerial = useRef(0);

  const requestPreview = useCallback(async () => {
    const requestId = ++requestSerial.current;
    const amountValue = Number(amount);
    if (!Number.isFinite(amountValue) || amountValue <= 0) {
      setPreview(null);
      setPreviewing(false);
      setError("请输入大于 0 的跟单金额");
      return;
    }
    if (amountValue > maxAmount) {
      setPreview(null);
      setPreviewing(false);
      setError(`单笔跟单金额不能超过 ${formatUsdc(maxAmount)}`);
      return;
    }
    setPreviewing(true);
    setError(null);
    try {
      const next = await whaleApi<WhaleFollowPreview>("/api/whales/follow/preview", {
        method: "POST",
        body: JSON.stringify({
          asset_id: side.asset_id,
          amount_usdc: amountValue,
          entry_id: entry.entry_id,
        }),
      });
      if (requestId === requestSerial.current) setPreview(next);
    } catch (requestError) {
      if (requestId !== requestSerial.current) return;
      setPreview(null);
      setError(requestError instanceof Error ? requestError.message : "跟单预览失败");
    } finally {
      if (requestId === requestSerial.current) setPreviewing(false);
    }
  }, [amount, entry.entry_id, maxAmount, side.asset_id]);

  useEffect(() => {
    const timer = window.setTimeout(() => void requestPreview(), 400);
    return () => window.clearTimeout(timer);
  }, [requestPreview]);

  const changeAmount = (value: string) => {
    requestSerial.current += 1;
    setPreview(null);
    setPreviewing(true);
    setError(null);
    setAmount(value);
  };

  const execute = async () => {
    if (!preview) return;
    setExecuting(true);
    setError(null);
    try {
      const result = await whaleApi<WhaleOrder>("/api/whales/follow/execute", {
        method: "POST",
        body: JSON.stringify({
          confirmation_id: preview.confirmation_id,
          confirmation_text: "确认真实买入",
        }),
      });
      setOrder(result);
      onCompleted();
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 409) {
        setError(`${requestError.message}，已刷新预览，请再次确认。`);
        setPreview(null);
        await requestPreview();
      } else {
        setError(requestError instanceof Error ? requestError.message : "真实买入未完成");
      }
    } finally {
      setExecuting(false);
    }
  };

  const averageFill = order && numeric(order.filled_size) > 0
    ? numeric(order.filled_usdc) / numeric(order.filled_size)
    : null;

  return (
    <ModalShell
      title="跟随巨鲸买入"
      eyebrow="WHALE FOLLOW"
      onClose={onClose}
      className="whaleFollowModal"
      footer={
        order ? (
          <button className="pcButton primary" type="button" onClick={onClose}>完成</button>
        ) : (
          <>
            <button className="pcButton ghost" type="button" onClick={onClose}>取消</button>
            <button
              className="pcButton primary"
              type="button"
              onClick={execute}
              disabled={!preview || previewing || executing}
            >
              {executing
                ? "提交中"
                : preview
                  ? `确认买入 ${formatUsdc(preview.amount_usdc)}`
                  : "等待收益预览"}
            </button>
          </>
        )
      }
    >
      <div className="whaleFollowMarket">
        <span className={`whaleOutcomeMark ${side.outcome_index === 0 ? "positive" : "negative"}`}>{side.outcome}</span>
        <div>
          <strong>{market.title}</strong>
          <span>巨鲸均价 {formatPrice(entry.avg_buy_price)} · 当前价 {formatPrice(side.current_price)}</span>
        </div>
      </div>

      {order ? (
        <div className="whaleExecutionResult">
          <span className={`pcBadge ${numeric(order.filled_size) > 0 ? "success" : "warning"}`}>{order.status}</span>
          <h3>{numeric(order.filled_size) > 0 ? "买入成交已记录" : "订单已提交"}</h3>
          <dl>
            <div><dt>成交份额</dt><dd>{numeric(order.filled_size).toFixed(4)}</dd></div>
            <div><dt>成交均价</dt><dd>{formatPrice(averageFill)}</dd></div>
            <div><dt>成交金额</dt><dd>{formatUsdc(order.filled_usdc)}</dd></div>
            <div><dt>手续费</dt><dd>{formatUsdc(order.fee_usdc)}</dd></div>
          </dl>
          {order.reason && <p>{order.reason}</p>}
          <Link href="/whales/records" className="pcTextLink">查看我的跟单 →</Link>
        </div>
      ) : (
        <>
          <label className="whaleAmountInput">
            <span>跟单金额 <small>单笔上限 {formatUsdc(maxAmount)}</small></span>
            <div>
              <input
                aria-label="跟单金额"
                type="number"
                min="0"
                max={maxAmount}
                step="1"
                value={amount}
                onChange={(event) => changeAmount(event.target.value)}
              />
              <b>USDC</b>
            </div>
          </label>
          <div className="whaleQuickAmounts" aria-label="快捷金额">
            {[20, 50, 100].filter((value) => value <= maxAmount).map((value) => (
              <button type="button" key={value} onClick={() => changeAmount(String(value))}>{value} USDC</button>
            ))}
          </div>

          {previewing && <div className="whalePreviewLoading">正在读取实时盘口并计算收益…</div>}
          {error && <div className="pcFormError" role="alert">{error}</div>}
          {preview && !previewing && (
            <div className="whaleFollowPreview">
              <dl className="whalePreviewGrid whaleQuoteGrid">
                <div><dt>预计份额</dt><dd>{numeric(preview.estimated_shares).toFixed(4)}</dd></div>
                <div><dt>成交价上限</dt><dd>{formatPrice(preview.worst_price)}</dd></div>
                <div><dt>预估手续费</dt><dd>{formatUsdc(preview.estimated_fee_usdc)}</dd></div>
                <div><dt>总成本</dt><dd>{formatUsdc(preview.total_cost_usdc)}</dd></div>
                <div><dt>可用余额</dt><dd>{formatUsdc(preview.available_balance_usdc)}</dd></div>
              </dl>

              <div className="whaleProfitScenarios">
                <section className="whaleProfitScenario winning">
                  <span>押中并结算为 1</span>
                  <strong className="profit">净赚 {formatUsdc(preview.winning_profit_usdc)}</strong>
                  <small>{formatPercent(preview.profit_ratio_percent)} 收益率</small>
                  <dl>
                    <div><dt>预计回款</dt><dd>{formatUsdc(preview.winning_payout_usdc)}</dd></div>
                    <div><dt>最大亏损</dt><dd className="loss">{formatUsdc(preview.max_loss_usdc)}</dd></div>
                  </dl>
                </section>
                <section className="whaleProfitScenario immediate">
                  <span>按当前盘口立即卖出</span>
                  {preview.immediate_exit_pnl_usdc === null ? (
                    <>
                      <strong>暂时无法估算</strong>
                      <small>{preview.immediate_exit_unavailable_reason || "市场当前没有有效买盘"}</small>
                    </>
                  ) : (
                    <>
                      <strong className={numeric(preview.immediate_exit_pnl_usdc) >= 0 ? "profit" : "loss"}>
                        {formatSigned(preview.immediate_exit_pnl_usdc, " USDC")}
                      </strong>
                      <small>{formatPercent(preview.immediate_exit_pnl_percent)} 浮动盈亏</small>
                      <dl>
                        <div><dt>预计回收</dt><dd>{formatUsdc(preview.immediate_exit_proceeds_usdc)}</dd></div>
                        <div><dt>卖出价</dt><dd>{formatPrice(preview.immediate_exit_price)}</dd></div>
                      </dl>
                    </>
                  )}
                </section>
              </div>

              {preview.price_delta_warning && (
                <div className="whalePreviewWarning">
                  当前价已比巨鲸买入价高 {formatSigned(preview.price_delta_cents, " 美分")}，跟进成本明显上升。
                </div>
              )}
              {preview.reserve_warning && (
                <div className="whaleReserveWarning">本次买入会使执行钱包余额低于现金保留额。</div>
              )}
              <p className="whaleEstimateDisclaimer">以上包含预估手续费，实际成交和收益以最终订单及市场结算为准。</p>
            </div>
          )}
        </>
      )}
    </ModalShell>
  );
}
