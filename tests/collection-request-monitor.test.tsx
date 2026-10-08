import { act, cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WhaleRequestMonitorPanel } from "../app/components/WhaleRequestMonitorPanel";

class Stream {
  static instance: Stream;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  constructor() { Stream.instance = this; }
  close() {}
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("合并状态检查成功计数，保留失败及分类同步明细，迟到快照不回退计数", async () => {
  const status = {
    id: 1, scan_id: "sync-1", source: "collection", status: "success",
    method: "GET", url: "http://server:8731/api/collection/v1/status",
    query_params: {}, started_at: "2026-10-08T08:00:00Z",
    finished_at: "2026-10-08T08:00:01Z", http_status: 200, duration_ms: 1000,
    request_count: 1, error_message: null,
  };
  vi.stubGlobal("EventSource", Stream);
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items: [status] }), {
    headers: { "content-type": "application/json" },
  })));
  render(<WhaleRequestMonitorPanel />);
  expect(await screen.findByText("HTTP 200 · 1000ms · 成功累计 1 次")).toBeInTheDocument();
  const emit = (record: object) => act(() => {
    Stream.instance.onmessage?.(new MessageEvent("message", { data: JSON.stringify(record) }));
  });
  emit({ ...status, id: 2, status: "failed", http_status: 503, error_message: "服务器暂不可用" });
  emit({ ...status, id: 3, request_count: 2 });
  emit(status);
  expect(screen.getAllByText("状态检查 · GET")).toHaveLength(2);
  expect(screen.getByText("HTTP 200 · 1000ms · 成功累计 2 次")).toBeInTheDocument();
  expect(screen.queryByText("HTTP 200 · 1000ms · 成功累计 1 次")).not.toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "错误日志" })).getByText(/HTTP 503.*服务器暂不可用/)).toBeInTheDocument();
  emit({ ...status, id: 4, url: "http://server:8731/api/collection/v1/changes", query_params: { categories: "sports,esports" } });
  expect(screen.getByText("增量同步 · 体育、电竞 · GET")).toBeInTheDocument();
  emit({ ...status, started_at: "2026-10-08T09:00:00Z", finished_at: "2026-10-08T09:00:01Z" });
  expect(screen.getByText("HTTP 200 · 1000ms · 成功累计 1 次")).toBeInTheDocument();
  expect(screen.queryByText("HTTP 200 · 1000ms · 成功累计 2 次")).not.toBeInTheDocument();
});
