import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import CollectionSettingsPanel from "../app/components/CollectionSettingsPanel";

const connection = {
  host: "192.168.1.20", port: 8731, categories: { sports: { ready: false, reason: "服务器断线", cursor: 7 } },
  server: { supported_categories: ["sports", "esports"], completed_at: "2026-10-08T10:00:00Z" }, last_error: null,
};
afterEach(() => vi.unstubAllGlobals());

it("测试服务器仅检查连接，保存只提交 IP 与端口，并展示暂停原因", async () => {
  const calls: { url: string; method?: string; body?: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, method: init?.method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    return new Response(JSON.stringify(url.endsWith("/test") ? connection.server : connection), { headers: { "Content-Type": "application/json" } });
  }));
  const user = userEvent.setup();
  render(<CollectionSettingsPanel />);
  await waitFor(() => expect(screen.getByLabelText("服务器 IP")).toHaveValue("192.168.1.20"));
  expect(screen.getByText("体育：服务器断线")).toBeInTheDocument();
  await user.clear(screen.getByLabelText("服务器 IP"));
  await user.type(screen.getByLabelText("服务器 IP"), "10.0.0.8");
  await user.click(screen.getByRole("button", { name: "测试服务器连接" }));
  expect(await screen.findByRole("status")).toHaveTextContent("连接成功");
  expect(calls.filter((call) => call.method === "PUT")).toHaveLength(0);
  await user.click(screen.getByRole("button", { name: "保存服务器" }));
  await waitFor(() => expect(calls.find((call) => call.method === "PUT")?.body).toEqual({ host: "10.0.0.8", port: 8731 }));
  expect(await screen.findByRole("status")).toHaveTextContent("首次追平仅建立历史基线");
});
