"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { PolyCopyShell } from "./PolyCopyShell";
import {
  WhaleExclusion,
  WhaleExclusionList,
  WhaleSettings,
  formatBeijing,
  formatCompactUsdc,
  numeric,
  whaleApi,
} from "./WhaleShared";

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
