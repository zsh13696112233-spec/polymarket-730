import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TakeProfitSettingsPanel, TakeProfitStatisticsPanel } from "../app/components/TakeProfitPanels";

const wallet = `0x${"a".repeat(40)}`;
const policy = { wallet, enabled: false, threshold_percent: "90", running: false,
  reason: "自动止盈已关闭", last_checked_at: null, protections: [] };
const holdings = [{ asset_id: "99", title: "测试市场", outcome: "Yes", available_size: "70" }];
const json = (payload: unknown, status = 200) => new Response(JSON.stringify(payload), { status });
const statistics = {
  summary: { realized_profit: "24", saved: "64", foregone: "6", net_impact: "58",
    filled_count: 2, pending_resolution_count: 1, pending_reconciliation_count: 1 },
  items: [{ order_id: 1, title: "已止盈市场", outcome: "Yes", threshold_percent: "90", filled_size: "70",
    net_proceeds: "64", cost: "40", realized_profit: "24", hypothetical_payout: "0", saved: "64", foregone: "0",
    status: "settled", order_status: "filled", created_at: "2026-09-13T10:00:00Z", resolved_at: "2026-09-13T11:00:00Z" }],
  total: 21,
};

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("自动止盈设置", () => {
  it("默认 90%，明确开启才保存策略，现有持仓单独加入", async () => {
    let current = policy;
    const fetcher = vi.fn(async (_path: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") current = { ...current, ...JSON.parse(String(init.body)) };
      return json(current);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    render(<TakeProfitSettingsPanel holdings={holdings} wallet={wallet} />);
    expect(await screen.findByLabelText("兑付比例阈值")).toHaveValue(90);
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(screen.getByText(/例如最高兑付 70 美元/)).toHaveTextContent("63.00 USDC");
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(0);
    await user.click(screen.getByRole("checkbox"));
    await user.clear(screen.getByLabelText("兑付比例阈值"));
    await user.type(screen.getByLabelText("兑付比例阈值"), "92");
    await user.click(screen.getByRole("button", { name: "保存止盈设置" }));
    const writes = fetcher.mock.calls.filter(([, init]) => init?.method === "PUT");
    expect(writes).toHaveLength(1);
    expect(JSON.parse(String(writes[0][1]?.body))).toEqual({ wallet, enabled: true, threshold_percent: "92" });
    await user.click(screen.getByRole("button", { name: "加入止盈保护 测试市场 Yes" }));
    expect(fetcher.mock.calls.at(-1)?.[0]).toContain("/positions/99/take-profit");
    expect(JSON.parse(String(fetcher.mock.calls.at(-1)?.[1]?.body))).toEqual({ wallet, enabled: true });
    expect(fetcher.mock.calls.some(([path]) => String(path).includes("/sell/"))).toBe(false);
  });

  it("刷新不覆盖编辑中的阈值，钱包不匹配时禁用保存", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(policy)));
    const user = userEvent.setup();
    const view = render(<TakeProfitSettingsPanel holdings={holdings} wallet={wallet} />);
    await user.clear(await screen.findByLabelText("兑付比例阈值"));
    await user.type(screen.getByLabelText("兑付比例阈值"), "85");
    await user.click(screen.getByRole("button", { name: "刷新止盈状态" }));
    expect(screen.getByLabelText("兑付比例阈值")).toHaveValue(85);
    view.rerender(<TakeProfitSettingsPanel holdings={holdings} wallet={`0x${"b".repeat(40)}`} />);
    expect(screen.getByRole("button", { name: "保存止盈设置" })).toBeDisabled();
  });

  it("启用失败保留关闭状态并显示可处理错误", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_path, init) => init?.method === "PUT"
      ? json({ detail: "持仓读取不完整，请稍后重试" }, 409) : json(policy)));
    const user = userEvent.setup();
    render(<TakeProfitSettingsPanel holdings={holdings} wallet={wallet} />);
    await user.click(await screen.findByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "保存止盈设置" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("持仓读取不完整");
    expect(screen.getByRole("status")).toHaveTextContent("自动止盈已关闭");
    expect(screen.getByRole("button", { name: "保存止盈设置" })).toBeDisabled();
  });

  it("保存后迟到的旧刷新不能把已开启状态覆盖为关闭", async () => {
    let reads = 0;
    let finishRead!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(async (_path, init) => {
      if (init?.method === "PUT") return json({ ...policy, enabled: true, reason: "运行中" });
      if (++reads === 1) return json(policy);
      return new Promise<Response>((resolve) => { finishRead = resolve; });
    }));
    const user = userEvent.setup();
    render(<TakeProfitSettingsPanel holdings={holdings} wallet={wallet} />);
    await user.click(await screen.findByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "刷新止盈状态" }));
    await user.click(screen.getByRole("button", { name: "保存止盈设置" }));
    expect(screen.getByRole("status")).toHaveTextContent("运行中");
    await act(async () => { finishRead(json(policy)); });
    expect(screen.getByRole("status")).toHaveTextContent("运行中");
    expect(screen.getByRole("checkbox")).toBeChecked();
  });

  it("请求未结束不重叠刷新，页面隐藏不轮询", async () => {
    let resolve!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>((done) => { resolve = done; }));
    vi.stubGlobal("fetch", fetcher);
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    render(<TakeProfitSettingsPanel holdings={holdings} wallet={wallet} />);
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(fetcher).not.toHaveBeenCalled();
    visibility.mockReturnValue("visible");
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
    await userEvent.click(screen.getByRole("button", { name: "刷新止盈状态" }));
    expect(fetcher).toHaveBeenCalledOnce();
    await act(async () => { resolve(json(policy)); });
  });
});

describe("止盈效果统计", () => {
  it("区分利润、保住回款和少赚，分页请求只读接口", async () => {
    const fetcher = vi.fn(async (path: RequestInfo | URL) => json(String(path).includes("offset=20")
      ? { ...statistics, items: [] } : statistics));
    vi.stubGlobal("fetch", fetcher);
    render(<TakeProfitStatisticsPanel />);
    const region = screen.getByRole("region", { name: "自动止盈效果" });
    expect(await within(region).findByText("已止盈市场 · Yes")).toBeInTheDocument();
    expect(within(region).getByText("累计保住回款").parentElement).toHaveTextContent("64.00 USDC");
    expect(within(region).getByText("累计少赚金额").parentElement).toHaveTextContent("6.00 USDC");
    expect(within(region).getByText("提前卖出净影响").parentElement).toHaveTextContent("58.00 USDC");
    expect(within(region).getByText(/待市场结算 1 笔/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(fetcher.mock.calls.some(([path]) => String(path).includes("offset=20"))).toBe(true));
    expect(fetcher.mock.calls.every(([path]) => String(path).includes("/take-profit/statistics?"))).toBe(true);
  });

  it("未结算不展示已节省金额", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ ...statistics,
      items: [{ ...statistics.items[0], hypothetical_payout: null, saved: null, foregone: null,
        resolved_at: null, status: "pending_resolution" }],
    })));
    render(<TakeProfitStatisticsPanel />);
    expect(await screen.findByText("待市场结算")).toBeInTheDocument();
    expect(screen.getByText("待结算 / 核对")).toBeInTheDocument();
    expect(screen.getByText("— / —")).toBeInTheDocument();
  });
});
