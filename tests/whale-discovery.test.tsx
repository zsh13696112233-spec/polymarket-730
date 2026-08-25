import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import WhaleDiscoveryWorkspace from "../app/components/WhaleDiscoveryWorkspace";
import WhaleRecordsWorkspace from "../app/components/WhaleRecordsWorkspace";
import { WhaleRequestMonitorPanel } from "../app/components/WhaleRequestMonitorPanel";

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const settings = {
  enabled: true,
  window_hours: 24,
  registration_window_days: 7,
  new_account_threshold_usdc: 100000,
  large_amount_threshold_usdc: 500000,
  collect_filter_amount_usdc: 1000,
  cumulative_threshold_usdc: 10000,
  single_trade_threshold_usdc: 10000,
  min_liquidity_usdc: 5000,
  min_remaining_minutes: 30,
  max_price_delta_cents: 5,
  max_follow_amount_usdc: 200,
  default_follow_amount_usdc: 20,
  scan_interval_seconds: 60,
  last_scan_at: "2026-08-16T09:00:00Z",
  last_scan_error: null,
  consecutive_failures: 0,
  tracked_trade_count: 12,
  entry_count: 2,
  market_count: 1,
  new_account_active_count: 2,
  new_account_history_count: 0,
  large_amount_active_count: 1,
  large_amount_history_count: 0,
};

describe("巨鲸页内设置", () => {
  it("在巨鲸页面内直接展示设置，保存后立即重新扫描", async () => {
    const user = userEvent.setup();
    const bodies: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/whales/settings") && init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        bodies.push(body);
        return json({
          ...settings,
          ...body,
          single_trade_threshold_usdc: body.new_account_threshold_usdc,
          cumulative_threshold_usdc: body.new_account_threshold_usdc,
        });
      }
      if (url.endsWith("/api/whales/settings")) return json(settings);
      if (url.endsWith("/api/whales/exclusions")) return json({ total: 0, items: [] });
      if (url.endsWith("/api/whales/scan")) return json({ status: "ok" });
      if (url.includes("/api/whales/markets?")) return json({
        generated_at: "2026-08-16T09:00:00Z",
        window_start: "2026-08-15T09:00:00Z",
        stale: false,
        total: 0,
        items: [],
      });
      if (url.includes("/api/whales/history?")) return json({ total: 0, items: [] });
      return json({ detail: "not found" }, 404);
    }));

    render(<WhaleDiscoveryWorkspace />);
    await user.click(await screen.findByRole("button", { name: "打开监测设置" }));
    const days = await screen.findByRole("spinbutton", { name: "巨鲸注册窗口天数" });
    const threshold = screen.getByRole("spinbutton", { name: "新号大额买入门槛" });
    const largeThreshold = screen.getByRole("spinbutton", { name: "全量超大额买入门槛" });
    await waitFor(() => expect(days).toBeEnabled());
    await user.clear(days);
    await user.type(days, "7");
    await user.clear(threshold);
    await user.type(threshold, "25000");
    await user.clear(largeThreshold);
    await user.type(largeThreshold, "600000");
    await user.click(screen.getByRole("button", { name: "保存监测条件" }));

    await waitFor(() => expect(bodies).toContainEqual({
      registration_window_days: 7,
      new_account_threshold_usdc: 25000,
      large_amount_threshold_usdc: 600000,
    }));
    expect(await screen.findByText("巨鲸监测条件已保存，数据已重新扫描。")).toBeInTheDocument();
  });

  it("展示默认排除账户，并支持用个人页新增和移出", async () => {
    const user = userEvent.setup();
    const defaultWallet = "0x6d20c35f65d9899b6d6b74f8466e824580f9a165";
    const addedWallet = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd";
    let exclusions = [{
      proxy_wallet: defaultWallet,
      display_name: "Djdjdjekekek",
      profile_url: `https://polymarket.com/profile/${defaultWallet}`,
      hidden_entry_count: 13,
      created_at: "2026-08-23T00:00:00Z",
    }];
    const requests: Array<{ method: string; body?: unknown; url: string }> = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/api/whales/settings")) return json(settings);
      if (url.endsWith("/api/whales/exclusions") && method === "POST") {
        const body = JSON.parse(String(init?.body));
        requests.push({ method, body, url });
        const item = {
          proxy_wallet: addedWallet,
          display_name: body.label,
          profile_url: `https://polymarket.com/profile/${addedWallet}`,
          hidden_entry_count: 0,
          created_at: "2026-08-23T01:00:00Z",
        };
        exclusions = [item, ...exclusions];
        return json(item, 201);
      }
      if (url.includes("/api/whales/exclusions/") && method === "DELETE") {
        requests.push({ method, url });
        const wallet = url.split("/").at(-1);
        exclusions = exclusions.filter((item) => item.proxy_wallet !== wallet);
        return new Response(null, { status: 204 });
      }
      if (url.endsWith("/api/whales/exclusions")) {
        return json({ total: exclusions.length, items: exclusions });
      }
      if (url.includes("/api/whales/markets?")) return json({
        generated_at: "2026-08-23T01:00:00Z",
        window_start: "2026-08-22T01:00:00Z",
        stale: false,
        total: 0,
        items: [],
      });
      if (url.includes("/api/whales/history?")) return json({ total: 0, items: [] });
      return json({ detail: "not found" }, 404);
    }));

    render(<WhaleDiscoveryWorkspace />);
    await user.click(await screen.findByRole("button", { name: "打开监测设置" }));
    expect(await screen.findByRole("link", { name: "Djdjdjekekek" })).toHaveAttribute(
      "href",
      `https://polymarket.com/profile/${defaultWallet}`,
    );
    expect(screen.getByText("13")).toBeInTheDocument();

    await user.type(
      screen.getByRole("textbox", { name: "排除账户地址或个人页" }),
      `https://polymarket.com/profile/${addedWallet}`,
    );
    await user.type(screen.getByRole("textbox", { name: "排除账户显示名称" }), "测试账户");
    await user.click(screen.getByRole("button", { name: "加入排除名单" }));

    expect(await screen.findByRole("link", { name: "测试账户" })).toBeInTheDocument();
    expect(requests).toContainEqual({
      method: "POST",
      url: "http://127.0.0.1:8730/api/whales/exclusions",
      body: {
        address: `https://polymarket.com/profile/${addedWallet}`,
        label: "测试账户",
      },
    });

    await user.click(screen.getAllByRole("button", { name: "移出" })[0]);
    await waitFor(() => expect(screen.queryByRole("link", { name: "测试账户" })).not.toBeInTheDocument());
    expect(requests.some((request) => request.method === "DELETE" && request.url.endsWith(addedWallet))).toBe(true);
  });
});

