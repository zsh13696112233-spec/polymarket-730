"use client";

import { useCallback, useEffect, useState } from "react";
import { PolyCopyShell } from "./PolyCopyShell";
import { useVisibleAutoRefresh } from "./useVisibleAutoRefresh";
import { formatCompactUsdc, formatPrice, formatUsdc } from "./WhaleShared";
import type { Numeric } from "./WhaleShared";

const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8730"
).replace(/\/$/, "");
const PAGE_SIZE = 50;

type DeliveryStatus = "pending" | "sending" | "retrying" | "sent" | "failed";
type NotificationKind = "entry" | "divergence" | "weekly_summary";
type SignalResult = "pending" | "hit" | "miss" | "special" | "not_applicable";

type EmailDelivery = {
  id: number;
  entry_id: number | null;
  notification_kind: NotificationKind;
  condition_id: string;
  entry_ids: number[];
  rules: Array<"new_account" | "large_amount">;
  recipient_email: string;
  market_title: string;
  wallet_label: string;
  market_summaries: Array<{
    category_label: string;
    outcome: string;
    avg_buy_price: Numeric;
    gross_buy_usdc: Numeric;
  }>;
  subject: string;
  body_text: string;
  result: SignalResult;
  status: DeliveryStatus;
  attempt_count: number;
  next_attempt_at: string | null;
  last_error: string | null;
  created_at: string;
  sent_at: string | null;
};

type EmailSummarySettings = {
  notifications_enabled: boolean;
  weekly_summary_enabled: boolean;
  weekly_summary_enabled_at: string | null;
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

const statusLabels: Record<DeliveryStatus, string> = {
  pending: "待发送",
  sending: "发送中",
  retrying: "等待重试",
  sent: "已发送",
  failed: "发送失败",
};

const resultLabels: Record<SignalResult, string> = {
  pending: "待结算",
  hit: "命中",
  miss: "未命中",
  special: "特殊结算",
  not_applicable: "不适用",
};

const resultTones: Record<SignalResult, string> = {
  pending: "warning",
  hit: "success",
  miss: "danger",
  special: "warning",
  not_applicable: "muted",
};

function formatTime(value: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  });
}

