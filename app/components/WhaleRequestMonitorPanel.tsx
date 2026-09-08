"use client";

import { useCallback, useEffect, useState } from "react";
import {
  API_BASE,
  WhaleRequestLog,
  WhaleRequestLogList,
  whaleApi,
} from "./WhaleShared";

type ConnectionState = "connecting" | "connected" | "disconnected";
const MAX_RECENT_REQUESTS = 8;

function requestTimestamp(record: WhaleRequestLog): number {
  const timestamp = Date.parse(record.finished_at || record.started_at);
  return Number.isFinite(timestamp) ? timestamp : record.id;
}

function requestKey(record: WhaleRequestLog): string {
  return JSON.stringify([
    record.scan_id,
    record.source,
    record.method,
    record.url,
    Object.entries(record.query_params).sort(([left], [right]) => left.localeCompare(right)),
  ]);
}

function mergeRecentRequests(
  current: WhaleRequestLog[],
  candidates: WhaleRequestLog[],
): WhaleRequestLog[] {
  const records = new Map(current.map((record) => [record.id, record]));
  for (const record of candidates) {
    records.set(record.id, record);
  }
  const chronological = Array.from(records.values())
    .sort((left, right) => requestTimestamp(right) - requestTimestamp(left) || right.id - left.id);
  const recovered = new Set<string>();
  const unresolved = chronological.filter((record) => {
    const key = requestKey(record);
    if (record.status === "success") {
      recovered.add(key);
      return true;
    }
    return record.status !== "failed" || !recovered.has(key);
  });
  return unresolved
    .sort((left, right) => {
      const failurePriority = Number(right.status === "failed") - Number(left.status === "failed");
      return failurePriority || requestTimestamp(right) - requestTimestamp(left) || right.id - left.id;
    })
    .slice(0, MAX_RECENT_REQUESTS);
}

function consoleTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--:--:--";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

export function WhaleRequestMonitorPanel({
  onConnectionChange,
}: {
  onConnectionChange?: (state: ConnectionState) => void;
} = {}) {
  const [recentRequests, setRecentRequests] = useState<WhaleRequestLog[]>([]);
  const [connection, setConnection] = useState<ConnectionState>("connecting");

  const acceptRequests = useCallback((records: WhaleRequestLog[]) => {
    setRecentRequests((current) => mergeRecentRequests(current, records));
  }, []);

  const loadSnapshot = useCallback(async () => {
    try {
      const snapshot = await whaleApi<WhaleRequestLogList>("/api/whales/request-logs");
      acceptRequests(Array.isArray(snapshot.items) ? snapshot.items : []);
    } catch {
      // The live stream reconnects automatically; keep the last known success visible.
      return;
    }
  }, [acceptRequests]);

  useEffect(() => {
    onConnectionChange?.(connection);
  }, [connection, onConnectionChange]);

  useEffect(() => {
    let active = true;
    let stream: EventSource | null = null;
    const snapshotTimer = window.setTimeout(() => {
      if (!active) return;
      if (typeof EventSource === "undefined") setConnection("disconnected");
      void loadSnapshot();
    }, 0);

    if (typeof EventSource === "undefined") {
      return () => {
        active = false;
        window.clearTimeout(snapshotTimer);
      };
    }

    stream = new EventSource(`${API_BASE}/api/whales/request-logs/stream`);
    stream.onopen = () => {
      if (!active) return;
      setConnection("connected");
      void loadSnapshot();
    };
    stream.onmessage = (event) => {
      if (!active) return;
      try {
        const record = JSON.parse(event.data) as WhaleRequestLog;
        acceptRequests([record]);
      } catch {
        return;
      }
    };
    stream.onerror = () => {
      if (!active) return;
      setConnection("disconnected");
    };
    return () => {
      active = false;
      window.clearTimeout(snapshotTimer);
      stream?.close();
    };
  }, [acceptRequests, loadSnapshot]);

  const connectionLabel = connection === "connected"
    ? "实时连接"
    : connection === "disconnected"
      ? "正在重连"
      : "正在连接";

  return (
    <section className="whaleRequestMonitor whaleRequestMonitorInline" aria-label="Request Monitor">
      <div className="whaleTerminalFrame">
        <div className="whaleTerminalChrome">
          <span>
            <strong>请求监控</strong>
            <small>当前进程最近请求</small>
          </span>
          <b
            className={`whaleTerminalLive ${connection}`}
            aria-label={`请求监控状态：${connectionLabel}`}
            title={connectionLabel}
          >
            {connectionLabel}
          </b>
        </div>
        <div className="whaleRequestColumns">
          {[
            { status: "success", title: "成功日志", records: recentRequests.filter((record) => record.status !== "failed"), empty: "暂无成功或进行中的请求。" },
            { status: "failed", title: "错误日志", records: recentRequests.filter((record) => record.status === "failed"), empty: "暂无失败请求。" },
          ].map((column) => (
            <section className={`whaleRequestColumn ${column.status}`} aria-label={column.title} key={column.status}>
              <h3>{column.title}</h3>
              <div className="whaleRequestTerminal recent" aria-live="polite" role="status">
                {column.records.length ? (
                  column.records.map((record) => (
                    <div className={`whaleTerminalEntry ${record.status}`} key={record.id}>
                      <div className="whaleTerminalLine">
                        <time>{consoleTime(record.finished_at || record.started_at)}</time>
                        <b className={record.status}>{record.status}</b>
                        <code>
                          <span className="whaleRequestSummary">
                            <strong>{record.source.toUpperCase()} · {record.method}</strong>
                            <span className="whaleRequestUrl">{record.url}</span>
                            <small>{record.status === "failed" ? `${record.error_message || record.error_type || "请求失败"} · ${record.duration_ms ?? 0}ms` : `HTTP ${record.http_status ?? "—"} · ${record.duration_ms ?? 0}ms`}</small>
                          </span>
                        </code>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="whaleTerminalEmpty">{column.empty}</div>
                )}
              </div>
            </section>
          ))}
        </div>
      </div>
    </section>
  );
}