const whaleMarket = {
  condition_id: `0x${"1".repeat(64)}`,
  title: "LoL: Movistar KOI vs Natus Vincere",
  icon_url: "https://example.test/market.png",
  market_slug: "lol-mkoi-navi",
  event_slug: "lol-mkoi-navi",
  polymarket_url: "https://polymarket.com/event/lol-mkoi-navi",
  tags: [{ id: "64", slug: "esports", label: "Esports", market_count: 1 }],
  end_date: "2026-08-16T12:00:00Z",
  remaining_seconds: 2700,
  end_date_is_date_only: false,
  liquidity: 30000,
  volume_24h: 100000,
  total_whale_usdc: 26000,
  whale_wallet_count: 2,
  both_sides: true,
  dominant_outcome_index: 0,
  side_imbalance_ratio: 0.62,
  sides: [
    {
      outcome_index: 0,
      outcome: "Movistar KOI",
      asset_id: "asset-a",
      current_price: 0.61,
      best_bid: 0.6,
      best_ask: 0.61,
      side_total_usdc: 16000,
      side_wallet_count: 1,
      entries: [{
        entry_id: 1,
        proxy_wallet: "0x1111111111111111111111111111111111111111",
        display_name: "Alpha Whale",
        wallet_avatar_url: "https://example.test/alpha-avatar.jpg",
        profile_url: null,
        wallet_created_at: null,
        wallet_age_days: null,
        verified_badge: false,
        taker_tier_name: null,
        gross_buy_usdc: 16000,
        gross_buy_size: 30000,
        net_size: 24000,
        current_value_usdc: 14640,
        avg_buy_price: 0.52,
        max_single_usdc: 10000,
        trade_count: 2,
        first_buy_at: "2026-08-16T08:00:00Z",
        last_buy_at: "2026-08-16T08:10:00Z",
        status: "holding",
        net_ratio: 100,
        hedged: true,
        price_delta_cents: 9,
        price_delta_percent: 17.3,
        matched_rules: ["new_account", "large_amount"],
        first_triggered_at: "2026-08-16T08:10:00Z",
        last_qualified_at: "2026-08-16T09:00:00Z",
        follow_eligible: true,
        follow_ineligible_reason: null,
      }],
    },
    {
      outcome_index: 1,
      outcome: "Natus Vincere",
      asset_id: "asset-b",
      current_price: 0.39,
      best_bid: 0.38,
      best_ask: 0.39,
      side_total_usdc: 10000,
      side_wallet_count: 1,
      entries: [{
        entry_id: 2,
        proxy_wallet: "0x2222222222222222222222222222222222222222",
        display_name: "Beta Whale",
        wallet_avatar_url: null,
        profile_url: null,
        wallet_created_at: "2025-08-16T00:00:00Z",
        wallet_age_days: 365,
        verified_badge: true,
        taker_tier_name: "Tier 2",
        gross_buy_usdc: 10000,
        gross_buy_size: 25000,
        net_size: 20000,
        current_value_usdc: 7800,
        avg_buy_price: 0.4,
        max_single_usdc: 10000,
        trade_count: 1,
        first_buy_at: "2026-08-16T08:20:00Z",
        last_buy_at: "2026-08-16T08:20:00Z",
        status: "holding",
        net_ratio: 100,
        hedged: false,
        price_delta_cents: -1,
        price_delta_percent: -2.5,
        matched_rules: ["new_account"],
        first_triggered_at: "2026-08-16T08:20:00Z",
        last_qualified_at: "2026-08-16T09:00:00Z",
        follow_eligible: true,
        follow_ineligible_reason: null,
      }],
    },
  ],
};

