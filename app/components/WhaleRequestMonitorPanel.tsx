"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  API_BASE,
  WhaleRequestLog,
  WhaleRequestLogList,
  whaleApi,
} from "./WhaleShared";

type RequestFilter = "all" | "failed";
type ConnectionState = "connecting" | "connected" | "disconnected";

function mergeRequestLog(
  current: WhaleRequestLog[],
  incoming: WhaleRequestLog | WhaleRequestLog[],
): WhaleRequestLog[] {
  const merged = new Map(current.map((record) => [record.id, record]));
  for (const record of Array.isArray(incoming) ? incoming : [incoming]) {
    merged.set(record.id, record);
  }
  return Array.from(merged.values())
    .sort((left, right) => right.id - left.id)
    .slice(0, 100);
}

function requestEndpoint(record: WhaleRequestLog): { host: string; path: string } {
  try {
    const url = new URL(record.url);
    return { host: url.host, path: url.pathname || "/" };
  } catch {
    return { host: "Polymarket", path: record.url };
  }
}

function queryText(record: WhaleRequestLog): string {
  return JSON.stringify(record.query_params);
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
  const [records, setRecords] = useState<WhaleRequestLog[]>([]);
  const [filter, setFilter] = useState<RequestFilter>("all");
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [loadError, setLoadError] = useState<string | null>(null);
  const terminalRef = useRef<HTMLDivElement | null>(null);

  const loadSnapshot = useCallback(async () => {
    try {
      const snapshot = await whaleApi<WhaleRequestLogList>("/api/whales/request-logs");
      setRecords((current) => mergeRequestLog(current, snapshot.items));
      setLoadError(null);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "请求监测记录加载失败");
    }
  }, []);

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
        setRecords((current) => mergeRequestLog(current, record));
        setLoadError(null);
      } catch {
        setLoadError("收到的请求监测消息格式无效");
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
  }, [loadSnapshot]);

  const failedCount = records.filter((record) => record.status === "failed").length;
  const visibleRecords = useMemo(
    () => records
      .filter((record) => filter === "all" || record.status === "failed")
      .slice()
      .reverse(),
    [filter, records],
  );

  useEffect(() => {
    const terminal = terminalRef.current;
    if (terminal) terminal.scrollTop = terminal.scrollHeight;
  }, [visibleRecords]);

  return (
    <section className="pcPanel whaleRequestMonitor" aria-label="巨鲸请求监测">
      <header className="whaleRequestMonitorHeader">
        <div>
          <div className="whaleRequestMonitorTitle">
            <span aria-hidden="true">⌁</span>
            <div>
              <h2>请求监测</h2>
              <p>巨鲸扫描器发往 Polymarket 的最近 100 条外部请求</p>
            </div>
          </div>
        </div>
        <div className="whaleRequestMonitorControls">
          <span className={`whaleRequestConnection ${connection}`}>
            <i aria-hidden="true" />
            {connection === "connected" ? "实时已连接" : connection === "connecting" ? "正在连接" : "实时连接中断"}
          </span>
          <span className="whaleRequestCount">共 {records.length} 条</span>
          <span className={`whaleRequestCount ${failedCount ? "danger" : ""}`}>失败 {failedCount}</span>
          <div className="whaleRequestFilters" aria-label="请求日志筛选">
            <button type="button" className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>全部</button>
            <button type="button" className={filter === "failed" ? "active" : ""} onClick={() => setFilter("failed")}>仅失败</button>
          </div>
        </div>
      </header>

      {loadError && (
        <div className="whaleRequestMonitorWarning" role="status">
          <span>{loadError}</span>
          <button type="button" onClick={() => void loadSnapshot()}>重新读取</button>
        </div>
      )}

      <div className="whaleTerminalFrame">
        <div className="whaleTerminalChrome" aria-hidden="true">
          <span><i /><i /><i /></span>
          <code>polymarket-whale-monitor — live requests</code>
          <b>100 × buffer</b>
        </div>
        <div className="whaleRequestTerminal" aria-live="polite" role="log" ref={terminalRef}>
        {visibleRecords.length ? visibleRecords.map((record) => {
          const endpoint = requestEndpoint(record);
          return (
            <div className={`whaleTerminalEntry ${record.status}`} key={record.id}>
              <div className="whaleTerminalLine">
                <time>{consoleTime(record.started_at)}</time>
                <b className="request">[request]</b>
                <code><strong>{record.method}</strong> {record.url} · scan={record.scan_id.slice(0, 8)}</code>
              </div>
              <div className="whaleTerminalLine continuation">
                <time aria-hidden="true"> </time>
                <b className="query">[query]</b>
                <code>{queryText(record)}</code>
              </div>
              <div className="whaleTerminalLine">
                <time>{consoleTime(record.finished_at || record.started_at)}</time>
                <b className={record.status}>[{record.status === "pending" ? "pending" : record.status}]</b>
                <code>
                  {endpoint.host}{endpoint.path}
                  {record.status === "pending"
                    ? " · waiting for response…"
                    : ` · HTTP ${record.http_status ?? "—"} · ${record.duration_ms ?? 0}ms`}
                  {record.status === "success" ? " · complete ✓" : ""}
                </code>
              </div>
              {record.status === "failed" && (
                <>
                  <div className="whaleTerminalLine continuation" role="alert">
                    <time aria-hidden="true"> </time>
                    <b className="error">[error]</b>
                    <code>{record.error_type ? `${record.error_type}: ` : ""}{record.error_message || "未知错误"}</code>
                  </div>
                  {record.response_excerpt && (
                    <div className="whaleTerminalLine continuation">
                      <time aria-hidden="true"> </time>
                      <b className="response">[response]</b>
                      <code>{record.response_excerpt}</code>
                    </div>
                  )}
                </>
              )}
            </div>
          );
        }) : (
          <div className="whaleTerminalEmpty">
            <span>_</span>{filter === "failed" ? "最近 100 条请求中没有失败记录" : "waiting for next whale scan request…"}
          </div>
        )}
        </div>
      </div>
    </section>
  );
}
