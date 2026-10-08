"use client";

import { useCallback, useEffect, useState } from "react";

const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8730"
).replace(/\/$/, "");

type EmailSummarySettings = {
  notifications_enabled: boolean;
  weekly_summary_enabled: boolean;
  weekly_summary_last_sent_at: string | null;
  weekly_summary_next_run_at: string | null;
};

async function emailApi<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.detail || `请求失败（${response.status}）`);
  return payload as T;
}

function formatTime(value: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
}

export default function WeeklyEmailSummarySettingsPanel() {
  const [settings, setSettings] = useState<EmailSummarySettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setSettings(await emailApi<EmailSummarySettings>("/api/email-settings"));
      setError("");
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "无法读取每周汇总设置");
    }
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(initial);
  }, [load]);

  async function setEnabled(enabled: boolean) {
    setSaving(true);
    setMessage("");
    setError("");
    try {
      const next = await emailApi<EmailSummarySettings>("/api/email-settings", {
        method: "PUT",
        body: JSON.stringify({ weekly_summary_enabled: enabled }),
      });
      setSettings(next);
      setMessage(enabled ? "每周命中率汇总已启用，将从下一个周一开始发送。" : "每周命中率汇总已关闭。");
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "保存每周汇总设置失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="pcPanel weeklyEmailSummaryPanel" aria-label="每周邮件命中率汇总设置">
      <div className="weeklyEmailSummaryCopy">
        <h2>每周命中率汇总</h2>
        <p>每周一 00:00（北京时间）汇总上一周新结算的已发邮件信号，同一信号不会因多个收件人重复计算。</p>
        {settings?.weekly_summary_enabled && !settings.notifications_enabled && (
          <small className="weeklyEmailSummaryPaused">系统邮件通知当前关闭，周报设置已保留但发送暂停。</small>
        )}
        {settings?.weekly_summary_last_sent_at && <small>上次发送：{formatTime(settings.weekly_summary_last_sent_at)}</small>}
        {settings?.weekly_summary_enabled && settings.weekly_summary_next_run_at && <small>下次计划：{formatTime(settings.weekly_summary_next_run_at)}</small>}
      </div>
      <label className="weeklyEmailSummaryToggle">
        <span>{settings?.weekly_summary_enabled ? "已启用" : "未启用"}</span>
        <input
          aria-label="启用每周命中率汇总"
          type="checkbox"
          checked={settings?.weekly_summary_enabled ?? false}
          disabled={!settings || saving}
          onChange={(event) => void setEnabled(event.target.checked)}
        />
      </label>
      {error && <p className="pcFormError weeklyEmailSummaryMessage" role="alert">{error}</p>}
      {message && <p className="pcFormSuccess weeklyEmailSummaryMessage">{message}</p>}
    </section>
  );
}
