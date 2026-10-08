"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { whaleApi, formatBeijing } from "./WhaleShared";
import { useVisibleAutoRefresh } from "./useVisibleAutoRefresh";

type Connection = {
  host: string;
  port: number;
  categories: Record<string, { ready: boolean; reason?: string | null; cursor: number }>;
  server: { supported_categories?: string[]; completed_at?: string | null; source_id?: string };
  last_error: string | null;
};
const labels: Record<string, string> = { sports: "体育", esports: "电竞", politics: "政治", crypto: "加密", science_tech: "科学与科技", entertainment: "娱乐", other: "其他" };

export default function CollectionSettingsPanel() {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [host, setHost] = useState<string | null>(null);
  const [port, setPort] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    try { setConnection(await whaleApi<Connection>("/api/collection/connection")); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "服务器同步状态读取失败"); }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);
  useVisibleAutoRefresh(refresh, 5000);
  const payload = { host: host ?? connection?.host ?? "", port: Number(port ?? connection?.port ?? 8731) };

  async function act(test: boolean) {
    setBusy(true); setError(""); setMessage("");
    try {
      if (test) {
        const result = await whaleApi<Connection["server"]>("/api/collection/connection/test", { method: "POST", body: JSON.stringify(payload) });
        setMessage(`连接成功，服务器支持：${(result.supported_categories ?? []).map((key) => labels[key] ?? key).join("、")}`);
      } else {
        setConnection(await whaleApi<Connection>("/api/collection/connection", { method: "PUT", body: JSON.stringify(payload) }));
        setMessage("连接已保存，等待后台同步；首次追平仅建立历史基线。");
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : "连接操作失败"); }
    finally { setBusy(false); }
  }
  function submit(event: FormEvent) { event.preventDefault(); void act(false); }

  return <section className="pcSettingsSection">
    <h2>公共采集服务器</h2>
    <p className="pcFormHint">本地每 5 秒检查增量，监测分类在监测设置中选择。恢复连接后先追平，不追买离线期间的信号。</p>
    <form className="pcSettingsForm" onSubmit={submit}>
      <div className="pcFormGrid">
        <label className="pcField"><span>服务器 IP</span><input aria-label="服务器 IP" value={payload.host} onChange={(event) => setHost(event.target.value)} placeholder="192.168.1.20" /></label>
        <label className="pcField"><span>服务器端口</span><input aria-label="服务器端口" type="number" min="1" max="65535" value={port ?? connection?.port ?? 8731} onChange={(event) => setPort(event.target.value)} /></label>
      </div>
      <p>支持分类：{connection?.server.supported_categories?.map((key) => labels[key] ?? key).join("、") || "尚未获取"}</p>
      <p>最后完整采集：{formatBeijing(connection?.server.completed_at ?? null, true)}</p>
      {Object.entries(connection?.categories ?? {}).map(([category, state]) => <p key={category}>{labels[category] ?? category}：{state.ready ? "已追平" : state.reason || "同步中或覆盖不完整"}</p>)}
      {(error || connection?.last_error) && <p className="pcFormError" role="alert">{error || connection?.last_error}</p>}
      {message && <p className="pcFormSuccess" role="status">{message}</p>}
      <div className="pcSettingsActions"><button className="pcButton" type="button" disabled={busy} onClick={() => void act(true)}>测试服务器连接</button><button className="pcButton primary" type="submit" disabled={busy}>保存服务器</button></div>
    </form>
  </section>;
}
