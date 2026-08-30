"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { PolyCopyShell } from "./PolyCopyShell";
import {
  WhaleExclusion,
  WhaleExclusionList,
  WhaleMarketCategory,
  WhaleSettings,
  formatBeijing,
  formatCompactUsdc,
  numeric,
  whaleApi,
} from "./WhaleShared";

const AUTO_CATEGORIES: Array<{ key: WhaleMarketCategory; label: string }> = [
  { key: "sports", label: "传统体育" },
  { key: "esports", label: "电竞" },
  { key: "politics", label: "政治" },
  { key: "crypto", label: "加密" },
  { key: "science_tech", label: "科学与科技" },
  { key: "entertainment", label: "娱乐" },
  { key: "other", label: "其他" },
];

function shortWallet(address: string): string {
  return `${address.slice(0, 6)}…${address.slice(-4)}`;
}

function WhaleExclusionManager({ onReload }: { onReload: () => Promise<void> }) {
  const [exclusions, setExclusions] = useState<WhaleExclusion[]>([]);
  const [address, setAddress] = useState("");
  const [label, setLabel] = useState("");
  const [loading, setLoading] = useState(true);
  const [busyWallet, setBusyWallet] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await whaleApi<WhaleExclusionList>("/api/whales/exclusions");
      setExclusions(result.items);
      setError(null);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "无法读取账户排除名单");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function addExclusion(event: FormEvent) {
    event.preventDefault();
    if (!address.trim()) {
      setError("请输入钱包地址或 Polymarket 个人页链接。");
      return;
    }
    setBusyWallet("new");
    setError(null);
    setMessage(null);
    try {
      const created = await whaleApi<WhaleExclusion>("/api/whales/exclusions", {
        method: "POST",
        body: JSON.stringify({ address: address.trim(), label: label.trim() || null }),
      });
      setAddress("");
      setLabel("");
      setMessage(`${created.display_name} 已加入排除名单，后台扫描正在更新。`);
      await Promise.all([load(), onReload()]);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "加入排除名单失败");
    } finally {
      setBusyWallet(null);
    }
  }

  async function removeExclusion(item: WhaleExclusion) {
    setBusyWallet(item.proxy_wallet);
    setError(null);
    setMessage(null);
    try {
      await whaleApi<void>(`/api/whales/exclusions/${item.proxy_wallet}`, {
        method: "DELETE",
      });
      setMessage(`${item.display_name} 已移出排除名单，旧历史与统计已恢复。`);
      await Promise.all([load(), onReload()]);
    } catch (removeError) {
      setError(removeError instanceof Error ? removeError.message : "移出排除名单失败");
    } finally {
      setBusyWallet(null);
    }
  }

  return (
    <div className="whaleExclusionManager">
      <div className="whaleExclusionHeading">
        <div>
          <span className="pcEyebrow">ACCOUNT FILTER</span>
          <h3>账户排除名单</h3>
          <p>排除后不再监测、统计或允许跟单；底层历史和真实交易账本不会删除。</p>
        </div>
        <strong>{exclusions.length} 个账户</strong>
      </div>
      <form className="whaleExclusionForm" onSubmit={addExclusion}>
        <label className="pcField whaleExclusionAddress">
          <span>钱包地址或个人页</span>
          <input
            aria-label="排除账户地址或个人页"
            value={address}
            onChange={(event) => setAddress(event.target.value)}
            placeholder="0x… 或 https://polymarket.com/profile/0x…"
            disabled={busyWallet !== null}
          />
        </label>
        <label className="pcField">
          <span>显示名称（可选）</span>
          <input
            aria-label="排除账户显示名称"
            value={label}
            maxLength={200}
            onChange={(event) => setLabel(event.target.value)}
            placeholder="便于识别"
            disabled={busyWallet !== null}
          />
        </label>
        <button className="pcButton primary" type="submit" disabled={busyWallet !== null}>
          {busyWallet === "new" ? "加入中…" : "加入排除名单"}
        </button>
      </form>
      {error && <p className="pcFormError" role="alert">{error}</p>}
      {message && <p className="pcFormSuccess">{message}</p>}
      <div className="whaleExclusionList" aria-live="polite">
        {loading && exclusions.length === 0 ? (
          <p className="whaleExclusionEmpty">正在读取排除名单…</p>
        ) : exclusions.length === 0 ? (
          <p className="whaleExclusionEmpty">暂无排除账户。</p>
        ) : exclusions.map((item) => (
          <article key={item.proxy_wallet} className="whaleExclusionItem">
            <div>
              <a href={item.profile_url} target="_blank" rel="noreferrer">{item.display_name}</a>
              <code title={item.proxy_wallet}>{shortWallet(item.proxy_wallet)}</code>
            </div>
            <dl>
              <div><dt>隐藏信号</dt><dd>{item.hidden_entry_count}</dd></div>
              <div><dt>加入时间</dt><dd>{formatBeijing(item.created_at, true)}</dd></div>
            </dl>
            <button
              className="pcButton"
              type="button"
              disabled={busyWallet !== null}
              onClick={() => void removeExclusion(item)}
            >
              {busyWallet === item.proxy_wallet ? "移出中…" : "移出"}
            </button>
          </article>
        ))}
      </div>
    </div>
  );
}

