"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { PolyCopyShell } from "./PolyCopyShell";
import {
  WhaleSettings,
  formatBeijing,
  formatCompactUsdc,
  numeric,
  whaleApi,
} from "./WhaleShared";

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
