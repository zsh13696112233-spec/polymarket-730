"use client";

import Link from "next/link";
import { type KeyboardEvent, useCallback, useEffect, useId, useRef, useState } from "react";
import { PolyCopyShell } from "./PolyCopyShell";
import { WhaleAutoSettingsPanel } from "./WhaleSettingsWorkspace";
import { useVisibleAutoRefresh } from "./useVisibleAutoRefresh";
import {
  WhaleAutoDecision,
  WhaleAutoDecisionList,
  WhaleRule,
  WhaleSettings,
  formatBeijing,
  formatPrice,
  formatUsdc,
  marketUrl,
  profileUrl,
  shortAddress,
  whaleApi,
} from "./WhaleShared";

const AUTO_REFRESH_INTERVAL_MS = 10_000;

const STATUS_OPTIONS = [
  ["", "全部状态"],
  ["pending", "等待处理"],
  ["bought", "已自动买入"],
  ["skipped", "已跳过"],
  ["failed", "执行失败"],
  ["conflict_locked", "分歧锁定"],
  ["exit_pending", "风控退出中"],
  ["exit_completed", "风控退出完成"],
] as const;

function DecisionFilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: ReadonlyArray<readonly [string, string]>;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const selectedIndex = Math.max(0, options.findIndex(([optionValue]) => optionValue === value));
  const [activeIndex, setActiveIndex] = useState(selectedIndex);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listboxId = useId();
  const selectedLabel = options[selectedIndex]?.[1] || options[0]?.[1] || "";

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: MouseEvent | FocusEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", closeOutside);
    document.addEventListener("focusin", closeOutside);
    return () => {
      document.removeEventListener("mousedown", closeOutside);
      document.removeEventListener("focusin", closeOutside);
    };
  }, [open]);

  const choose = (index: number) => {
    const option = options[index];
    if (!option) return;
    onChange(option[0]);
    setActiveIndex(index);
    setOpen(false);
    window.setTimeout(() => triggerRef.current?.focus(), 0);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Escape") {
      setOpen(false);
      return;
    }
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((current) => {
        if (event.key === "Home") return 0;
        if (event.key === "End") return options.length - 1;
        const direction = event.key === "ArrowDown" ? 1 : -1;
        return (current + direction + options.length) % options.length;
      });
      return;
    }
    if ((event.key === "Enter" || event.key === " ") && open) {
      event.preventDefault();
      choose(activeIndex);
    }
  };

  return (
    <label className="whaleDecisionFilter">
      <span>{label}</span>
      <div className={`whaleDecisionSelect ${open ? "open" : ""}`} ref={rootRef}>
        <button
          ref={triggerRef}
          type="button"
          role="combobox"
          aria-label={label}
          aria-expanded={open}
          aria-controls={listboxId}
          aria-activedescendant={open ? `${listboxId}-${activeIndex}` : undefined}
          onClick={() => {
            setActiveIndex(selectedIndex);
            setOpen((current) => !current);
          }}
          onKeyDown={handleKeyDown}
        >
          <span>{selectedLabel}</span>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4" /></svg>
        </button>
        {open && (
          <div className="whaleDecisionSelectMenu" id={listboxId} role="listbox" aria-label={label}>
            {options.map(([optionValue, optionLabel], index) => (
              <button
                id={`${listboxId}-${index}`}
                key={optionValue}
                type="button"
                role="option"
                tabIndex={-1}
                aria-selected={optionValue === value}
                className={index === activeIndex ? "active" : ""}
                onMouseEnter={() => setActiveIndex(index)}
                onClick={() => choose(index)}
              >
                <span>{optionLabel}</span>
                {optionValue === value && <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m3 8 3 3 7-7" /></svg>}
              </button>
            ))}
          </div>
        )}
      </div>
    </label>
  );
}

function decisionLabel(status: string) {
  return STATUS_OPTIONS.find(([value]) => value === status)?.[1] || status;
}

function decisionTone(status: string) {
  if (status === "bought" || status === "exit_completed") return "success";
  if (status === "pending" || status === "exit_pending") return "processing";
  if (status === "failed" || status === "conflict_locked") return "danger";
  return "neutral";
}

function decisionReason(reason: string | null) {
  if (!reason) return "等待处理";
  return reason.replace(
    /(实际买价|策略最低价|策略最高价) (-?\d+(?:\.\d+)?)/g,
    (_match, label: string, value: string) => {
      const normalized = value.includes(".") ? value.replace(/0+$/, "").replace(/\.$/, "") : value;
      return `${label} ${normalized}`;
    },
  );
}