afterEach(() => vi.unstubAllGlobals());

describe("巨鲸请求监测面板", () => {
  it("只显示最近一条成功请求并用新的成功请求替换", async () => {
    class FakeEventSource {
      static instance: FakeEventSource | null = null;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent<string>) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      readonly url: string;

      constructor(url: string) {
        this.url = url;
        FakeEventSource.instance = this;
      }

      close() {}
    }

    const pending = {
      id: 7,
      scan_id: "scan-live",
      status: "pending",
      started_at: "2026-08-23T08:00:01Z",
      finished_at: null,
      method: "GET",
      url: "https://data-api.polymarket.com/trades",
      query_params: { side: "BUY", limit: "500" },
      http_status: null,
      duration_ms: null,
      error_type: null,
      error_message: null,
      response_excerpt: null,
    };
    const snapshotSuccess = {
      ...pending,
      id: 6,
      status: "success",
      finished_at: "2026-08-23T08:00:00Z",
      http_status: 200,
      duration_ms: 321,
    };
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.stubGlobal("fetch", vi.fn(async () => json({
      generated_at: "2026-08-23T08:00:01Z",
      total: 2,
      items: [pending, snapshotSuccess],
    })));

    render(<WhaleRequestMonitorPanel />);

    expect(await screen.findByText("成功")).toBeInTheDocument();
    expect(screen.getByText("16:00:00")).toBeInTheDocument();
    expect(screen.getByLabelText("Request Monitor")).not.toHaveClass("pcPanel");
    expect(screen.queryByRole("heading", { name: "Request Monitor" })).not.toBeInTheDocument();
    expect(screen.getByText(/https:\/\/data-api\.polymarket\.com\/trades/)).toHaveTextContent(
      "GET https://data-api.polymarket.com/trades",
    );
    expect(screen.getByText("HTTP 200 · 321ms")).toBeInTheDocument();
    expect(screen.queryByText("[pending]")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "仅失败" })).not.toBeInTheDocument();
    FakeEventSource.instance?.onopen?.(new Event("open"));
    expect(await screen.findByLabelText("请求监控状态：实时连接")).toBeInTheDocument();

    FakeEventSource.instance?.onmessage?.(new MessageEvent("message", {
      data: JSON.stringify({
        ...pending,
        status: "failed",
        finished_at: "2026-08-23T08:00:02Z",
        http_status: 503,
        duration_ms: 924,
        error_type: "HTTPError",
        error_message: "Polymarket 接口返回 503",
        response_excerpt: "upstream unavailable",
      }),
    }));
    expect(screen.getByText("16:00:00")).toBeInTheDocument();
    expect(screen.queryByText(/HTTPError/)).not.toBeInTheDocument();

    FakeEventSource.instance?.onmessage?.(new MessageEvent("message", {
      data: JSON.stringify({
        ...pending,
        id: 8,
        status: "success",
        finished_at: "2026-08-23T08:00:03Z",
        http_status: 200,
        duration_ms: 410,
      }),
    }));
    expect(await screen.findByText("16:00:03")).toBeInTheDocument();
    expect(screen.getByText("16:00:00")).toBeInTheDocument();

    FakeEventSource.instance?.onmessage?.(new MessageEvent("message", {
      data: JSON.stringify({
        ...pending,
        id: 9,
        status: "success",
        finished_at: "2026-08-23T08:00:05Z",
        http_status: 200,
        duration_ms: 280,
      }),
    }));
    expect(await screen.findByText("16:00:05")).toBeInTheDocument();

    for (const [id, second] of [[10, "06"], [11, "07"], [12, "08"]] as const) {
      FakeEventSource.instance?.onmessage?.(new MessageEvent("message", {
        data: JSON.stringify({
          ...pending,
          id,
          status: "success",
          finished_at: `2026-08-23T08:00:${second}Z`,
          http_status: 200,
          duration_ms: 250,
        }),
      }));
    }
    await waitFor(() => expect(screen.getAllByText("成功")).toHaveLength(5));
    expect(screen.queryByText("16:00:00")).not.toBeInTheDocument();
    expect(screen.getByText("16:00:08")).toBeInTheDocument();
  });

  it("没有成功请求时显示等待状态，断线后显示自动重连", async () => {
    class FailedEventSource {
      static instance: FailedEventSource | null = null;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent<string>) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;

      constructor() {
        FailedEventSource.instance = this;
        window.setTimeout(() => this.onerror?.(new Event("error")), 0);
      }

      close() {}
    }
    vi.stubGlobal("EventSource", FailedEventSource);
    vi.stubGlobal("fetch", vi.fn(async () => json({
      generated_at: "2026-08-23T08:00:01Z",
      total: 0,
      items: [],
    })));

    render(<WhaleRequestMonitorPanel />);

    expect(await screen.findByLabelText("请求监控状态：正在重连")).toBeInTheDocument();
    expect(screen.getByText("暂无成功请求，收到新数据后会自动更新。")).toBeInTheDocument();
    FailedEventSource.instance?.onopen?.(new Event("open"));
    expect(await screen.findByLabelText("请求监控状态：实时连接")).toBeInTheDocument();
  });
});

