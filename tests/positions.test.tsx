import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import PositionsWorkspace from "../app/components/PositionsWorkspace";

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const portfolio = {
  wallet: "0x123", warning: null,
  positions: [{ asset_id: "99", title: "测试持仓", outcome: "Yes", size: "20", reserved_size: "0", available_size: "20", price: "0.6", market_value: "12", cost: null, pnl: null, status: "open", reason: null }],
  orders: [{ id: null, external_order_id: "external", asset_id: "99", title: "外部订单", outcome: "Yes", order_type: "GTC", price: "0.7", size: "2", filled_size: "0", status: "live", can_cancel: false, reason: null }],
};
const preview = { confirmation_id: "token", expires_at: "2099-01-01T00:00:00Z", wallet: "0x123", asset_id: "99", title: "测试持仓", outcome: "Yes", order_type: "FAK", size: "20", price: "0.6", estimated_proceeds: "12", estimated_fee: "0", estimated_pnl: null };

function mockApi(previewResponse = () => json(preview)) {
  const fetcher = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    void _init;
    const path = String(input);
    if (path.endsWith("/sell/preview")) return previewResponse();
    if (path.endsWith("/sell/execute")) return json({ ...portfolio.orders[0], id: 1, filled_size: "20", status: "filled" });
    return json(portfolio);
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("持仓一键卖出", () => {
  it("shows holdings without limit-order or cancellation controls", async () => {
    mockApi(); render(<PositionsWorkspace />);
    expect(await screen.findByText("测试持仓")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "持仓管理" })).toHaveAttribute("href", "/positions");
    expect(screen.getAllByText("未知")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "撤单" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("卖出方式")).not.toBeInTheDocument();
  });

  it("automatically previews sell-all FAK and submits once after explicit confirmation", async () => {
    const fetcher = mockApi(); const user = userEvent.setup(); render(<PositionsWorkspace />);
    await user.click(await screen.findByRole("button", { name: "一键卖出" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "确认真实卖出" })).toBeEnabled());
    const call = fetcher.mock.calls.find(([path]) => String(path).endsWith("/sell/preview"));
    expect(JSON.parse(call?.[1]?.body as string)).toEqual({ order_type: "FAK", sell_all: true });
    expect(fetcher.mock.calls.filter(([path]) => String(path).endsWith("/sell/execute"))).toHaveLength(0);
    expect(screen.queryByLabelText("卖出份额")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("卖出限价")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("真实卖出确认文案")).not.toBeInTheDocument();
    await user.dblClick(screen.getByRole("button", { name: "确认真实卖出" }));
    expect(await screen.findByRole("status")).toHaveTextContent("已成交");
    const submits = fetcher.mock.calls.filter(([path]) => String(path).endsWith("/sell/execute"));
    expect(submits).toHaveLength(1);
    expect(JSON.parse(submits[0][1]?.body as string)).toEqual({ confirmation_id: "token", confirmation_text: "确认真实卖出" });
  });

  it("does not submit if the user cancels", async () => {
    const fetcher = mockApi(); const user = userEvent.setup(); render(<PositionsWorkspace />);
    await user.click(await screen.findByRole("button", { name: "一键卖出" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "取消" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(fetcher.mock.calls.filter(([path]) => String(path).endsWith("/sell/execute"))).toHaveLength(0);
  });

  it("keeps execution disabled after a failed preview", async () => {
    const fetcher = mockApi(() => json({ detail: "市场当前没有可成交买盘" }, 409));
    const user = userEvent.setup(); render(<PositionsWorkspace />);
    await user.click(await screen.findByRole("button", { name: "一键卖出" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("市场当前没有可成交买盘");
    expect(screen.getByRole("button", { name: "确认真实卖出" })).toBeDisabled();
    expect(fetcher.mock.calls.filter(([path]) => String(path).endsWith("/sell/execute"))).toHaveLength(0);
  });

  it("requires a new preview when its price expires", async () => {
    const fetcher = mockApi(() => json({ ...preview, expires_at: "2000-01-01T00:00:00Z" }));
    const user = userEvent.setup(); render(<PositionsWorkspace />);
    await user.click(await screen.findByRole("button", { name: "一键卖出" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "确认真实卖出" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "确认真实卖出" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("价格已过期");
    expect(fetcher.mock.calls.filter(([path]) => String(path).endsWith("/sell/execute"))).toHaveLength(0);
  });

  it("shows only unsettled holdings without search, filters or wallet details", async () => {
    const active = { ...portfolio.positions[0], title: "Will Arsenal FC win on 2026-09-06?", size: "6.636362", available_size: "6.636362" };
    const settled = { ...portfolio.positions[0], asset_id: "100", title: "US Open ATP: Marcos Giron vs Ignacio Buse", size: "51.157893", available_size: "0", price: "0", market_value: "0", status: "settled" };
    vi.stubGlobal("fetch", vi.fn(async () => json({ ...portfolio, positions: [active, settled] })));
    render(<PositionsWorkspace />);
    expect(await screen.findByText(active.title)).toBeInTheDocument();
    expect(screen.queryByText(settled.title)).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "一键卖出" })).toHaveLength(1);
    expect(screen.queryByLabelText("搜索持仓")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("持仓状态")).not.toBeInTheDocument();
    expect(screen.queryByText(portfolio.wallet)).not.toBeInTheDocument();
    expect(screen.queryByText("EXECUTION WALLET")).not.toBeInTheDocument();
  });

  it("does not poll while hidden and resumes when visible", async () => {
    const fetcher = mockApi();
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    render(<PositionsWorkspace />);
    expect(fetcher).not.toHaveBeenCalled();
    visibility.mockReturnValue("visible");
    document.dispatchEvent(new Event("visibilitychange"));
    await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  });
});