export function WhaleSettingsPanel({
  settings,
  onSettingsChange,
  onReload,
  onClose,
}: {
  settings: WhaleSettings | null;
  onSettingsChange: (settings: WhaleSettings) => void;
  onReload: () => Promise<void>;
  onClose?: () => void;
}) {
  const [registrationDaysInput, setRegistrationDaysInput] = useState<string | null>(null);
  const [newAccountThresholdInput, setNewAccountThresholdInput] = useState<string | null>(null);
  const [largeAmountThresholdInput, setLargeAmountThresholdInput] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const registrationDays = registrationDaysInput ?? String(settings?.registration_window_days ?? 7);
  const newAccountThreshold = newAccountThresholdInput ?? (settings ? String(settings.new_account_threshold_usdc) : "");
  const largeAmountThreshold = largeAmountThresholdInput ?? (settings ? String(settings.large_amount_threshold_usdc) : "");

  async function submit(event: FormEvent) {
    event.preventDefault();
    const days = Number(registrationDays);
    const newAccountAmount = Number(newAccountThreshold);
    const largeAmount = Number(largeAmountThreshold);
    const minimum = numeric(settings?.collect_filter_amount_usdc ?? 1000);
    if (!Number.isInteger(days) || days < 1 || days > 30) {
      setError("注册窗口必须是 1–30 之间的整数天数。");
      return;
    }
    if (!Number.isFinite(newAccountAmount) || newAccountAmount < minimum) {
      setError(`新号大额门槛不能低于 ${formatCompactUsdc(minimum)}。`);
      return;
    }
    if (!Number.isFinite(largeAmount) || largeAmount < minimum) {
      setError(`全量超大额门槛不能低于 ${formatCompactUsdc(minimum)}。`);
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const next = await whaleApi<WhaleSettings>("/api/whales/settings", {
        method: "PUT",
        body: JSON.stringify({
          registration_window_days: days,
          new_account_threshold_usdc: newAccountAmount,
          large_amount_threshold_usdc: largeAmount,
        }),
      });
      onSettingsChange(next);
      setRegistrationDaysInput(null);
      setNewAccountThresholdInput(null);
      setLargeAmountThresholdInput(null);
      const scan = await whaleApi<{ status: string }>("/api/whales/scan", {
        method: "POST",
      });
      setMessage(
        scan.status === "ok"
          ? "巨鲸监测条件已保存，数据已重新扫描。"
          : "巨鲸监测条件已保存，扫描将在后台更新。",
      );
      await onReload();
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "保存巨鲸设置失败");
    } finally {
      setBusy(false);
    }
  }

  return (
      <section className="pcPanel whaleInlineSettingsPanel" aria-label="巨鲸监测设置">
        <header className="pcPanelHeader">
          <div>
            <span className="pcEyebrow">WHALE DISCOVERY</span>
            <h2>监测条件</h2>
            <p>成交窗口固定为 24 小时；新号和全量超大额规则共用一次扫描。</p>
          </div>
          {onClose && <button className="whaleSettingsClose" type="button" onClick={onClose}>收起设置</button>}
        </header>
        <form className="pcSettingsForm pcSystemSettingsForm" onSubmit={submit}>
          <div className="whaleInlineSettingsBody">
            <div className="pcFormGrid three">
              <label className="pcField">
                <span>注册窗口</span>
                <div className="pcUnitInput">
                  <input
                    aria-label="巨鲸注册窗口天数"
                    type="number"
                    min="1"
                    max="30"
                    step="1"
                    value={registrationDays}
                    onChange={(event) => setRegistrationDaysInput(event.target.value)}
                    disabled={!settings || busy}
                  />
                  <b>天</b>
                </div>
                <small>新号规则首次触发时，账号创建时间不得超过该天数。</small>
              </label>
              <label className="pcField">
                  <span>新号近 24 小时门槛</span>
                <div className="pcUnitInput">
                  <input
                    aria-label="新号大额买入门槛"
                    type="number"
                    min={numeric(settings?.collect_filter_amount_usdc ?? 1000)}
                    step="1000"
                    value={newAccountThreshold}
                    onChange={(event) => setNewAccountThresholdInput(event.target.value)}
                    disabled={!settings || busy}
                  />
                  <b>USDC</b>
                </div>
                <small>仅账号年龄不超过注册窗口的钱包参与该规则。</small>
              </label>
              <label className="pcField">
                <span>全量近 24 小时门槛</span>
                <div className="pcUnitInput">
                  <input
                    aria-label="全量超大额买入门槛"
                    type="number"
                    min={numeric(settings?.collect_filter_amount_usdc ?? 1000)}
                    step="1000"
                    value={largeAmountThreshold}
                    onChange={(event) => setLargeAmountThresholdInput(event.target.value)}
                    disabled={!settings || busy}
                  />
                  <b>USDC</b>
                </div>
                <small>不限制账号年龄；同一市场方向的买入按 24 小时累计。</small>
              </label>
            </div>
            <div className="whaleSettingsStatus" aria-label="扫描状态">
              <div><span>最后扫描</span><strong>{formatBeijing(settings?.last_scan_at, true)}</strong></div>
              <div><span>新号 当前 / 历史</span><strong>{settings?.new_account_active_count ?? 0} / {settings?.new_account_history_count ?? 0}</strong></div>
              <div><span>全量 当前 / 历史</span><strong>{settings?.large_amount_active_count ?? 0} / {settings?.large_amount_history_count ?? 0}</strong></div>
              <div><span>成交记录 / 失败</span><strong>{settings?.tracked_trade_count ?? 0} / {settings?.consecutive_failures ?? 0}</strong></div>
            </div>
            <p className="pcFormHint">成交采集下限仍为 {formatCompactUsdc(settings?.collect_filter_amount_usdc ?? 1000)}，更小的拆分成交可能不会计入累计金额。</p>
          </div>
          {error && <p className="pcFormError" role="alert">{error}</p>}
          {message && <p className="pcFormSuccess">{message}</p>}
          {settings?.last_scan_error && <p className="pcFormError" role="alert">{settings.last_scan_error}</p>}
          <div className="pcSettingsActions">
            <button className="pcButton primary" type="submit" disabled={!settings || busy}>
              {busy ? "保存并扫描中…" : "保存监测条件"}
            </button>
          </div>
        </form>
        <WhaleExclusionManager onReload={onReload} />
      </section>
  );
}

