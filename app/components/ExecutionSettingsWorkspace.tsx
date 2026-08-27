"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { PolyCopyShell } from "./PolyCopyShell";
import EmailSettingsPanel from "./EmailSettingsPanel";

const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8730").replace(/\/$/, "");

type Account = {
  signer_address: string | null;
  funder_address: string | null;
  signature_type: 1 | 3;
  credentials_configured: boolean;
  status: string;
  budget_usdc: number;
  cash_reserve_usdc: number;
  max_total_exposure_usdc: number;
  daily_buy_limit_usdc: number;
  daily_loss_limit_usdc: number;
  auto_redeem: boolean;
  collateral_balance: number | null;
  last_balance_at: string | null;
  last_error: string | null;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}) },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.detail || `请求失败（${response.status}）`);
  return payload as T;
}

function money(value: number | null) {
  return value === null ? "—" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);
}

export default function ExecutionSettingsWorkspace() {
  const [account, setAccount] = useState<Account | null>(null);
  const [signer, setSigner] = useState("");
  const [funder, setFunder] = useState("");
  const [signatureType, setSignatureType] = useState<1 | 3>(3);
  const [cashReserve, setCashReserve] = useState("240");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      const next = await request<Account | null>("/api/execution-account");
      setAccount(next);
      if (next) {
        setSigner(next.signer_address ?? "");
        setFunder(next.funder_address ?? "");
        setSignatureType(next.signature_type);
        setCashReserve(String(next.cash_reserve_usdc));
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "无法读取执行钱包");
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setMessage("");
    try {
      const reserve = Number(cashReserve);
      await request<Account>("/api/execution-account", {
        method: "PUT",
        body: JSON.stringify({
          signer_address: signer,
          funder_address: funder,
          signature_type: signatureType,
          budget_usdc: Math.max(400, reserve),
          cash_reserve_usdc: reserve,
          max_total_exposure_usdc: 160,
          daily_buy_limit_usdc: 80,
          daily_loss_limit_usdc: 40,
          auto_redeem: true,
        }),
      });
      setMessage("执行钱包配置已保存。");
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    } finally { setBusy(false); }
  }

  async function action(path: string, success: string) {
    setBusy(true); setMessage("");
    try {
      await request<Account>(path, { method: "POST" });
      setMessage(success);
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "操作失败");
    } finally { setBusy(false); }
  }

  return (
    <PolyCopyShell active="settings" title="系统设置" subtitle="统一管理执行钱包、发件邮箱与系统安全配置">
      <section className="pcPanel pcExecutionAccountPanel">
        <header className="pcPanelHeader"><div><span className="pcEyebrow">EXECUTION WALLET</span><h2>执行钱包</h2><p>仅用于链上监测产生的真实买入、卖出与赎回。</p></div></header>
        {account && <div className="pcAccountSummary"><div><span>状态</span><strong>{account.status}</strong></div><div><span>pUSD 余额</span><strong>{money(account.collateral_balance)}</strong></div><div><span>钥匙串</span><strong>{account.credentials_configured ? "已配置" : "未配置"}</strong></div><div><span>最后更新</span><strong>{account.last_balance_at ? new Date(account.last_balance_at).toLocaleString("zh-CN") : "—"}</strong></div></div>}
        <form className="pcSettingsForm pcSystemSettingsForm" onSubmit={save}>
          <div className="pcFormGrid two">
            <label className="pcField"><span>签名钱包地址</span><input value={signer} onChange={(event) => setSigner(event.target.value)} placeholder="0x…" required /></label>
            <label className="pcField"><span>资金钱包地址</span><input value={funder} onChange={(event) => setFunder(event.target.value)} placeholder="0x…" required /></label>
            <label className="pcField"><span>钱包类型</span><select value={signatureType} onChange={(event) => setSignatureType(Number(event.target.value) as 1 | 3)}><option value={3}>Deposit Wallet</option><option value={1}>Poly Proxy</option></select></label>
            <label className="pcField"><span>现金保留额</span><div className="pcUnitInput"><input type="number" min="0" step="0.01" value={cashReserve} onChange={(event) => setCashReserve(event.target.value)} /><b>USDC</b></div><small>下单后余额低于该值时显示风险警告。</small></label>
          </div>
          {signer && !account?.credentials_configured && <div className="pcCommandHint"><span>导入执行密钥</span><code>uv run python -m backend.trading_cli set-key --account {signer}</code></div>}
          {message && <p className={message.includes("已") ? "pcFormSuccess" : "pcFormError"}>{message}</p>}
          <div className="pcSettingsActions"><button className="pcButton primary" type="submit" disabled={busy}>保存配置</button><button className="pcButton ghost" type="button" disabled={busy || !account} onClick={() => void action("/api/execution-account/verify", "执行钱包验证完成。")}>验证密钥与授权</button><button className="pcButton ghost" type="button" disabled={busy || !account} onClick={() => void action("/api/execution-account/balance/refresh", "余额已刷新。")}>刷新余额</button></div>
        </form>
      </section>
      <EmailSettingsPanel />
    </PolyCopyShell>
  );
}
