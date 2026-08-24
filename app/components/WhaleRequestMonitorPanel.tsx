"use client";

import { useCallback, useEffect, useState } from "react";
import {
  API_BASE,
  WhaleRequestLog,
  WhaleRequestLogList,
  whaleApi,
} from "./WhaleShared";

type ConnectionState = "connecting" | "connected" | "disconnected";

function newestSuccess(records: WhaleRequestLog[]): WhaleRequestLog | null {
  return records.reduce<WhaleRequestLog | null>(
    (latest, record) =>
      record.status === "success" && (!latest || record.id > latest.id)
        ? record
        : latest,
    null,
  );
}

function isNewerSuccess(candidate: WhaleRequestLog, current: WhaleRequestLog): boolean {
  const candidateTime = Date.parse(candidate.finished_at || candidate.started_at);
  const currentTime = Date.parse(current.finished_at || current.started_at);
  if (Number.isFinite(candidateTime) && Number.isFinite(currentTime)) {
    return candidateTime > currentTime;
  }
  return candidate.id > current.id;
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
  const [latestSuccess, setLatestSuccess] = useState<WhaleRequestLog | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("connecting");

  const acceptSuccess = useCallback((record: WhaleRequestLog | null) => {
    if (!record) return;
    setLatestSuccess((current) =>
      !current || isNewerSuccess(record, current) ? record : current,
    );
  }, []);

  const loadSnapshot = useCallback(async () => {
    try {
      const snapshot = await whaleApi<WhaleRequestLogList>("/api/whales/request-logs");
      acceptSuccess(newestSuccess(snapshot.items));
    } catch {
      // The live stream reconnects automatically; keep the last known success visible.
      return;
    }
  }, [acceptSuccess]);

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
        if (record.status === "success") acceptSuccess(record);
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
  }, [acceptSuccess, loadSnapshot]);

  const connectionLabel = connection === "connected"
    ? "LIVE"
    : connection === "disconnected"
      ? "RECONNECTING"
      : "CONNECTING";

  return (
    <section className="pcPanel whaleRequestMonitor" aria-label="Whale request monitor">
      <header className="whaleRequestMonitorHeader">
        <div className="whaleRequestMonitorTitle">
          <span aria-hidden="true">⌁</span>
          <div>
            <h2>Request Monitor</h2>
            <p>Latest successful Polymarket request</p>
          </div>
        </div>
      </header>

      <div className="whaleTerminalFrame">
        <div className="whaleTerminalChrome" aria-hidden="true">
          <code>polymarket-whale-monitor</code>
          <b className={`whaleTerminalLive ${connection}`}>{connectionLabel}</b>
        </div>
        <div className="whaleRequestTerminal single" aria-live="polite" role="status">
          {latestSuccess ? (
            <div className="whaleTerminalEntry success">
              <div className="whaleTerminalLine">
                <time>{consoleTime(latestSuccess.finished_at || latestSuccess.started_at)}</time>
                <b className="success">[success]</b>
                <code>
                  <strong>{latestSuccess.method}</strong> {latestSuccess.url}
                  {` · HTTP ${latestSuccess.http_status ?? "—"} · ${latestSuccess.duration_ms ?? 0}ms · complete ✓`}
                </code>
              </div>
            </div>
          ) : (
            <div className="whaleTerminalEmpty">
              <span aria-hidden="true">_</span>waiting for successful request…
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
