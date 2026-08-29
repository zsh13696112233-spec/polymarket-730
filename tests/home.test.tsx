import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import HomeWorkspace from "../app/components/HomeWorkspace";

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function overview(overrides: Record<string, unknown> = {}) {
  const daily = Array.from({ length: 30 }, (_, index) => ({
    date: `2026-08-${String(index + 1).padStart(2, "0")}`,
    buy_amount_usdc: index === 29 ? 20.5 : 0,
    buy_count: index === 29 ? 2 : 0,
    realized_pnl_usdc: index === 29 ? -3 : 0,
    realized_cost_usdc: index === 29 ? 12 : 0,
    realized_roi_percent: index === 29 ? -25 : null,
    win_count: index === 29 ? 1 : 0,
    loss_count: index === 29 ? 1 : 0,
    flat_count: 0,
  }));
  return {
    as_of: "2026-08-30T08:00:00Z",
    timezone: "Asia/Shanghai",
    range_start: "2026-08-01",
    range_end: "2026-08-30",
    system: {
      status: "healthy",
      enabled: true,
      last_scan_at: "2026-08-30T07:59:30Z",
      last_scan_error: null,
      consecutive_failures: 0,
      scan_interval_seconds: 60,
      rules: [
        { rule: "new_account", enabled: true, auto_follow_enabled: true, active_wallet_count: 3 },
        { rule: "large_amount", enabled: true, auto_follow_enabled: false, active_wallet_count: 2 },
      ],
    },
    today: {
      ...daily[29],
      unrealized_pnl_usdc: 4.2,
      win_rate_percent: 50,
    },
    wallet: {
      status: "ready",
      available: true,
      cash_balance_usdc: 100,
      open_cost_usdc: 30,
      market_value_usdc: 34.2,
      total_assets_usdc: 134.2,
      unrealized_pnl_usdc: 4.2,
      cash_reserve_usdc: 60,
      available_cash_usdc: 40,
      open_position_count: 2,
      last_balance_at: "2026-08-30T07:59:20Z",
      balance_stale: false,
      valuation_complete: true,
      unpriced_position_count: 0,
      last_error: null,
    },
    daily,
    recent_auto_decisions: [{
      id: 1,
      created_at: "2026-08-30T07:55:00Z",
      selected_rule: "new_account",
      matched_rules: ["new_account"],
      title: "冠军归属市场",
      outcome: "Yes",
      market_slug: "champion",
      event_slug: "champion-event",
      configured_amount_usdc: 10,
      filled_usdc: 10.1,
      status: "bought",
      reason: "自动跟单买入已执行",
      is_risk_exit: false,
    }],
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("首页运行与跟单看板", () => {
  it("展示首页导航、核心口径、钱包、决策并切换 7/30 日趋势", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(overview())));
    const user = userEvent.setup();
    const { container } = render(<HomeWorkspace />);

    expect(await screen.findByRole("heading", { name: "首页" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "首页" })).toHaveClass("active");
    expect(screen.getByRole("link", { name: "链上监测" })).toHaveAttribute("href", "/whales");
    expect(screen.getByText("20.50 USDC")).toBeInTheDocument();
    expect(screen.getByText("-3.00 USDC")).toBeInTheDocument();
    expect(screen.getByText("已实现 ROI -25.0%")).toBeInTheDocument();
    expect(screen.getAllByText("134.20 USDC")).toHaveLength(2);
    expect(screen.getByText("冠军归属市场")).toBeInTheDocument();
    expect(screen.getByText("已成交")).toBeInTheDocument();
    expect(container.querySelector(".homeTrendChart")).toHaveAttribute("data-point-count", "7");
    expect(screen.getByRole("button", { name: "近 7 日" })).toHaveAttribute("aria-pressed", "true");
    expect(container.querySelector(".homeChartLegend .roi")).not.toBeInTheDocument();
    const runtime = screen.getByLabelText("运行概览");
    const metrics = screen.getByLabelText("今日核心指标");
    expect(runtime.compareDocumentPosition(metrics) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "近 15 日" }));
    expect(container.querySelector(".homeTrendChart")).toHaveAttribute("data-point-count", "15");

    await user.click(screen.getByRole("button", { name: "近 30 日" }));
    expect(container.querySelector(".homeTrendChart")).toHaveAttribute("data-point-count", "30");
  });

  it("余额过期时在冷却窗口内只刷新一次，失败后保留缓存并告警", async () => {
    const stale = overview({
      wallet: {
        ...overview().wallet,
        balance_stale: true,
        last_balance_at: "2026-08-30T07:55:00Z",
      },
    });
    const requests: Array<{ url: string; method: string }> = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, method: init?.method || "GET" });
      if (url.endsWith("/api/execution-account/balance/refresh")) return json({ detail: "RPC 暂不可用" }, 502);
      return json(stale);
    }));

    render(<HomeWorkspace />);

    expect(await screen.findByText("余额刷新失败，已保留缓存数据")).toBeInTheDocument();
    expect(screen.queryByText("钱包余额已过期")).not.toBeInTheDocument();
    expect(screen.getAllByText("134.20 USDC")).toHaveLength(2);
    await waitFor(() => expect(requests.filter((item) => item.method === "POST")).toHaveLength(1));
  });

  it("缺价时不显示误导性的总资产", async () => {
    const incomplete = overview({
      wallet: {
        ...overview().wallet,
        market_value_usdc: null,
        total_assets_usdc: null,
        unrealized_pnl_usdc: null,
        valuation_complete: false,
        unpriced_position_count: 2,
      },
      today: { ...overview().today, unrealized_pnl_usdc: null },
    });
    vi.stubGlobal("fetch", vi.fn(async () => json(incomplete)));

    render(<HomeWorkspace />);

    expect(await screen.findByText("持仓估值不完整")).toBeInTheDocument();
    expect(screen.getAllByText("估值不完整").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("2 个开放仓位缺少有效买一价，已暂停总资产估算。")).toBeInTheDocument();
  });
});