function DecisionRow({ decision }: { decision: WhaleAutoDecision }) {
  const rules = decision.matched_rules.map((rule) => rule === "large_amount" ? "全量" : "新号").join(" + ");
  const selectedAmount = decision.selected_amount_usdc ?? decision.configured_amount_usdc;
  const usedLowPriceAmount = decision.selected_amount_usdc != null
    && decision.configured_amount_usdc != null
    && Number(decision.selected_amount_usdc) < Number(decision.configured_amount_usdc);
  return (
    <tr>
      <td><strong>{formatBeijing(decision.created_at, true)}</strong><span className={`pcBadge ${decisionTone(decision.status)}`}>{decisionLabel(decision.status)}</span></td>
      <td><a className="whaleAutoDecisionLink" href={profileUrl(decision.proxy_wallet)} target="_blank" rel="noreferrer"><strong>{shortAddress(decision.proxy_wallet)} ↗</strong><small>{rules || "—"}{decision.selected_rule ? ` · 采用${decision.selected_rule === "large_amount" ? "全量" : "新号"}` : ""}</small></a></td>
      <td><a className="whaleAutoDecisionLink" href={marketUrl(decision.event_slug || decision.market_slug)} target="_blank" rel="noreferrer"><strong>{decision.title} ↗</strong><small>{decision.outcome} · 同向已跟 {decision.followed_wallet_count} 个钱包</small></a></td>
      <td><span className="pcBadge neutral">{decision.category_label}</span></td>
      <td className="numeric"><strong>{selectedAmount == null ? "—" : formatUsdc(selectedAmount)}</strong><small>{usedLowPriceAmount ? "低价小额" : ""}</small></td>
      <td className="numeric"><strong>{decision.configured_min_price == null ? "—" : `${formatPrice(decision.configured_min_price)}–${formatPrice(decision.configured_max_price)}`}</strong><small>盘口 {formatPrice(decision.observed_best_ask)}</small></td>
      <td><strong>{decisionReason(decision.reason)}</strong><small>{decision.buy_order_id ? `买单 #${decision.buy_order_id}` : ""}{decision.latest_sell_order_id ? ` · 卖单 #${decision.latest_sell_order_id}` : ""}</small></td>
    </tr>
  );
}

export default function WhaleAutoFollowWorkspace() {
  const [settings, setSettings] = useState<WhaleSettings | null>(null);
  const [decisions, setDecisions] = useState<WhaleAutoDecision[]>([]);
  const [total, setTotal] = useState(0);
  const [rule, setRule] = useState<WhaleRule | "">("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError(null);
    const params = new URLSearchParams({ limit: "200", offset: "0" });
    if (rule) params.set("rule", rule);
    if (status) params.set("status", status);
    try {
      const [settingsResponse, decisionResponse] = await Promise.all([
        whaleApi<WhaleSettings>("/api/whales/settings"),
        whaleApi<WhaleAutoDecisionList>(`/api/whales/auto-decisions?${params.toString()}`),
      ]);
      setSettings(settingsResponse);
      setDecisions(decisionResponse.items || []);
      setTotal(decisionResponse.total || 0);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "自动跟单工作台加载失败");
    } finally {
      if (!silent) setLoading(false);
    }
  }, [rule, status]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  useVisibleAutoRefresh(async () => {
    if (loading) return;
    await load(true);
  }, AUTO_REFRESH_INTERVAL_MS);

  return (
    <PolyCopyShell
      active="auto-follow"
      title="自动跟单"
      subtitle="集中配置真实资金策略，并查看每一个信号的自动决策与风控退出结果。"
      actions={
        <>
          <Link className="pcButton ghost" href="/whales/records">查看持仓与流水</Link>
          <button className="pcButton primary" type="button" onClick={() => void load()} disabled={loading}>
            <span className={loading ? "spinning" : ""}>↻</span> 刷新
          </button>
        </>
      }
    >
      {error && (
        <div className="pcAlert danger whalePageError" role="alert">
          <strong>自动跟单数据读取未完成</strong><p>{error}</p><button type="button" onClick={() => void load()}>重试</button>
        </div>
      )}

      <WhaleAutoSettingsPanel settings={settings} onSettingsChange={setSettings} onReload={load} />

      <section className="pcPanel whaleAutoDecisionPanel" aria-label="自动跟单决策记录">
        <header className="pcPanelHeader whaleRecordsHeader">
          <div><span className="pcEyebrow">AUTO FOLLOW DECISIONS</span><h2>自动跟单决策</h2><p>每个钱包资产只判断一次；未成交信号也永久保留中文原因。</p></div>
          <div className="whaleDecisionFilters" aria-label="自动决策筛选">
            <DecisionFilterSelect
              label="命中规则"
              value={rule}
              options={[["", "全部规则"], ["new_account", "新号大额"], ["large_amount", "全量超大额"]]}
              onChange={(nextRule) => setRule(nextRule as WhaleRule | "")}
            />
            <DecisionFilterSelect label="决策状态" value={status} options={STATUS_OPTIONS} onChange={setStatus} />
          </div>
          <span className="pcPanelCount">{total} 条</span>
        </header>
        {loading && !decisions.length ? (
          <div className="pcLoading whaleLoading">正在读取自动决策…</div>
        ) : decisions.length ? (
          <div className="pcTableWrap">
            <table className="pcTable whaleAutoDecisionTable">
              <colgroup>
                <col className="whaleDecisionTimeColumn" />
                <col className="whaleDecisionWalletColumn" />
                <col className="whaleDecisionMarketColumn" />
                <col className="whaleDecisionCategoryColumn" />
                <col className="whaleDecisionAmountColumn" />
                <col className="whaleDecisionPriceColumn" />
                <col className="whaleDecisionReasonColumn" />
              </colgroup>
              <thead><tr><th>时间 / 状态</th><th>钱包 / 规则</th><th>市场 / 方向</th><th>分类</th><th className="numeric">策略金额</th><th className="numeric">价格区间 / 盘口</th><th>说明</th></tr></thead>
              <tbody>{decisions.map((decision) => <DecisionRow key={decision.id} decision={decision} />)}</tbody>
            </table>
          </div>
        ) : (
          <div className="pcEmptyState whaleCompactEmpty"><div className="pcEmptyIcon">◎</div><h2>暂无自动跟单决策</h2><p>只有功能上线后首次产生的新信号会进入这里，历史信号不会回放。</p></div>
        )}
      </section>
    </PolyCopyShell>
  );
}