describe("巨鲸持仓页", () => {
  it("按双规则切换当前持仓和永久历史记录", async () => {
    const requests: string[] = [];
    const historyItem = {
      entry_id: 9,
      rule_type: "new_account",
      matched_rules: ["new_account", "large_amount"],
      proxy_wallet: "0x9999999999999999999999999999999999999999",
      display_name: "Settled Whale",
      wallet_created_at: "2026-08-15T00:00:00Z",
      wallet_age_days: 1,
      title: "Settled market",
      outcome: "Yes",
      market_slug: "settled-market",
      event_slug: "settled-market",
      gross_buy_usdc: 600000,
      gross_buy_size: 1000000,
      avg_buy_price: 0.6,
      net_size: 0,
      first_buy_at: "2026-08-16T07:30:00Z",
      first_triggered_at: "2026-08-16T08:00:00Z",
      last_qualified_at: "2026-08-16T08:30:00Z",
      inactive_at: "2026-08-16T09:00:00Z",
      inactive_reason: "market_closed",
      threshold_usdc_snapshot: 100000,
      registration_days_snapshot: 7,
      settlement_price: 1,
      settled_at: "2026-08-16T09:00:00Z",
      hold_to_settlement_pnl_usdc: 400000,
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      requests.push(url);
      if (url.endsWith("/api/whales/settings")) return json(settings);
      if (url.includes("/api/whales/markets?")) return json({
        generated_at: "2026-08-16T09:00:00Z",
        window_start: "2026-08-15T09:00:00Z",
        stale: false,
        total: 1,
        items: [whaleMarket],
      });
      if (url.includes("/api/whales/history?rule=new_account")) {
        return json({ total: 1, items: [historyItem] });
      }
      if (url.includes("/api/whales/history?rule=large_amount")) {
        return json({ total: 0, items: [] });
      }
      return json({ detail: "not found" }, 404);
    }));

    const user = userEvent.setup();
    render(<WhaleDiscoveryWorkspace />);
    const settledWalletLink = await screen.findByRole("link", { name: /Settled Whale/ });
    expect(screen.getByText(/市场已结算/)).toBeInTheDocument();
    const recentHistory = screen.getByLabelText("新号大额最近历史");
    expect(recentHistory).toHaveTextContent("买入价 0.6");
    expect(recentHistory.querySelector("time")).toHaveTextContent("触发 08/16 16:00");
    await user.click(screen.getByRole("button", { name: "展开完整历史" }));
    expect(await screen.findByLabelText("新号大额历史记录")).toBeInTheDocument();
    expect(screen.getAllByText(/^建仓 /).length).toBeGreaterThan(0);
    expect(settledWalletLink).toHaveAttribute(
      "href",
      "https://polymarket.com/profile/0x9999999999999999999999999999999999999999",
    );

    await user.click(screen.getByRole("button", { name: /全量超大额/ }));
    await waitFor(() => expect(requests.some((url) => url.includes("markets?rule=large_amount"))).toBe(true));
    expect(await screen.findByLabelText("全量超大额最近历史")).toHaveTextContent("该规则暂无历史触发记录");
    expect(screen.queryByLabelText("新号大额历史记录")).not.toBeInTheDocument();
  });

  it("按钱包分组持仓，并在收益预览后允许确认买入", async () => {
    const secondMarket = {
      ...whaleMarket,
      condition_id: `0x${"2".repeat(64)}`,
      title: "Will Bitcoin reach $200,000?",
      sides: [{
        ...whaleMarket.sides[0],
        asset_id: "asset-c",
        outcome: "Yes",
        current_price: 0.5,
        entries: [{
          ...whaleMarket.sides[0].entries[0],
          entry_id: 3,
          net_size: 10000,
          current_value_usdc: 5000,
          last_buy_at: "2026-08-16T08:30:00Z",
        }],
      }],
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/whales/settings")) return json(settings);
      if (url.includes("/api/whales/follow/preview")) return json({
        confirmation_id: "confirmation-token",
        expires_at: "2026-08-16T09:05:00Z",
        asset_id: "asset-a",
        condition_id: whaleMarket.condition_id,
        title: whaleMarket.title,
        outcome: "Movistar KOI",
        outcome_index: 0,
        neg_risk: false,
        amount_usdc: 20,
        best_ask: 0.61,
        worst_price: 0.64,
        tick_size: 0.01,
        minimum_order_usdc: 3.2,
        estimated_shares: 31.25,
        estimated_fee_usdc: 0.4,
        total_cost_usdc: 20.4,
        profit_ratio_percent: 53.2,
        max_loss_usdc: 20.4,
        winning_payout_usdc: 31.25,
        winning_profit_usdc: 10.85,
        immediate_exit_price: 0.57,
        immediate_exit_proceeds_usdc: 17.8125,
        immediate_exit_fee_usdc: 0.2,
        immediate_exit_pnl_usdc: -2.7875,
        immediate_exit_pnl_percent: -13.66,
        immediate_exit_unavailable_reason: null,
        whale_avg_price: 0.52,
        whale_profit_ratio_percent: 87.8,
        profit_ratio_gap_percent: -34.6,
        price_delta_cents: 9,
        price_delta_warning: true,
        reserve_warning: false,
        available_balance_usdc: 300,
      });
      if (url.includes("/api/whales/markets?")) return json({
        generated_at: "2026-08-16T09:00:00Z",
        window_start: "2026-08-15T09:00:00Z",
        stale: true,
        total: 2,
        items: [whaleMarket, secondMarket],
      });
      if (url.includes("/api/whales/history?")) return json({ total: 0, items: [] });
      return json({ detail: "not found" }, 404);
    }));

    const user = userEvent.setup();
    const { container } = render(<WhaleDiscoveryWorkspace />);

    expect(await screen.findByRole("heading", { name: "链上大额资金监测" })).toBeInTheDocument();
    const requestMonitor = screen.getByLabelText("Request Monitor");
    expect(requestMonitor.closest(".whaleDashboardMain")).not.toBeNull();
    expect(requestMonitor.closest(".whaleDashboardRail")).toBeNull();
    expect(await screen.findByRole("heading", { name: "分歧市场" })).toBeInTheDocument();
    expect(screen.getByLabelText("巨鲸分歧市场")).toHaveTextContent("1 个市场");
    expect(screen.getByLabelText("Movistar KOI方向")).toHaveTextContent("16.0K USDC");
    expect(screen.getByLabelText("Natus Vincere方向")).toHaveTextContent("10.0K USDC");
    expect(screen.getAllByText("反向巨鲸")).toHaveLength(2);
    expect(screen.getByText("Will Bitcoin reach $200,000?")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "只看分歧持仓" }));
    expect(await screen.findByText("分歧筛选 · 2 个钱包 · 2 个持仓")).toBeInTheDocument();
    expect(screen.queryByText("Will Bitcoin reach $200,000?")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "显示全部持仓" }));
    expect((await screen.findAllByText("24小时买入")).length).toBeGreaterThan(0);
    expect(await screen.findByText("2 个钱包 · 3 个持仓")).toBeInTheDocument();
    expect(screen.getAllByText("监测建仓").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/首次触发/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Alpha Whale")).toHaveLength(2);
    expect(screen.getByText("2 个持仓")).toBeInTheDocument();
    expect(screen.getByText("注册日期 2025-08-16 · 已认证")).toBeInTheDocument();
    expect(screen.getByText("24000.00")).toBeInTheDocument();
    expect(screen.getByText("14.6K USDC")).toBeInTheDocument();
    expect(screen.getAllByText("Movistar KOI").length).toBeGreaterThan(0);
    expect(container.querySelector('img.whaleWalletAvatar[src="https://example.test/alpha-avatar.jpg"]')).not.toBeNull();
    expect(screen.getByLabelText("Beta Whale 钱包默认头像")).toHaveTextContent("BW");
    expect(container.querySelector('img.whaleHoldingMarketIcon[src="https://example.test/market.png"]')).not.toBeNull();

    expect(screen.queryByLabelText("搜索巨鲸持仓")).not.toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "跟单" })[0]);
    const execute = screen.getByRole("button", { name: "等待收益预览" });
    expect(execute).toBeDisabled();

    expect(await screen.findByText("总成本", {}, { timeout: 2000 })).toBeInTheDocument();
    expect(screen.getByText("净赚 10.85 USDC")).toBeInTheDocument();
    expect(screen.getByText("按当前盘口立即卖出")).toBeInTheDocument();
    expect(screen.getByText("-2.79 USDC")).toBeInTheDocument();
    expect(screen.getByText("最大亏损")).toBeInTheDocument();
    expect(screen.queryByLabelText("真实买入确认文案")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "确认买入 20.00 USDC" })).toBeEnabled());
  });

  it("同一钱包持有同一市场两侧时保留两条并提示钱包对冲，不误报巨鲸分歧", async () => {
    const dualMarket = {
      ...whaleMarket,
      sides: [
        whaleMarket.sides[0],
        {
          ...whaleMarket.sides[1],
          entries: [{
            ...whaleMarket.sides[1].entries[0],
            proxy_wallet: whaleMarket.sides[0].entries[0].proxy_wallet,
            display_name: "Alpha Whale",
          }],
        },
      ],
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/whales/settings")) return json(settings);
      if (url.includes("/api/whales/markets?")) return json({
        generated_at: "2026-08-16T09:00:00Z",
        window_start: "2026-08-15T09:00:00Z",
        stale: false,
        total: 1,
        items: [dualMarket],
      });
      if (url.includes("/api/whales/history?")) return json({ total: 0, items: [] });
      return json({ detail: "not found" }, 404);
    }));

    render(<WhaleDiscoveryWorkspace />);

    expect(await screen.findByText("1 个钱包 · 2 个持仓")).toBeInTheDocument();
    expect(screen.getAllByText("钱包对冲")).toHaveLength(2);
    expect(screen.getByRole("heading", { name: "分歧市场" })).toBeInTheDocument();
    expect(screen.getByLabelText("巨鲸分歧市场")).toHaveTextContent("0 个市场");
    expect(screen.getByText("暂无分歧市场")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "只看分歧持仓" })).not.toBeInTheDocument();
    expect(screen.queryByText("反向巨鲸")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "跟单" })).toHaveLength(2);
  });
});