function AutoStrategyCard({
  title,
  enabled,
  amount,
  minPrice,
  maxPrice,
  lowPriceEnabled,
  lowPriceMax,
  lowPriceAmount,
  categories,
  busy,
  onEnabled,
  onAmount,
  onMinPrice,
  onMaxPrice,
  onLowPriceEnabled,
  onLowPriceMax,
  onLowPriceAmount,
  onCategories,
}: {
  title: string;
  enabled: boolean;
  amount: string;
  minPrice: string;
  maxPrice: string;
  lowPriceEnabled: boolean;
  lowPriceMax: string;
  lowPriceAmount: string;
  categories: WhaleMarketCategory[];
  busy: boolean;
  onEnabled: (value: boolean) => void;
  onAmount: (value: string) => void;
  onMinPrice: (value: string) => void;
  onMaxPrice: (value: string) => void;
  onLowPriceEnabled: (value: boolean) => void;
  onLowPriceMax: (value: string) => void;
  onLowPriceAmount: (value: string) => void;
  onCategories: (value: WhaleMarketCategory[]) => void;
}) {
  const toggleCategory = (category: WhaleMarketCategory) => {
    onCategories(
      categories.includes(category)
        ? categories.filter((item) => item !== category)
        : [...categories, category],
    );
  };
  return (
    <section className={`whaleAutoStrategyCard ${enabled ? "enabled" : ""}`}>
      <header>
        <div><span>REAL AUTO FOLLOW</span><h3>{title}</h3></div>
        <label className="whaleAutoToggle">
          <input
            type="checkbox"
            aria-label={`开启${title}`}
            checked={enabled}
            disabled={busy}
            onChange={(event) => onEnabled(event.target.checked)}
          />
          <b>{enabled ? "已开启" : "已关闭"}</b>
        </label>
      </header>
      <div className="whaleAutoFields">
        <label className="pcField"><span>单笔金额</span><div className="pcUnitInput"><input aria-label={`${title}单笔金额`} type="number" min="0.01" step="0.01" value={amount} disabled={busy} onChange={(event) => onAmount(event.target.value)} /><b>USDC</b></div></label>
        <label className="pcField"><span>实际最低买价</span><input aria-label={`${title}最低买价`} type="number" min="0.01" max="0.99" step="0.01" value={minPrice} disabled={busy} onChange={(event) => onMinPrice(event.target.value)} /></label>
        <label className="pcField"><span>实际最高买价</span><input aria-label={`${title}最高买价`} type="number" min="0.01" max="0.99" step="0.01" value={maxPrice} disabled={busy} onChange={(event) => onMaxPrice(event.target.value)} /></label>
      </div>
      <div className="whaleAutoLowPriceSection">
        <label className="whaleAutoToggle">
          <input
            type="checkbox"
            aria-label={`${title}启用低价小额`}
            checked={lowPriceEnabled}
            disabled={busy}
            onChange={(event) => onLowPriceEnabled(event.target.checked)}
          />
          <b>低价小额</b>
        </label>
        {lowPriceEnabled && (
          <div className="whaleAutoFields">
            <label className="pcField"><span>低于此价格</span><input aria-label={`${title}低价分界`} type="number" min="0.01" max="0.99" step="0.01" value={lowPriceMax} disabled={busy} onChange={(event) => onLowPriceMax(event.target.value)} /></label>
            <label className="pcField"><span>低价单笔金额</span><div className="pcUnitInput"><input aria-label={`${title}低价金额`} type="number" min="0.01" step="0.01" value={lowPriceAmount} disabled={busy} onChange={(event) => onLowPriceAmount(event.target.value)} /><b>USDC</b></div></label>
          </div>
        )}
      </div>
      <fieldset className="whaleAutoCategories">
        <legend>允许的市场分类</legend>
        {AUTO_CATEGORIES.map((item) => (
          <label key={item.key}>
            <input type="checkbox" checked={categories.includes(item.key)} disabled={busy} onChange={() => toggleCategory(item.key)} />
            <span>{item.label}</span>
          </label>
        ))}
      </fieldset>
    </section>
  );
}

