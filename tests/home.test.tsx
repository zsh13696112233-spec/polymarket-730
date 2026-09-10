import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import HomeWorkspace, { buildHomeTrendData } from "../app/components/HomeWorkspace";

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
    conflict_exit_proceeds_usdc: index === 29 ? 8.75 : 0,
    conflict_exit_count: index === 29 ? 1 : 0,
    realized_pnl_usdc: index === 29 ? -3 : 0,
    realized_cost_usdc: index === 29 ? 12 : 0,
    realized_roi_percent: index === 29 ? -25 : null,
    win_count: index === 29 ? 1 : 0,
    loss_count: index === 29 ? 1 : 0,
    flat_count: 0,
    excluded_conflict_exit_count: index === 29 ? 2 : 0,
    excluded_chain_test_count: index === 29 ? 1 : 0,
    win_rate_percent: index === 29 ? 50 : null,
  }));
  return {
    as_of: "2026-08-30T08:00:00Z",
    timezone: "Asia/Shanghai",
    opportunity_counts: {
      new_account: { last_1_day: 2, last_3_days: 6, last_5_days: 10, last_7_days: 15 },
      large_amount: { last_1_day: 0, last_3_days: 1, last_5_days: 3, last_7_days: 5 },
    },
    follow_counts: {
      new_account: { last_1_day: 1, last_3_days: 2, last_5_days: 3, last_7_days: 4 },
      large_amount: { last_1_day: 0, last_3_days: 1, last_5_days: 2, last_7_days: 3 },
    },
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
      winning_pnl_usdc: 16.33827784,
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
  it.each([
    ["strategy_protected", "策略保护", "warning"],
    ["failed", "执行失败", "danger"],
  ])("首页决策展示 %s 对应的文案和颜色", async (status, label, tone) => {
    const data = overview();
    data.recent_auto_decisions[0].status = status;
    vi.stubGlobal("fetch", vi.fn(async () => json(data)));
    render(<HomeWorkspace />);
    expect(await screen.findByText(label)).toHaveClass(tone);
  });

  it("展示首页导航、核心口径、钱包、决策并切换 7/30 日趋势", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(overview())));
    const user = userEvent.setup();
    const { container } = render(<HomeWorkspace />);

    expect(await screen.findByRole("heading", { name: "首页" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "首页" })).toHaveClass("active");
    expect(screen.getByRole("link", { name: "链上监测" })).toHaveAttribute("href", "/whales");
    const flowCard = await screen.findByLabelText("今日跟单买入与今日分歧退出回款");
    expect(within(flowCard).getByText("今日跟单资金流")).toBeInTheDocument();
    expect(within(flowCard).getByText("实际跟单买入")).toBeInTheDocument();
    expect(within(flowCard).getByText("20.50 USDC")).toBeInTheDocument();
    expect(within(flowCard).getByText("分歧退出回款")).toBeInTheDocument();
    expect(within(flowCard).getByText("8.75 USDC")).toBeInTheDocument();
    expect(within(flowCard).getByText("2 次买入 · 1 次分歧退出到账")).toBeInTheDocument();
    expect(screen.getByText("-3.00 USDC")).toBeInTheDocument();
    expect(screen.getByText("已实现 ROI -25.0%")).toBeInTheDocument();
    expect(screen.getByText("134.20 USDC")).toBeInTheDocument();
    expect(screen.queryByText("钱包总资产估值")).not.toBeInTheDocument();
    expect(screen.getByText("当前持仓浮动盈亏合计")).toBeInTheDocument();
    expect(screen.getByText("获胜收益")).toHaveTextContent("获胜收益 +16.34 USDC");
    expect(screen.getByText("+16.34 USDC")).toHaveClass("profit");
    expect(screen.queryByText("当前浮盈亏")).not.toBeInTheDocument();
    expect(screen.getByText("1 胜 · 1 负 · 不含 2 笔分歧退出、1 笔链路测试")).toBeInTheDocument();
    expect(screen.getByText("北京时间自然日；跟单按买入日、胜率按仓位结束日统计，平局不计入胜率。")).toBeInTheDocument();
    const trendSummary = screen.getByLabelText("所选区间跟单汇总");
    expect(within(trendSummary).getByText("2 次")).toBeInTheDocument();
    expect(within(trendSummary).getByText("2 个")).toBeInTheDocument();
    expect(within(trendSummary).getByText("50.0%")).toBeInTheDocument();
    expect(screen.getByText("冠军归属市场")).toBeInTheDocument();
    expect(screen.getByText("已成交")).toBeInTheDocument();
    expect(container.querySelector(".homeTrendChart")).toHaveAttribute("data-point-count", "7");
    expect(container.querySelector(".homeTrendChart")).toHaveAttribute("data-mode", "finance");
    expect(screen.getByRole("button", { name: "近 7 日" })).toHaveAttribute("aria-pressed", "true");
    expect(container.querySelector(".homeChartLegend .roi")).not.toBeInTheDocument();
    const opportunities = within(screen.getByLabelText("链上发现机会"));
    expect(opportunities.getAllByRole("article")).toHaveLength(4);
    for (const [label, newCount, largeCount, newFollow, largeFollow] of [
      ["最近 1 天（今日）", 2, 0, 1, 0],
      ["最近 3 天", 6, 1, 2, 1],
      ["最近 5 天", 10, 3, 3, 2],
      ["最近 7 天", 15, 5, 4, 3],
    ] as const) {
      const card = within(opportunities.getByRole("article", { name: `${label}发现机会` }));
      expect(card.getAllByRole("term").map((term) => term.textContent)).toEqual(["新号大额", "全量超大额"]);
      expect(card.getAllByRole("definition").map((value) => value.textContent)).toEqual([`${newCount}（已跟单 ${newFollow}）`, `${largeCount}（已跟单 ${largeFollow}）`]);
    }
    const runtime = screen.getByLabelText("运行概览");
    const metrics = screen.getByLabelText("今日核心指标");
    expect(metrics.compareDocumentPosition(runtime) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByLabelText("系统状态").closest("header")).toHaveClass("pcTopbar");

    await user.click(screen.getByRole("button", { name: "次数与胜率" }));
    expect(screen.getByRole("button", { name: "次数与胜率" })).toHaveAttribute("aria-pressed", "true");
    expect(container.querySelector(".homeTrendChart")).toHaveAttribute("data-mode", "quality");
    expect(container.querySelector(".homeChartLegend .count")).toHaveTextContent("跟单次数");
    expect(container.querySelector(".homeChartLegend .winRate")).toHaveTextContent("结束仓位胜率");

    await user.click(screen.getByRole("button", { name: "近 15 日" }));
    expect(container.querySelector(".homeTrendChart")).toHaveAttribute("data-point-count", "15");

    await user.click(screen.getByRole("button", { name: "近 30 日" }));
    expect(container.querySelector(".homeTrendChart")).toHaveAttribute("data-point-count", "30");
  });

  it("正负盈亏跨越零轴时保持同一条连续序列", () => {
    const daily = overview().daily.slice(0, 4).map((item, index) => ({
      ...item,
      realized_pnl_usdc: [0, 18, -6, 0][index],
    }));

    expect(buildHomeTrendData(daily).map((item) => item.realizedPnlPlot)).toEqual([
      0,
      18,
      -6,
      0,
    ]);
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
    expect(screen.getByText("134.20 USDC")).toBeInTheDocument();
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
    expect(screen.getByText("获胜收益")).toHaveTextContent("获胜收益 +16.34 USDC");
    expect(screen.getAllByText("估值不完整").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("2 个开放仓位缺少有效买一价，已暂停总资产估算。")).toBeInTheDocument();
  });

  it("实时连接断开后不再把扫描与监测规则显示为绿色运行中", async () => {
    class FailedEventSource {
      static instance: FailedEventSource | null = null;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;

      constructor() {
        FailedEventSource.instance = this;
      }

      close() {}
    }
    vi.stubGlobal("EventSource", FailedEventSource);
    vi.stubGlobal("fetch", vi.fn(async () => json(overview())));
    const { container } = render(<HomeWorkspace />);

    expect(await screen.findByText("系统运行正常")).toBeInTheDocument();
    FailedEventSource.instance?.onerror?.(new Event("error"));

    expect(await screen.findByText("运行状态连接中断")).toBeInTheDocument();
    expect(container.querySelector(".homeTopStatus .homeStatusDot")).toHaveClass("error");
    expect(container.querySelectorAll(".homeRuleDot.enabled")).toHaveLength(0);
    expect(screen.getAllByText("连接中断")).toHaveLength(2);
  });
});