export default function EmailRecordsWorkspace() {
  const [items, setItems] = useState<EmailDelivery[]>([]);
  const [total, setTotal] = useState(0);
  const [filter, setFilter] = useState("all");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [summarySettings, setSummarySettings] = useState<EmailSummarySettings | null>(null);
  const [summarySaving, setSummarySaving] = useState(false);
  const [summaryMessage, setSummaryMessage] = useState("");
  const [summaryError, setSummaryError] = useState("");

  const load = useCallback(async () => {
    try {
      const response = await fetch(
        `${API_BASE}/api/email-notifications?status=${filter}&limit=${PAGE_SIZE}&offset=${(page - 1) * PAGE_SIZE}`,
        { headers: { Accept: "application/json" } },
      );
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.detail || `请求失败（${response.status}）`);
      setItems(payload.items);
      setTotal(payload.total);
      setError("");
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "无法读取邮件发送记录");
    } finally {
      setLoading(false);
    }
  }, [filter, page]);

  const loadSummarySettings = useCallback(async () => {
    try {
      setSummarySettings(await emailApi<EmailSummarySettings>("/api/email-settings"));
      setSummaryError("");
    } catch (loadError) {
      setSummaryError(loadError instanceof Error ? loadError.message : "无法读取每周汇总设置");
    }
  }, []);

  async function setWeeklySummaryEnabled(enabled: boolean) {
    setSummarySaving(true);
    setSummaryMessage("");
    setSummaryError("");
    try {
      const next = await emailApi<EmailSummarySettings>("/api/email-settings", {
        method: "PUT",
        body: JSON.stringify({ weekly_summary_enabled: enabled }),
      });
      setSummarySettings(next);
      setSummaryMessage(enabled ? "每周命中率汇总已启用，将从下一个周一开始发送。" : "每周命中率汇总已关闭。");
    } catch (saveError) {
      setSummaryError(saveError instanceof Error ? saveError.message : "保存每周汇总设置失败");
    } finally {
      setSummarySaving(false);
    }
  }

  useEffect(() => {
    const initial = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(initial);
  }, [load]);

  useVisibleAutoRefresh(async () => {
    if (loading) return;
    await load();
  }, 30_000);

  useEffect(() => {
    const initial = window.setTimeout(() => void loadSummarySettings(), 0);
    return () => window.clearTimeout(initial);
  }, [loadSummarySettings]);

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <PolyCopyShell
      active="email-records"
      title="邮件记录"
      subtitle="查看系统通知的逐收件人投递状态、重试次数与失败原因"
    >
      <section className="pcPanel weeklyEmailSummaryPanel" aria-label="每周邮件命中率汇总">
        <div className="weeklyEmailSummaryCopy">
          <span className="pcEyebrow">WEEKLY HIT RATE</span>
          <h2>每周命中率汇总</h2>
          <p>每周一 00:00（北京时间）汇总上一周新结算的已发邮件信号，同一信号不会因多个收件人重复计算。</p>
          {summarySettings?.weekly_summary_enabled && !summarySettings.notifications_enabled && (
            <small className="weeklyEmailSummaryPaused">系统邮件通知当前关闭，周报设置已保留但发送暂停。</small>
          )}
          {summarySettings?.weekly_summary_last_sent_at && (
            <small>上次发送：{formatTime(summarySettings.weekly_summary_last_sent_at)}</small>
          )}
          {summarySettings?.weekly_summary_enabled && summarySettings.weekly_summary_next_run_at && (
            <small>下次计划：{formatTime(summarySettings.weekly_summary_next_run_at)}</small>
          )}
        </div>
        <label className="weeklyEmailSummaryToggle">
          <span>{summarySettings?.weekly_summary_enabled ? "已启用" : "未启用"}</span>
          <input
            aria-label="启用每周命中率汇总"
            type="checkbox"
            checked={summarySettings?.weekly_summary_enabled ?? false}
            disabled={!summarySettings || summarySaving}
            onChange={(event) => void setWeeklySummaryEnabled(event.target.checked)}
          />
        </label>
        {summaryError && <p className="pcFormError weeklyEmailSummaryMessage" role="alert">{summaryError}</p>}
        {summaryMessage && <p className="pcFormSuccess weeklyEmailSummaryMessage">{summaryMessage}</p>}
      </section>
      <section className="pcPanel emailRecordsPanel" aria-label="邮件发送记录">
        <header className="pcPanelHeader emailDeliveryHeading">
          <div>
            <span className="pcEyebrow">EMAIL DELIVERY LOG</span>
            <h2>发送记录</h2>
            <p>记录巨鲸单笔提醒与市场分歧提醒，每位收件人的投递单独保存。</p>
          </div>
          <label className="emailRecordsFilter">
            <span>投递状态</span>
            <select
              aria-label="邮件状态筛选"
              value={filter}
              onChange={(event) => {
                setFilter(event.target.value);
                setPage(1);
                setLoading(true);
              }}
            >
              <option value="all">全部状态</option>
              <option value="pending">待发送</option>
              <option value="sending">发送中</option>
              <option value="retrying">等待重试</option>
              <option value="sent">已发送</option>
              <option value="failed">发送失败</option>
            </select>
          </label>
        </header>
        {error && <p className="pcFormError emailRecordsMessage" role="alert">{error}</p>}
        <div className={`emailRecordList${loading ? " loading" : ""}`}>
          {!loading && items.length === 0 ? (
            <div className="emailRecordsEmpty">暂无发送记录。</div>
          ) : items.map((item) => (
            <article className="emailRecordCard" key={item.id}>
              <header className="emailRecordCardHeader">
                <div className="emailRecordLead">
                  <div className="emailRecordBadges">
                    {item.notification_kind === "weekly_summary" && (
                      <span className="pcBadge warning">每周汇总</span>
                    )}
                    {item.notification_kind === "divergence" && (
                      <span className="pcBadge danger">分歧市场</span>
                    )}
                    {item.notification_kind !== "weekly_summary" && (
                      <span className={`pcBadge ${resultTones[item.result]}`}>{resultLabels[item.result]}</span>
                    )}
                  </div>
                  <h3>{item.market_title}</h3>
                  <p>
                    <strong>{item.wallet_label}</strong>
                    <span>
                      {item.notification_kind === "divergence"
                        ? `市场级提醒 · ${item.entry_ids.length} 条关联记录`
                        : item.notification_kind === "weekly_summary"
                          ? "北京时间 · 自动周报"
                          : `记录 #${item.entry_id}`}
                    </span>
                  </p>
                </div>
                <div className="emailRecordStatusBlock">
                  <strong className={`emailStatus ${item.status}`}>{statusLabels[item.status]}</strong>
                </div>
              </header>

              {item.notification_kind !== "weekly_summary" && <div className="emailRecordMarketGrid" aria-label="买入摘要">
                <div className="emailRecordSectionHeading">
                  <strong>买入摘要</strong>
                  <span>{item.market_summaries.length > 0 ? `${item.market_summaries.length} 个市场方向` : "暂无市场方向"}</span>
                </div>
                {item.market_summaries.length > 0 ? (
                  <div className="emailRecordMarketList">
                    {item.market_summaries.map((summary) => (
                      <div className="emailRecordMarketRow" key={`${summary.outcome}-${summary.avg_buy_price}-${summary.gross_buy_usdc}`}>
                        <div className="emailRecordRuleCell">
                          <span>买入类别</span>
                          <strong>{item.rules.map((rule) => rule === "new_account" ? "新号大额" : "全量超大额").join(" / ")}</strong>
                        </div>
                        <div className="emailRecordOutcome">
                          <span>市场种类</span>
                          <strong>{summary.category_label}</strong>
                          <small>买入方向 · <b>{summary.outcome}</b></small>
                        </div>
                        <div className="emailRecordMarketMetric">
                          <span>买入均价</span>
                          <strong>{formatPrice(summary.avg_buy_price)} <small>USDC</small></strong>
                        </div>
                        <div className="emailRecordMarketMetric total">
                          <span>买入总额</span>
                          <strong title={formatUsdc(summary.gross_buy_usdc)}>{formatCompactUsdc(summary.gross_buy_usdc)}</strong>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="emailRecordMarketEmpty">暂无关联买入数据</div>
                )}
              </div>}

              <div className="emailRecordMetaGrid">
                <div><span>收件人</span><strong>{item.recipient_email}</strong></div>
                <div><span>投递时间</span><strong>{formatTime(item.sent_at)}</strong><small>触发于 {formatTime(item.created_at)}</small></div>
                <div>
                  <span>投递详情</span>
                  <strong>尝试 {item.attempt_count} 次</strong>
                  {item.status === "retrying" && <small>下次重试：{formatTime(item.next_attempt_at)}</small>}
                  {item.last_error && <small className="emailDeliveryError">{item.last_error}</small>}
                </div>
              </div>

            </article>
          ))}
        </div>
        <footer className="emailRecordsPagination">
          <span>共 {total} 条 · 第 {page} / {pageCount} 页</span>
          <div>
            <button className="pcButton ghost" type="button" disabled={page <= 1 || loading} onClick={() => { setPage((value) => value - 1); setLoading(true); }}>上一页</button>
            <button className="pcButton ghost" type="button" disabled={page >= pageCount || loading} onClick={() => { setPage((value) => value + 1); setLoading(true); }}>下一页</button>
          </div>
        </footer>
      </section>
    </PolyCopyShell>
  );
}
