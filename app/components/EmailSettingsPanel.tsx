"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8730"
).replace(/\/$/, "");

type EmailSettings = {
  notifications_enabled: boolean;
  notification_recipients: string[];
  smtp_host: string | null;
  smtp_port: number;
  smtp_security: "ssl" | "starttls" | "none";
  smtp_username: string | null;
  smtp_from_email: string | null;
  smtp_from_name: string;
  smtp_authorization_code_configured: boolean;
  smtp_configured: boolean;
};

type EmailTestResult = {
  status: "ok";
  connection: "ok";
  tls: "ok" | "not_used";
  authentication: "ok";
  message_sent: boolean;
  detail: string;
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

export default function EmailSettingsPanel() {
  const [settings, setSettings] = useState<EmailSettings | null>(null);
  const [notificationsEnabled, setNotificationsEnabled] = useState(false);
  const [recipients, setRecipients] = useState("");
  const [host, setHost] = useState("smtp.163.com");
  const [port, setPort] = useState("465");
  const [security, setSecurity] = useState<"ssl" | "starttls" | "none">("ssl");
  const [username, setUsername] = useState("");
  const [fromName, setFromName] = useState("PolyCopy");
  const [authorizationCode, setAuthorizationCode] = useState("");
  const [testRecipient, setTestRecipient] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const applySettings = useCallback((next: EmailSettings) => {
    setSettings(next);
    setNotificationsEnabled(next.notifications_enabled);
    setRecipients(next.notification_recipients.join("\n"));
    setHost(next.smtp_host ?? "smtp.163.com");
    setPort(String(next.smtp_port ?? 465));
    setSecurity(next.smtp_security ?? "ssl");
    setUsername(next.smtp_username ?? "");
    setFromName(next.smtp_from_name ?? "PolyCopy");
  }, []);

  const load = useCallback(async () => {
    try {
      applySettings(await emailApi<EmailSettings>("/api/email-settings"));
      setError("");
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "无法读取邮件设置");
    }
  }, [applySettings]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  function payload() {
    return {
      smtp_host: host.trim(),
      notifications_enabled: notificationsEnabled,
      notification_recipients: recipients.split(/[\n,;]+/).map((email) => email.trim()).filter(Boolean),
      smtp_port: Number(port),
      smtp_security: security,
      smtp_from_name: fromName.trim(),
      ...(username.trim()
        ? { smtp_username: username.trim(), smtp_from_email: username.trim() }
        : {}),
      ...(authorizationCode.trim()
        ? { smtp_authorization_code: authorizationCode.trim() }
        : {}),
    };
  }

  async function saveSettings() {
    const next = await emailApi<EmailSettings>("/api/email-settings", {
      method: "PUT",
      body: JSON.stringify(payload()),
    });
    applySettings(next);
    setAuthorizationCode("");
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    setError("");
    try {
      await saveSettings();
      setMessage("发件邮箱配置已保存。");
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "保存邮件设置失败");
    } finally {
      setBusy(false);
    }
  }

  async function testConnection(sendEmail: boolean) {
    if (!username.trim()) {
      setError("请填写 163 发件邮箱。");
      return;
    }
    if (sendEmail && !testRecipient.trim()) {
      setError("请填写测试邮件收件邮箱。");
      return;
    }
    setBusy(true);
    setMessage("");
    setError("");
    try {
      await saveSettings();
      const result = await emailApi<EmailTestResult>("/api/email-settings/test", {
        method: "POST",
        body: JSON.stringify({
          send_email: sendEmail,
          recipient_email: sendEmail ? testRecipient.trim() : null,
        }),
      });
      setMessage(result.detail);
    } catch (testError) {
      setError(testError instanceof Error ? testError.message : "SMTP 测试失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="pcPanel emailSettingsPanel" aria-label="发件邮箱设置">
      <header className="pcPanelHeader">
        <div>
          <span className="pcEyebrow">EMAIL DELIVERY</span>
          <h2>发件邮箱</h2>
          <p>供系统通知统一使用；客户端授权码只保存到 macOS 钥匙串。</p>
        </div>
      </header>
      <form className="pcSettingsForm pcSystemSettingsForm" onSubmit={save}>
        <div className="emailNotificationGrid">
          <div className="pcField emailNotificationToggle"><span id="email-notification-toggle-label">启用系统邮件通知</span><input aria-labelledby="email-notification-toggle-label" type="checkbox" checked={notificationsEnabled} onChange={(event) => setNotificationsEnabled(event.target.checked)} disabled={busy} /><small>当前用于发送巨鲸新增提醒，后续系统通知共用此通道。</small></div>
          <label className="pcField"><span>通知收件邮箱</span><textarea aria-label="系统通知收件邮箱" value={recipients} onChange={(event) => setRecipients(event.target.value)} rows={4} placeholder="每行一个邮箱" disabled={busy} /><small>支持换行、逗号或分号分隔。</small></label>
        </div>
        <div className="emailSettingsStatus">
          <span>163 SMTP</span>
          <strong>{settings?.smtp_configured ? "配置完整" : "等待配置"}</strong>
          <small>{settings?.smtp_authorization_code_configured ? "授权码已保存" : "缺少客户端授权码"}</small>
        </div>
        <div className="pcFormGrid three emailSmtpGrid">
          <label className="pcField"><span>SMTP 服务器</span><input aria-label="SMTP 服务器" value={host} onChange={(event) => setHost(event.target.value)} disabled={busy} /></label>
          <label className="pcField"><span>端口</span><input aria-label="SMTP 端口" type="number" min="1" max="65535" value={port} onChange={(event) => setPort(event.target.value)} disabled={busy} /></label>
          <label className="pcField"><span>连接安全</span><select aria-label="SMTP 安全模式" value={security} onChange={(event) => setSecurity(event.target.value as "ssl" | "starttls" | "none")} disabled={busy}><option value="ssl">SSL（163 推荐）</option><option value="starttls">STARTTLS</option><option value="none">不加密</option></select></label>
          <label className="pcField"><span>163 发件邮箱</span><input aria-label="163 发件邮箱" type="email" value={username} onChange={(event) => setUsername(event.target.value)} placeholder="name@163.com" disabled={busy} /></label>
          <label className="pcField"><span>发件人名称</span><input aria-label="SMTP 发件人名称" value={fromName} onChange={(event) => setFromName(event.target.value)} disabled={busy} /></label>
          <label className="pcField"><span>客户端授权码</span><input aria-label="163 客户端授权码" type="password" autoComplete="new-password" value={authorizationCode} onChange={(event) => setAuthorizationCode(event.target.value)} placeholder={settings?.smtp_authorization_code_configured ? "留空表示保持原授权码" : "请输入客户端授权码"} disabled={busy} /></label>
        </div>
        <div className="emailSmtpTestRow">
          <label className="pcField"><span>测试邮件收件箱</span><input aria-label="测试邮件收件邮箱" type="email" value={testRecipient} onChange={(event) => setTestRecipient(event.target.value)} placeholder={username || "用于接收测试邮件"} disabled={busy} /></label>
          <button className="pcButton ghost" type="button" onClick={() => void testConnection(false)} disabled={busy}>{busy ? "测试中…" : "测试连接"}</button>
          <button className="pcButton ghost" type="button" onClick={() => void testConnection(true)} disabled={busy}>{busy ? "发送中…" : "发送测试邮件"}</button>
        </div>
        {error && <p className="pcFormError" role="alert">{error}</p>}
        {message && <p className="pcFormSuccess">{message}</p>}
        <div className="pcSettingsActions"><button className="pcButton primary" type="submit" disabled={busy || !settings}>{busy ? "保存中…" : "保存发件邮箱"}</button></div>
      </form>
    </section>
  );
}