describe("巨鲸命中率统计", () => {
  it("通过第三页签展示总览、规则对比、趋势和可筛选明细", async () => {
    const requests: string[] = [];
    const metrics = {
      settled_count: 2,
      effective_sample_count: 2,
      hit_count: 1,
      miss_count: 1,
      special_count: 0,
      pending_count: 1,
      hit_rate_percent: 50,
      theoretical_cost_usdc: 300000,
      theoretical_payout_usdc: 360000,
      theoretical_pnl_usdc: 60000,
      theoretical_roi_percent: 20,
      weighted_avg_buy_price: 0.55,
      break_even_rate_percent: 55,
      edge_percentage_points: -5,
      wallet_count: 2,
      market_count: 2,
    };
    const statistics = {
      generated_at: "2026-08-23T09:00:00Z",
      coverage_start: "2026-08-01T08:00:00Z",
      range: "all",
      range_start: null,
      range_end: "2026-08-23T09:00:00Z",
      overall: metrics,
      new_account: metrics,
      large_amount: { ...metrics, hit_rate_percent: 100, hit_count: 1, miss_count: 0, effective_sample_count: 1 },
      dual_match: { ...metrics, hit_rate_percent: null, hit_count: 0, miss_count: 0, effective_sample_count: 0 },
      trend: [{ key: "2026-08", label: "2026-08", metrics }],
      amount_bands: [
        { key: "lt_100k", label: "< 10万", metrics },
        { key: "100k_500k", label: "10万–50万", metrics: { ...metrics, hit_rate_percent: null, effective_sample_count: 0 } },
        { key: "500k_1m", label: "50万–100万", metrics: { ...metrics, hit_rate_percent: null, effective_sample_count: 0 } },
        { key: "gte_1m", label: "≥ 100万", metrics: { ...metrics, hit_rate_percent: null, effective_sample_count: 0 } },
      ],
    };
    const signal = {
      entry_id: 19,
      result: "hit",
      matched_rules: ["new_account", "large_amount"],
      proxy_wallet: "0x9999999999999999999999999999999999999999",
      display_name: "Winning Whale",
      profile_url: "https://polymarket.com/profile/0x9999999999999999999999999999999999999999",
      wallet_created_at: "2026-08-01T00:00:00Z",
      wallet_age_days_at_trigger: 2,
      condition_id: whaleMarket.condition_id,
      title: "Winning market",
      outcome: "Yes",
      market_slug: "winning-market",
      event_slug: "winning-market",
      polymarket_url: "https://polymarket.com/event/winning-market",
      gross_buy_usdc: 120000,
      gross_buy_size: 200000,
      avg_buy_price: 0.6,
      settlement_price: 1,
      theoretical_payout_usdc: 200000,
      theoretical_pnl_usdc: 80000,
      theoretical_roi_percent: 66.7,
      first_triggered_at: "2026-08-03T08:00:00Z",
      settled_at: "2026-08-22T08:00:00Z",
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      requests.push(url);
      if (url.endsWith("/api/whales/settings")) return json(settings);
      if (url.includes("/api/whales/markets?")) return json({ generated_at: "2026-08-23T09:00:00Z", window_start: "2026-08-22T09:00:00Z", stale: false, total: 0, items: [] });
      if (url.includes("/api/whales/history?")) return json({ total: 0, items: [] });
      if (url.endsWith("/api/whales/request-logs")) return json({ generated_at: "2026-08-23T09:00:00Z", total: 0, items: [] });
      if (url.includes("/api/whales/statistics/signals?")) return json({ total: 1, items: [signal] });
      if (url.includes("/api/whales/statistics?")) return json({ ...statistics, range: url.includes("range=7d") ? "7d" : "all" });
      return json({ detail: "not found" }, 404);
    }));

    const user = userEvent.setup();
    render(<WhaleDiscoveryWorkspace />);
    await user.click(await screen.findByRole("button", { name: /统计/ }));

    expect(await screen.findByRole("heading", { name: "巨鲸信号表现" })).toBeInTheDocument();
    expect(screen.queryByLabelText("巨鲸监测设置")).not.toBeInTheDocument();
    expect(screen.getByLabelText("整体统计")).toHaveTextContent("50.0%");
    expect(screen.getByLabelText("规则表现对比")).toHaveTextContent("样本不足");
    expect(await screen.findByText("Winning Whale ↗")).toBeInTheDocument();
    expect(screen.getByText("持有至结算，未计手续费")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "近 7 天" }));
    await waitFor(() => expect(requests.some((url) => url.includes("statistics?range=7d"))).toBe(true));
    await user.selectOptions(screen.getByLabelText("统计结果筛选"), "hit");
    await waitFor(() => expect(requests.some((url) => url.includes("result=hit"))).toBe(true));
  });
});

