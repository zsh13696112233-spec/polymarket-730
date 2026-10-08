import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import JevSimulationWorkspace from "../app/components/JevSimulationWorkspace";

const result = {
  model: "jev-1.13.0", elapsed_ms: 1234,
  answer: { type: "choice", choice: "RECOVER_PRINCIPAL", confidence: 0.7,
    probabilities: { SELL_ALL: 0.1, RECOVER_PRINCIPAL: 0.8, HOLD: 0.05, ABSTAIN: 0.05 } },
  raw_response: { model: "jev-1.13.0" },
};
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("仅手动模拟请求，发送编辑后的信息，提交清空密钥并展示结果", async () => {
  const fetcher = vi.fn(async () => new Response(JSON.stringify(result)));
  const storage = vi.spyOn(Storage.prototype, "setItem");
  vi.stubGlobal("fetch", fetcher);
  render(<JevSimulationWorkspace />);
  const user = userEvent.setup();
  expect((screen.getByLabelText("比赛与持仓信息（可编辑）") as HTMLTextAreaElement).value).toContain("FICTIONAL SIMULATION");
  expect(fetcher).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "模拟评估" })).toBeDisabled();
  await user.type(screen.getByLabelText("TypeSafe API Key"), "fake-key");
  await user.clear(screen.getByLabelText("比赛与持仓信息（可编辑）"));
  await user.type(screen.getByLabelText("比赛与持仓信息（可编辑）"), "自定义比赛信息");
  await user.click(screen.getByRole("button", { name: "模拟评估" }));
  expect(await screen.findByText("建议：收回本金")).toBeInTheDocument();
  expect(screen.getByLabelText("TypeSafe API Key")).toHaveValue("");
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher).toHaveBeenCalledWith(expect.stringContaining("/api/ai/jev/simulate"), expect.objectContaining({
    method: "POST", body: JSON.stringify({ state: "自定义比赛信息" }),
    headers: expect.objectContaining({ "X-Typesafe-Api-Key": "fake-key" }),
  }));
  expect(storage).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "恢复默认示例" }));
  expect(screen.queryByText("建议：收回本金")).not.toBeInTheDocument();
});

it("请求期间禁止重复点击，错误可见且密钥不回填", async () => {
  let finish!: (value: Response) => void;
  const fetcher = vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; }));
  vi.stubGlobal("fetch", fetcher);
  render(<JevSimulationWorkspace />);
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("TypeSafe API Key"), "fake-key");
  await user.click(screen.getByRole("button", { name: "模拟评估" }));
  expect(screen.getByRole("button", { name: "正在评估…" })).toBeDisabled();
  finish(new Response(JSON.stringify({ detail: "API Key 无效，请检查后重试。" }), { status: 502 }));
  expect(await screen.findByRole("alert")).toHaveTextContent("API Key 无效");
  await waitFor(() => expect(screen.getByLabelText("TypeSafe API Key")).toHaveValue(""));
  expect(fetcher).toHaveBeenCalledTimes(1);
});
