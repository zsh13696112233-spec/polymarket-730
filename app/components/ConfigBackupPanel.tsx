"use client";

import { useRef, useState } from "react";
import { API_BASE } from "./WhaleShared";

type ImportResult = { added: number; updated: number; retained: number; message: string };

async function backupRequest(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}) },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = payload?.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail)
      ? detail.map((item: { loc?: string[]; msg?: string }) => `${item.loc?.slice(1).join(".") || "文件"}：${item.msg || "格式不正确"}`).join("；")
      : `请求失败（${response.status}）`;
    throw new Error(message);
  }
  return response;
}

function readFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("无法读取文件，请重新选择配置文件。"));
    reader.readAsText(file);
  });
}

export default function ConfigBackupPanel({ onImported }: { onImported: () => void }) {
  const fileInput = useRef<HTMLInputElement>(null);
  const pending = useRef(false);
  const [busy, setBusy] = useState<"export" | "import" | null>(null);
  const [notice, setNotice] = useState<{ error: boolean; text: string } | null>(null);

  async function exportConfiguration() {
    if (pending.current) return;
    pending.current = true;
    setBusy("export"); setNotice(null);
    try {
      const response = await backupRequest("/api/whales/config/export");
      const blob = new Blob([await response.text()], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `polycopy-config-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
      setNotice({ error: false, text: "已导出保存的配置和黑名单。" });
    } catch (error) {
      setNotice({ error: true, text: `导出失败：${error instanceof Error ? error.message : "请稍后重试"}` });
    } finally {
      pending.current = false; setBusy(null);
    }
  }

  async function importConfiguration(file: File) {
    if (pending.current) return;
    pending.current = true;
    setBusy("import"); setNotice(null);
    try {
      const content = (await readFile(file)).replace(/^\uFEFF/, "");
      try { JSON.parse(content); } catch { throw new Error("文件不是有效的 JSON，请选择导出的配置文件。"); }
      const response = await backupRequest("/api/whales/config/import", { method: "POST", body: content });
      const result = await response.json() as ImportResult;
      onImported();
      setNotice({ error: false, text: `${result.message} 黑名单新增 ${result.added} 个、更新备注 ${result.updated} 个、保留 ${result.retained} 个。` });
    } catch (error) {
      setNotice({ error: true, text: `导入未完成：${error instanceof Error ? error.message : "请检查文件后重试"}` });
    } finally {
      pending.current = false; setBusy(null);
    }
  }

  return (
    <section className="pcPanel pcConfigBackupPanel" aria-label="配置备份">
      <div>
        <h2>配置备份</h2>
        <p>导出已保存的监测、跟单策略和黑名单，不含钱包配置或密钥。</p>
        <p>导入会覆盖策略配置和未保存的策略修改、合并黑名单，同地址备注以文件为准；自动跟单和监测自动赎回会关闭。</p>
      </div>
      <div className="pcSettingsActions">
        <button className="pcButton ghost" type="button" disabled={busy !== null} onClick={() => void exportConfiguration()}>{busy === "export" ? "导出中…" : "导出配置"}</button>
        <button className="pcButton primary" type="button" disabled={busy !== null} onClick={() => fileInput.current?.click()}>{busy === "import" ? "导入中…" : "导入配置"}</button>
        <input ref={fileInput} aria-label="选择配置文件" type="file" accept=".json,application/json" hidden disabled={busy !== null} onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) void importConfiguration(file);
        }} />
      </div>
      {notice && <p className={notice.error ? "pcFormError" : "pcFormSuccess"} role={notice.error ? "alert" : "status"}>{notice.text}</p>}
    </section>
  );
}