export function WhaleAutoSettingsPanel({
  settings,
  onSettingsChange,
  onReload,
}: {
  settings: WhaleSettings | null;
  onSettingsChange: (settings: WhaleSettings) => void;
  onReload: () => Promise<void>;
}) {
  const [newAutoEnabled, setNewAutoEnabled] = useState<boolean | null>(null);
  const [newAutoAmount, setNewAutoAmount] = useState<string | null>(null);
  const [newAutoMinPrice, setNewAutoMinPrice] = useState<string | null>(null);
  const [newAutoMaxPrice, setNewAutoMaxPrice] = useState<string | null>(null);
  const [newAutoCategories, setNewAutoCategories] = useState<WhaleMarketCategory[] | null>(null);
  const [newLowPriceEnabled, setNewLowPriceEnabled] = useState<boolean | null>(null);
  const [newLowPriceMax, setNewLowPriceMax] = useState<string | null>(null);
  const [newLowPriceAmount, setNewLowPriceAmount] = useState<string | null>(null);
  const [largeAutoEnabled, setLargeAutoEnabled] = useState<boolean | null>(null);
  const [largeAutoAmount, setLargeAutoAmount] = useState<string | null>(null);
  const [largeAutoMinPrice, setLargeAutoMinPrice] = useState<string | null>(null);
  const [largeAutoMaxPrice, setLargeAutoMaxPrice] = useState<string | null>(null);
  const [largeAutoCategories, setLargeAutoCategories] = useState<WhaleMarketCategory[] | null>(null);
  const [largeLowPriceEnabled, setLargeLowPriceEnabled] = useState<boolean | null>(null);
  const [largeLowPriceMax, setLargeLowPriceMax] = useState<string | null>(null);
  const [largeLowPriceAmount, setLargeLowPriceAmount] = useState<string | null>(null);
  const [marketCapEnabled, setMarketCapEnabled] = useState<boolean | null>(null);
  const [marketMaxPurchaseCount, setMarketMaxPurchaseCount] = useState<string | null>(null);
  const [marketMaxAmount, setMarketMaxAmount] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [riskVisible, setRiskVisible] = useState(true);
  const [riskNoticeVersion, setRiskNoticeVersion] = useState(0);
  const [editing, setEditing] = useState(false);
  const [activeStrategy, setActiveStrategy] = useState<"new_account" | "large_amount">("new_account");

  const resolvedNewAutoEnabled = newAutoEnabled ?? settings?.new_account_auto_follow_enabled ?? false;
  const resolvedNewAutoAmount = newAutoAmount ?? String(settings?.new_account_auto_follow_amount_usdc ?? 5);
  const resolvedNewAutoMinPrice = newAutoMinPrice ?? String(settings?.new_account_auto_follow_min_price ?? 0.65);
  const resolvedNewAutoMaxPrice = newAutoMaxPrice ?? String(settings?.new_account_auto_follow_max_price ?? 0.8);
  const resolvedNewAutoCategories = newAutoCategories ?? settings?.new_account_auto_follow_categories ?? ["sports"];
  const resolvedNewLowPriceEnabled = newLowPriceEnabled ?? settings?.new_account_auto_follow_low_price_max_price != null;
  const resolvedNewLowPriceMax = newLowPriceMax ?? (settings?.new_account_auto_follow_low_price_max_price == null ? "" : String(settings.new_account_auto_follow_low_price_max_price));
  const resolvedNewLowPriceAmount = newLowPriceAmount ?? (settings?.new_account_auto_follow_low_price_amount_usdc == null ? "" : String(settings.new_account_auto_follow_low_price_amount_usdc));
  const resolvedLargeAutoEnabled = largeAutoEnabled ?? settings?.large_amount_auto_follow_enabled ?? false;
  const resolvedLargeAutoAmount = largeAutoAmount ?? String(settings?.large_amount_auto_follow_amount_usdc ?? 10);
  const resolvedLargeAutoMinPrice = largeAutoMinPrice ?? String(settings?.large_amount_auto_follow_min_price ?? 0.6);
  const resolvedLargeAutoMaxPrice = largeAutoMaxPrice ?? String(settings?.large_amount_auto_follow_max_price ?? 0.8);
  const resolvedLargeAutoCategories = largeAutoCategories ?? settings?.large_amount_auto_follow_categories ?? ["sports"];
  const resolvedLargeLowPriceEnabled = largeLowPriceEnabled ?? settings?.large_amount_auto_follow_low_price_max_price != null;
  const resolvedLargeLowPriceMax = largeLowPriceMax ?? (settings?.large_amount_auto_follow_low_price_max_price == null ? "" : String(settings.large_amount_auto_follow_low_price_max_price));
  const resolvedLargeLowPriceAmount = largeLowPriceAmount ?? (settings?.large_amount_auto_follow_low_price_amount_usdc == null ? "" : String(settings.large_amount_auto_follow_low_price_amount_usdc));
  const resolvedMarketCapEnabled = marketCapEnabled ?? settings?.auto_follow_market_max_purchase_count != null;
  const resolvedMarketMaxPurchaseCount = marketMaxPurchaseCount ?? (settings?.auto_follow_market_max_purchase_count == null ? "" : String(settings.auto_follow_market_max_purchase_count));
  const resolvedMarketMaxAmount = marketMaxAmount ?? (settings?.auto_follow_market_max_amount_usdc == null ? "" : String(settings.auto_follow_market_max_amount_usdc));

  useEffect(() => {
    const timer = window.setTimeout(() => setRiskVisible(false), 8000);
    return () => window.clearTimeout(timer);
  }, [riskNoticeVersion]);

  const updateEnabled = (setter: (value: boolean) => void, value: boolean) => {
    setter(value);
    if (value) {
      setRiskVisible(true);
      setRiskNoticeVersion((current) => current + 1);
    }
  };

  const resetDraft = () => {
    setNewAutoEnabled(null);
    setNewAutoAmount(null);
    setNewAutoMinPrice(null);
    setNewAutoMaxPrice(null);
    setNewAutoCategories(null);
    setNewLowPriceEnabled(null);
    setNewLowPriceMax(null);
    setNewLowPriceAmount(null);
    setLargeAutoEnabled(null);
    setLargeAutoAmount(null);
    setLargeAutoMinPrice(null);
    setLargeAutoMaxPrice(null);
    setLargeAutoCategories(null);
    setLargeLowPriceEnabled(null);
    setLargeLowPriceMax(null);
    setLargeLowPriceAmount(null);
    setMarketCapEnabled(null);
    setMarketMaxPurchaseCount(null);
    setMarketMaxAmount(null);
  };

  const categorySummary = (categories: WhaleMarketCategory[]) => categories
    .map((category) => AUTO_CATEGORIES.find((item) => item.key === category)?.label || category)
    .join("、");

  async function submit(event: FormEvent) {
    event.preventDefault();
    const newAutoAmountValue = Number(resolvedNewAutoAmount);
    const newAutoMinValue = Number(resolvedNewAutoMinPrice);
    const newAutoMaxValue = Number(resolvedNewAutoMaxPrice);
    const newLowMaxValue = Number(resolvedNewLowPriceMax);
    const newLowAmountValue = Number(resolvedNewLowPriceAmount);
    const largeAutoAmountValue = Number(resolvedLargeAutoAmount);
    const largeAutoMinValue = Number(resolvedLargeAutoMinPrice);
    const largeAutoMaxValue = Number(resolvedLargeAutoMaxPrice);
    const largeLowMaxValue = Number(resolvedLargeLowPriceMax);
    const largeLowAmountValue = Number(resolvedLargeLowPriceAmount);
    const marketMaxPurchaseCountValue = Number(resolvedMarketMaxPurchaseCount);
    const marketMaxAmountValue = Number(resolvedMarketMaxAmount);
    for (const [label, amount, minPrice, maxPrice, categories, lowEnabled, lowMax, lowAmount] of [
      ["新号大额", newAutoAmountValue, newAutoMinValue, newAutoMaxValue, resolvedNewAutoCategories, resolvedNewLowPriceEnabled, newLowMaxValue, newLowAmountValue],
      ["全量超大额", largeAutoAmountValue, largeAutoMinValue, largeAutoMaxValue, resolvedLargeAutoCategories, resolvedLargeLowPriceEnabled, largeLowMaxValue, largeLowAmountValue],
    ] as const) {
      if (!Number.isFinite(amount) || amount <= 0 || amount > numeric(settings?.max_follow_amount_usdc ?? 200)) {
        setError(`${label}自动跟单金额必须大于 0，且不能超过单笔买入上限。`);
        return;
      }
      if (!Number.isFinite(minPrice) || !Number.isFinite(maxPrice) || minPrice <= 0 || maxPrice >= 1 || minPrice > maxPrice) {
        setError(`${label}实际买价必须满足 0 < 最低价 ≤ 最高价 < 1。`);
        return;
      }
      if (!categories.length) {
        setError(`${label}自动跟单至少需要选择一个市场分类。`);
        return;
      }
      if (lowEnabled && (!Number.isFinite(lowMax) || lowMax <= minPrice || lowMax >= maxPrice)) {
        setError(`${label}低价分界必须严格位于实际买价区间内。`);
        return;
      }
      if (lowEnabled && (!Number.isFinite(lowAmount) || lowAmount <= 0 || lowAmount >= amount)) {
        setError(`${label}低价金额必须大于 0 且小于基础单笔金额。`);
        return;
      }
    }
    if (resolvedMarketCapEnabled) {
      if (!Number.isInteger(marketMaxPurchaseCountValue) || marketMaxPurchaseCountValue <= 0) {
        setError("单市场最大购买次数必须是正整数。");
        return;
      }
      if (!Number.isFinite(marketMaxAmountValue) || marketMaxAmountValue <= 0) {
        setError("单市场累计金额必须大于 0。");
        return;
      }
      const enabledBaseAmounts = [
        resolvedNewAutoEnabled ? newAutoAmountValue : 0,
        resolvedLargeAutoEnabled ? largeAutoAmountValue : 0,
      ];
      if (marketMaxAmountValue < Math.max(...enabledBaseAmounts)) {
        setError("单市场累计金额不能低于已开启策略的基础单笔金额。");
        return;
      }
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const next = await whaleApi<WhaleSettings>("/api/whales/settings", {
        method: "PUT",
        body: JSON.stringify({
          new_account_auto_follow_enabled: resolvedNewAutoEnabled,
          new_account_auto_follow_amount_usdc: newAutoAmountValue,
          new_account_auto_follow_min_price: newAutoMinValue,
          new_account_auto_follow_max_price: newAutoMaxValue,
          new_account_auto_follow_categories: resolvedNewAutoCategories,
          new_account_auto_follow_low_price_max_price: resolvedNewLowPriceEnabled ? newLowMaxValue : null,
          new_account_auto_follow_low_price_amount_usdc: resolvedNewLowPriceEnabled ? newLowAmountValue : null,
          large_amount_auto_follow_enabled: resolvedLargeAutoEnabled,
          large_amount_auto_follow_amount_usdc: largeAutoAmountValue,
          large_amount_auto_follow_min_price: largeAutoMinValue,
          large_amount_auto_follow_max_price: largeAutoMaxValue,
          large_amount_auto_follow_categories: resolvedLargeAutoCategories,
          large_amount_auto_follow_low_price_max_price: resolvedLargeLowPriceEnabled ? largeLowMaxValue : null,
          large_amount_auto_follow_low_price_amount_usdc: resolvedLargeLowPriceEnabled ? largeLowAmountValue : null,
          auto_follow_market_max_purchase_count: resolvedMarketCapEnabled ? marketMaxPurchaseCountValue : null,
          auto_follow_market_max_amount_usdc: resolvedMarketCapEnabled ? marketMaxAmountValue : null,
        }),
      });
      onSettingsChange(next);
      resetDraft();
      const scan = await whaleApi<{ status: string }>("/api/whales/scan", { method: "POST" });
      setMessage(scan.status === "ok" ? "自动跟单策略已保存，数据已重新扫描。" : "自动跟单策略已保存，扫描将在后台更新。");
      setEditing(false);
      await onReload();
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "保存自动跟单策略失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="pcPanel whaleInlineSettingsPanel" aria-label="巨鲸自动跟单设置">
      <header className="pcPanelHeader whaleAutoCompactHeader">
        <div>
          <span className="pcEyebrow">REAL AUTO FOLLOW</span>
          <h2>自动跟单策略</h2>
          <p>默认显示当前配置；需要调整时再展开编辑。</p>
        </div>
        <button
          className="pcButton ghost"
          type="button"
          aria-expanded={editing}
          aria-controls="whale-auto-strategy-editor"
          onClick={() => {
            setError(null);
            setEditing((current) => !current);
          }}
        >
          {editing ? "收起设置" : "编辑策略"}
        </button>
      </header>
      <form className="pcSettingsForm pcSystemSettingsForm" onSubmit={submit}>
        <div className="whaleAutoOverviewGrid" aria-label="自动跟单策略概览">
          <article className="whaleAutoOverviewCard">
            <header><strong>新号大额</strong><span className={resolvedNewAutoEnabled ? "enabled" : "disabled"}>{resolvedNewAutoEnabled ? "已开启" : "已关闭"}</span></header>
            <dl><div><dt>单笔</dt><dd>{resolvedNewAutoAmount} USDC</dd></div><div><dt>买价</dt><dd>{resolvedNewAutoMinPrice}–{resolvedNewAutoMaxPrice}</dd></div><div><dt>低价</dt><dd>{resolvedNewLowPriceEnabled ? `< ${resolvedNewLowPriceMax}：${resolvedNewLowPriceAmount} USDC` : "未启用"}</dd></div><div><dt>分类</dt><dd title={categorySummary(resolvedNewAutoCategories)}>{categorySummary(resolvedNewAutoCategories)}</dd></div></dl>
          </article>
          <article className="whaleAutoOverviewCard">
            <header><strong>全量超大额</strong><span className={resolvedLargeAutoEnabled ? "enabled" : "disabled"}>{resolvedLargeAutoEnabled ? "已开启" : "已关闭"}</span></header>
            <dl><div><dt>单笔</dt><dd>{resolvedLargeAutoAmount} USDC</dd></div><div><dt>买价</dt><dd>{resolvedLargeAutoMinPrice}–{resolvedLargeAutoMaxPrice}</dd></div><div><dt>低价</dt><dd>{resolvedLargeLowPriceEnabled ? `< ${resolvedLargeLowPriceMax}：${resolvedLargeLowPriceAmount} USDC` : "未启用"}</dd></div><div><dt>分类</dt><dd title={categorySummary(resolvedLargeAutoCategories)}>{categorySummary(resolvedLargeAutoCategories)}</dd></div></dl>
          </article>
        </div>
        <p className="pcFormHint whaleAutoMarketCapSummary">
          单市场共享上限：{resolvedMarketCapEnabled ? `最多 ${resolvedMarketMaxPurchaseCount} 次 / 累计 ${resolvedMarketMaxAmount} USDC` : "暂不限制"}
        </p>

        {editing && (
          <div className="whaleAutoEditor" id="whale-auto-strategy-editor">
            <div className="whaleAutoEditorTabs" role="tablist" aria-label="选择要编辑的自动跟单策略">
              <button type="button" role="tab" aria-selected={activeStrategy === "new_account"} onClick={() => setActiveStrategy("new_account")}>新号大额</button>
              <button type="button" role="tab" aria-selected={activeStrategy === "large_amount"} onClick={() => setActiveStrategy("large_amount")}>全量超大额</button>
            </div>
            {activeStrategy === "new_account" ? (
              <AutoStrategyCard
                title="新号大额自动跟单"
                enabled={resolvedNewAutoEnabled}
                amount={resolvedNewAutoAmount}
                minPrice={resolvedNewAutoMinPrice}
                maxPrice={resolvedNewAutoMaxPrice}
                lowPriceEnabled={resolvedNewLowPriceEnabled}
                lowPriceMax={resolvedNewLowPriceMax}
                lowPriceAmount={resolvedNewLowPriceAmount}
                categories={resolvedNewAutoCategories}
                busy={!settings || busy}
                onEnabled={(value) => updateEnabled(setNewAutoEnabled, value)}
                onAmount={setNewAutoAmount}
                onMinPrice={setNewAutoMinPrice}
                onMaxPrice={setNewAutoMaxPrice}
                onLowPriceEnabled={setNewLowPriceEnabled}
                onLowPriceMax={setNewLowPriceMax}
                onLowPriceAmount={setNewLowPriceAmount}
                onCategories={setNewAutoCategories}
              />
            ) : (
              <AutoStrategyCard
                title="全量超大额自动跟单"
                enabled={resolvedLargeAutoEnabled}
                amount={resolvedLargeAutoAmount}
                minPrice={resolvedLargeAutoMinPrice}
                maxPrice={resolvedLargeAutoMaxPrice}
                lowPriceEnabled={resolvedLargeLowPriceEnabled}
                lowPriceMax={resolvedLargeLowPriceMax}
                lowPriceAmount={resolvedLargeLowPriceAmount}
                categories={resolvedLargeAutoCategories}
                busy={!settings || busy}
                onEnabled={(value) => updateEnabled(setLargeAutoEnabled, value)}
                onAmount={setLargeAutoAmount}
                onMinPrice={setLargeAutoMinPrice}
                onMaxPrice={setLargeAutoMaxPrice}
                onLowPriceEnabled={setLargeLowPriceEnabled}
                onLowPriceMax={setLargeLowPriceMax}
                onLowPriceAmount={setLargeLowPriceAmount}
                onCategories={setLargeAutoCategories}
              />
            )}
            <section className="whaleAutoStrategyCard whaleAutoMarketCapCard">
              <header>
                <div><span>SHARED MARKET LIMIT</span><h3>单市场共享上限</h3></div>
                <label className="whaleAutoToggle">
                  <input type="checkbox" aria-label="启用单市场共享上限" checked={resolvedMarketCapEnabled} disabled={!settings || busy} onChange={(event) => setMarketCapEnabled(event.target.checked)} />
                  <b>{resolvedMarketCapEnabled ? "已开启" : "暂不限制"}</b>
                </label>
              </header>
              {resolvedMarketCapEnabled && (
                <div className="whaleAutoFields">
                  <label className="pcField"><span>最大购买次数</span><input aria-label="单市场最大购买次数" type="number" min="1" step="1" value={resolvedMarketMaxPurchaseCount} disabled={busy} onChange={(event) => setMarketMaxPurchaseCount(event.target.value)} /></label>
                  <label className="pcField"><span>累计投入上限</span><div className="pcUnitInput"><input aria-label="单市场累计投入上限" type="number" min="0.01" step="0.01" value={resolvedMarketMaxAmount} disabled={busy} onChange={(event) => setMarketMaxAmount(event.target.value)} /><b>USDC</b></div></label>
                </div>
              )}
              <p className="pcFormHint">两类规则和所有触发钱包按同一 condition_id 共享；卖出后额度不重置。</p>
            </section>
            {riskVisible && (
              <div className="pcFormHint whaleAutoRiskHint" role="status">
                <span>真实资金功能：同一钱包同一资产只判断一次；不同钱包同方向可继续触发，并受上方单市场共享上限约束。分歧市场禁止买入，持仓后出现反向大额信号会卖出原方向全部份额，包括混合人工份额。</span>
                <button type="button" aria-label="关闭真实资金风险提示" onClick={() => setRiskVisible(false)}>知道了</button>
              </div>
            )}
            {error && <p className="pcFormError" role="alert">{error}</p>}
            <div className="pcSettingsActions whaleAutoEditorActions">
              <button className="pcButton ghost" type="button" disabled={busy} onClick={() => { resetDraft(); setError(null); setEditing(false); }}>取消</button>
              <button className="pcButton primary" type="submit" disabled={!settings || busy}>{busy ? "保存并扫描中…" : "保存自动跟单策略"}</button>
            </div>
          </div>
        )}
        {message && <p className="pcFormSuccess">{message}</p>}
      </form>
    </section>
  );
}

export default function WhaleSettingsWorkspace() {
  const [settings, setSettings] = useState<WhaleSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      setSettings(await whaleApi<WhaleSettings>("/api/whales/settings"));
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "无法读取巨鲸设置");
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  return (
    <PolyCopyShell
      active="whales"
      title="巨鲸监测"
      subtitle="监测条件已经合并到巨鲸页面"
    >
      {loadError && <div className="pcAlert danger" role="alert">{loadError}</div>}
      <WhaleSettingsPanel settings={settings} onSettingsChange={setSettings} onReload={load} />
    </PolyCopyShell>
  );
}