it("将进行中的市场补齐与扫描异常区分显示", async () => {
  const base = overview();
  vi.stubGlobal("fetch", vi.fn(async () => json(overview({
    wallet: { ...base.wallet, available: false },
    system: {
      ...base.system,
      status: "degraded",
      last_scan_error: "重点市场 0xabc 历史补齐中，拆单回溯不完整，未用于新增信号",
    },
  }))));
  render(<HomeWorkspace />);
  expect((await screen.findAllByText("重点市场历史补齐中")).length).toBeGreaterThan(0);
  expect(screen.queryByText("链上扫描异常")).not.toBeInTheDocument();
  const progress = screen.getByText(/重点市场 0xabc 历史补齐中/).closest(".homeAlert");
  expect(progress).toHaveClass("homeAlertInfo");
  expect(progress).toHaveAttribute("role", "status");
  const runtime = screen.getByLabelText("运行概览");
  expect(within(runtime).getAllByText("运行中")).toHaveLength(2);
  expect(within(runtime).queryByText("未运行")).not.toBeInTheDocument();
  expect(runtime.querySelectorAll(".homeRuleDot.enabled")).toHaveLength(2);
  const error = screen.getByText("交易钱包不可用").closest(".homeAlert");
  expect(error).not.toHaveClass("homeAlertInfo");
  expect(error).toHaveAttribute("role", "alert");
});