describe("巨鲸跟单记录页", () => {
  it("展示汇总、无法估值状态，并在卖出确认前禁用按钮", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/whales/positions?")) return json({ items: [{
        id: 9,
        asset_id: "asset-a",
        condition_id: whaleMarket.condition_id,
        title: whaleMarket.title,
        outcome: "Movistar KOI",
        outcome_index: 0,
        market_slug: "lol-mkoi-navi",
        event_slug: "lol-mkoi-navi",
        icon_url: null,
        source_wallet: "0x1111111111111111111111111111111111111111",
        source_whale_avg_price: 0.52,
        size: 31.25,
        avg_cost_price: 0.6528,
        cost_usdc: 20.4,
        current_price: null,
        market_value_usdc: null,
        unrealized_pnl: null,
        realized_pnl: 0,
        total_pnl: null,
        lifetime_bought_size: 31.25,
        lifetime_bought_usdc: 20,
        lifetime_sold_size: 0,
        lifetime_sold_usdc: 0,
        lifetime_fee_usdc: 0.4,
        status: "open",
        opened_at: "2026-08-16T09:00:00Z",
        closed_at: null,
        valuation_status: "unavailable",
      }] });
      if (url.includes("/api/whales/records?")) return json({
        items: [],
        summary: {
          total_invested_usdc: 20.4,
          total_proceeds_usdc: 0,
          total_fee_usdc: 0.4,
          realized_pnl: 0,
          unrealized_pnl: null,
          total_pnl: null,
          open_position_count: 1,
          closed_position_count: 0,
          win_count: 0,
          loss_count: 0,
          win_rate_percent: null,
          average_profit_ratio_percent: null,
        },
      });
      if (url.includes("/sell/preview")) return json({
        confirmation_id: "sell-token",
        expires_at: "2026-08-16T09:05:00Z",
        size: 31.25,
        best_bid: 0.6,
        worst_price: 0.57,
        minimum_order_size: 5,
        estimated_proceeds_usdc: 17.81,
        estimated_fee_usdc: 0.2,
        cost_basis_usdc: 20.4,
        estimated_pnl_usdc: -2.79,
        estimated_pnl_percent: -13.7,
      });
      return json({ detail: "not found" }, 404);
    }));

    const user = userEvent.setup();
    render(<WhaleRecordsWorkspace />);

    expect(await screen.findByText("无法估值")).toBeInTheDocument();
    expect(screen.getAllByText("20.40 USDC").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "一键卖出" }));
    const execute = screen.getByRole("button", { name: "确认卖出" });
    expect(execute).toBeDisabled();
    expect(await screen.findByText("预计回收", {}, { timeout: 2000 })).toBeInTheDocument();
    expect(execute).toBeDisabled();
  });
});
