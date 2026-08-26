"use client";

import { useCallback, useEffect, useState } from "react";
import {
  API_BASE,
  WhaleRequestLog,
  WhaleRequestLogList,
  whaleApi,
} from "./WhaleShared";

type ConnectionState = "connecting" | "connected" | "disconnected";
const MAX_RECENT_SUCCESSES = 5;

function successTimestamp(record: WhaleRequestLog): number {
  const timestamp = Date.parse(record.finished_at || record.started_at);
  return Number.isFinite(timestamp) ? timestamp : record.id;
}

function mergeRecentSuccesses(
  current: WhaleRequestLog[],
  candidates: WhaleRequestLog[],
): WhaleRequestLog[] {
  const records = new Map(current.map((record) => [record.id, record]));
  for (const record of candidates) {
    if (record.status === "success") records.set(record.id, record);
  }
  return Array.from(records.values())
    .sort((left, right) => successTimestamp(right) - successTimestamp(left) || right.id - left.id)
    .slice(0, MAX_RECENT_SUCCESSES);
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

export function WhaleRequestMonitorPanel() {
  const [recentSuccesses, setRecentSuccesses] = useState<WhaleRequestLog[]>([]);
  const [connection, setConnection] = useState<ConnectionState>("connecting");

  const acceptSuccesses = useCallback((records: WhaleRequestLog[]) => {
    setRecentSuccesses((current) => mergeRecentSuccesses(current, records));
  }, []);

  const loadSnapshot = useCallback(async () => {
    try {
      const snapshot = await whaleApi<WhaleRequestLogList>("/api/whales/request-logs");
      acceptSuccesses(snapshot.items);
    } catch {
      // The live stream reconnects automatically; keep the last known success visible.
      return;
    }
  }, [acceptSuccesses]);

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
        if (record.status === "success") acceptSuccesses([record]);
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
  }, [acceptSuccesses, loadSnapshot]);

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
            <small>最近成功请求</small>
          </span>
          <b
            className={`whaleTerminalLive ${connection}`}
            aria-label={`请求监控状态：${connectionLabel}`}
            title={connectionLabel}
          >
            {connectionLabel}
          </b>
        </div>
        <div className="whaleRequestTerminal recent" aria-live="polite" role="status">
          {recentSuccesses.length ? (
            recentSuccesses.map((record) => (
              <div className="whaleTerminalEntry success" key={record.id}>
                <div className="whaleTerminalLine">
                  <time>{consoleTime(record.finished_at || record.started_at)}</time>
                  <b className="success">success</b>
                  <code>
                    <span><strong>{record.method}</strong> {record.url}</span>
                    <small>{`HTTP ${record.http_status ?? "—"} · ${record.duration_ms ?? 0}ms`}</small>
                  </code>
                </div>
              </div>
            ))
          ) : (
            <div className="whaleTerminalEmpty">
              暂无成功请求，收到新数据后会自动更新。
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
