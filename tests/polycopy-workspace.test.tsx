import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PolyCopyWorkspace from "../app/components/PolyCopyWorkspace";

const wallet = {
  id: 1,
  address: "0x1111111111111111111111111111111111111111",
  proxy_wallet: "0x1111111111111111111111111111111111111111",
  label: "策略一",
  wallet_role: "tracked",
  enabled: true,
  status: "ok",
  last_success_at: "2026-08-06T08:00:00Z",
  last_error: null,
};

const subscription = {
  id: 10,
  tracked_wallet_id: 1,
  tracked_wallet_label: "策略一",
  enabled: true,
  state: "active",
  strategy_mode: "normal",
  copy_ratio_percent: 10,
  position_cap_usdc: 20,
  large_increase_threshold_usdc: 100,
  base_entry_threshold_usdc: 100,
  base_entry_ratio_percent: 10,
  tier_one_threshold_usdc: 50000,
  tier_one_ratio_percent: 0.1,
  tier_two_threshold_usdc: 100000,
  tier_two_ratio_percent: 0.2,
  total_exposure_cap_usdc: 160,
  market_slippage_cents: 5,
  open_exposure_usdc: 25,
  daily_bought_usdc: 8,
  daily_realized_pnl: 2,
  last_error: null,
};

const account = {
  wallet_id: 9,
  signer_address: "0x9999999999999999999999999999999999999999",
  funder_address: "0x9999999999999999999999999999999999999999",
  signature_type: 3,
  credentials_configured: true,
  status: "ready",
  budget_usdc: 400,
  cash_reserve_usdc: 240,
  max_total_exposure_usdc: 160,
  daily_buy_limit_usdc: 80,
  daily_loss_limit_usdc: 40,
  auto_redeem: true,
  collateral_balance: 300,
  last_balance_at: "2026-08-06T08:00:00Z",
  last_error: null,
};

const filledOrder = {
  id: 101,
  asset_id: "asset-one",
  side: "BUY",
  requested_size: 20,
  requested_usdc: 10,
  leader_purchase_usdc: 100,
  proportional_target_usdc: 10,
  filled_size: 20,
  filled_usdc: 9.8,
  fee_usdc: 0.02,
  reference_price: 0.48,
  limit_price: 0.5,
  average_fill_price: 0.49,
  status: "filled",
  execution_provider: "unified_sdk",
  fills: [],
  reason: null,
  created_at: "2026-08-06T08:00:00Z",
  tracked_wallet_id: 1,
  tracked_wallet_label: "策略一",
  tracked_wallet_address: wallet.proxy_wallet,
  title: "Will Team A win?",
  outcome: "Yes",
  event_slug: "team-a-win",
};

const filledActivity = {
  activity_id: "order:101",
  activity_type: "order",
  source_id: 101,
  operation: "BUY",
  asset_id: "asset-one",
  requested_size: 20,
  requested_usdc: 10,
  leader_purchase_usdc: 100,
  proportional_target_usdc: 10,
  executed_size: 20,
  executed_usdc: 9.8,
  fee_usdc: 0.02,
  execution_price: 0.49,
  realized_pnl: null,
  status: "filled",
  reason: null,
  execution_provider: "unified_sdk",
  transaction_id: null,
  transaction_hash: null,
  fills: [],
  force_buy_eligible: false,
  force_buy_unavailable_reason: null,
  force_buy_order_id: null,
  force_buy_status: null,
  activity_at: "2026-08-06T08:00:00Z",
  tracked_wallet_id: 1,
  tracked_wallet_label: "策略一",
  tracked_wallet_address: wallet.proxy_wallet,
  title: "Will Team A win?",
  outcome: "Yes",
  event_slug: "team-a-win",
};

const dailyRealizedPnl = Array.from({ length: 31 }, (_, index) => {
  const date = new Date(Date.UTC(2026, 6, 7 + index)).toISOString().slice(0, 10);
  const realizedPnl = index === 0 ? 5 : index === 29 ? 3 : index === 30 ? -1 : 0;
  const boughtUsdc = index === 0 ? 20 : index === 29 ? 10 : index === 30 ? 5 : 0;
  return { date, realized_pnl: realizedPnl, bought_usdc: boughtUsdc };
});

const overview = {
  live_copy_enabled: true,
  account,
  totals: {
    collateral_balance: 300,
    available_capacity_usdc: 60,
    open_exposure_usdc: 25,
    daily_bought_usdc: 8,
    total_pnl: 7.5,
    valuation_complete: true,
    unpriced_positions: 0,
  },
  daily_realized_pnl: dailyRealizedPnl,
  strategies: [{
    subscription,
    wallet,
    portfolio: {
      open_cost_usdc: 25,
      market_value_usdc: 30,
      unrealized_pnl: 5,
      realized_pnl: 2.5,
      total_pnl: 7.5,
      valuation_complete: true,
      unpriced_positions: 0,
      valued_at: "2026-08-06T08:00:00Z",
    },
    lifetime_bought_usdc: 42.5,
    lifetime_copy_order_count: 12,
    open_positions: 1,
    stale: false,
  }],
  recent_orders: [filledOrder],
  recent_activities: [filledActivity],
  as_of: "2026-08-06T08:00:00Z",
};

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

describe("PolyCopy workspace", () => {
  const requests: string[] = [];
  const requestBodies: unknown[] = [];
  let recordsResponse: unknown;

  beforeEach(() => {
    requests.length = 0;
    requestBodies.length = 0;
    recordsResponse = {
      items: [{ ...filledActivity, status: "blocked", reason: "每日买入上限已用完" }],
      next_cursor: null,
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push(url);
      if (init?.body) requestBodies.push(JSON.parse(String(init.body)));
      if (url.includes("/api/copy-trading/overview")) return json(overview);
      if (url.endsWith("/api/wallets")) return json([wallet]);
      if (url.includes("/api/copy-trading/positions")) return json({
        items: [{
          id: 1,
          asset_id: "asset-one",
          title: "Will Team A win?",
          outcome: "Yes",
          event_slug: "team-a-win",
          tracked_wallet_id: 1,
          tracked_wallet_label: "策略一",
          tracked_wallet_address: wallet.proxy_wallet,
          attributed_size: 20,
          attributed_cost: 10,
          realized_pnl: 0,
          status: "open",
          average_entry_price: 0.5,
          current_bid: null,
          current_value: null,
          unrealized_pnl: null,
          unrealized_pnl_percent: null,
          total_pnl: null,
          lifetime_bought_usdc: 10,
          lifetime_sold_usdc: 0,
          valuation_status: "unavailable",
          updated_at: "2026-08-06T08:00:00Z",
        }],
        portfolio: { open_cost_usdc: 10, market_value_usdc: null, unrealized_pnl: null, realized_pnl: 0, total_pnl: null, valuation_complete: false, unpriced_positions: 1, valued_at: "2026-08-06T08:00:00Z" },
        as_of: "2026-08-06T08:00:00Z",
      });
      if (url.includes("/api/copy-trading/activities")) return json(recordsResponse);
      return json({ ...subscription, enabled: false, state: "exit_only" });
    }));
    vi.stubGlobal("confirm", vi.fn(() => true));
  });

  afterEach(() => vi.unstubAllGlobals());

  it("展示全局资金、策略状态和最近记录", async () => {
    render(<PolyCopyWorkspace view="overview" />);
    expect(await screen.findByText("执行钱包余额")).toBeInTheDocument();
    expect(screen.getAllByText("策略一").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Will Team A win?").length).toBeGreaterThan(0);
    expect(screen.getAllByText("+$7.50").length).toBeGreaterThan(0);
    expect(screen.getByText("累计跟单")).toBeInTheDocument();
    expect(screen.getByText("12 笔")).toBeInTheDocument();
    expect(screen.getAllByText("源钱包交易金额").length).toBeGreaterThan(0);
    expect(screen.getAllByText("按比例目标金额").length).toBeGreaterThan(0);
    expect(screen.getAllByText("执行预算").length).toBeGreaterThan(0);
    expect(screen.getAllByText("实际执行金额").length).toBeGreaterThan(0);
    expect(screen.getAllByText("$100.00").length).toBeGreaterThan(0);
    expect(screen.getByText("普通跟单")).toBeInTheDocument();
    expect(screen.getByText("10% · $20.00")).toBeInTheDocument();
    const totalInvestment = screen.getByText("钱包总投入").closest("div");
    expect(totalInvestment).not.toBeNull();
    expect(within(totalInvestment!).getByText("$42.50")).toBeInTheDocument();
  });

  it("熔断跳过记录可预览并确认一次性强制买入", async () => {
    const user = userEvent.setup();
    const skippedActivity = {
      ...filledActivity,
      activity_id: "order:190",
      source_id: 190,
      requested_size: 0,
      requested_usdc: 0,
      executed_size: 0,
      executed_usdc: 0,
      status: "skipped",
      reason: "已触发执行钱包当日亏损熔断",
      force_buy_eligible: true,
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push(url);
      if (init?.body) requestBodies.push(JSON.parse(String(init.body)));
      if (url.endsWith("/force-buy/preview")) return json({
        confirmation_id: "force-confirmation-id-1234567890",
        source_order_id: 190,
        title: "Will Team A win?",
        outcome: "Yes",
        proportional_target_usdc: 0.5,
        minimum_order_usdc: 5,
        minimum_adjusted: true,
        executable_usdc: 5,
        best_ask: 0.48,
        worst_price: 0.53,
        expires_at: "2026-08-06T08:05:00Z",
      });
      if (url.endsWith("/force-buy/execute")) return json({ id: 191, status: "filled" });
      if (url.includes("/api/copy-trading/overview")) return json({ ...overview, recent_activities: [skippedActivity] });
      if (url.endsWith("/api/wallets")) return json([wallet]);
      return json({ items: [], next_cursor: null });
    }));

    render(<PolyCopyWorkspace view="overview" />);
    const forceButtons = await screen.findAllByRole("button", { name: "强制买入" });
    await user.click(forceButtons[0]);
    expect(await screen.findByRole("dialog", { name: "确认强制买入" })).toBeInTheDocument();
    expect(screen.getByText("已按市场最小份数提高执行预算。")).toBeInTheDocument();
    expect(screen.getByText(/单仓、总敞口、每日买入额度/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认买入 $5.00" }));

    await waitFor(() => expect(requests.some((url) => url.endsWith("/orders/190/force-buy/execute"))).toBe(true));
    expect(requestBodies).toContainEqual({
      confirmation_id: "force-confirmation-id-1234567890",
      confirmation_text: "确认强制真实买入",
    });
  });

  it("总览最近记录可按目标钱包筛选并切回全部钱包", async () => {
    const user = userEvent.setup();
    const secondWallet = {
      ...wallet,
      id: 2,
      address: "0x2222222222222222222222222222222222222222",
      proxy_wallet: "0x2222222222222222222222222222222222222222",
      label: "策略二",
    };
    const secondStrategy = {
      ...overview.strategies[0],
      wallet: secondWallet,
      subscription: { ...subscription, id: 20, tracked_wallet_id: 2, tracked_wallet_label: "策略二" },
    };
    const secondOrder = {
      ...filledActivity,
      activity_id: "order:202",
      source_id: 202,
      tracked_wallet_id: 2,
      tracked_wallet_label: "策略二",
      tracked_wallet_address: secondWallet.proxy_wallet,
      title: "Will Team B win?",
      event_slug: "team-b-win",
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      requests.push(url);
      if (url.includes("/api/copy-trading/overview")) return json({ ...overview, strategies: [...overview.strategies, secondStrategy] });
      if (url.endsWith("/api/wallets")) return json([wallet, secondWallet]);
      if (url.includes("/api/copy-trading/activities")) return json({ items: [secondOrder], next_cursor: null });
      return json({ ...subscription, enabled: false, state: "exit_only" });
    }));

    render(<PolyCopyWorkspace view="overview" />);
    const panel = (await screen.findByRole("heading", { name: "最近记录" })).closest("section");
    expect(panel).not.toBeNull();
    const walletFilter = within(panel!).getByLabelText("最近记录目标钱包");
    expect(walletFilter).toHaveValue("all");
    expect(within(walletFilter).getByRole("option", { name: "全部钱包" })).toBeInTheDocument();
    expect(within(walletFilter).getByRole("option", { name: "策略二" })).toBeInTheDocument();
    expect(within(panel!).getAllByText("Will Team A win?").length).toBeGreaterThan(0);

    await user.selectOptions(walletFilter, "2");
    await waitFor(() => expect(requests.some((url) => url.includes("/api/copy-trading/activities?limit=8&tracked_wallet_id=2"))).toBe(true));
    await waitFor(() => expect(within(panel!).getAllByText("Will Team B win?").length).toBeGreaterThan(0));
    expect(within(panel!).queryByText("Will Team A win?")).not.toBeInTheDocument();

    await user.selectOptions(walletFilter, "all");
    await waitFor(() => expect(within(panel!).getAllByText("Will Team A win?").length).toBeGreaterThan(0));
    expect(within(panel!).queryByText("Will Team B win?")).not.toBeInTheDocument();
  });

  it("切换最近记录钱包时保留当前表格直到新数据返回", async () => {
    const user = userEvent.setup();
    let resolveOrders: ((response: Response) => void) | null = null;
    const pendingOrders = new Promise<Response>((resolve) => { resolveOrders = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/copy-trading/overview")) return json(overview);
      if (url.endsWith("/api/wallets")) return json([wallet]);
      if (url.includes("/api/copy-trading/activities")) return pendingOrders;
      return json({ ...subscription, enabled: false, state: "exit_only" });
    }));

    render(<PolyCopyWorkspace view="overview" />);
    const panel = (await screen.findByRole("heading", { name: "最近记录" })).closest("section");
    await user.selectOptions(within(panel!).getByLabelText("最近记录目标钱包"), "1");

    expect(panel!.querySelector('[aria-busy="true"]')).not.toBeNull();
    expect(within(panel!).getAllByText("Will Team A win?").length).toBeGreaterThan(0);

    resolveOrders!(json({ items: [{ ...filledActivity, activity_id: "order:303", source_id: 303, title: "筛选后的最新记录" }], next_cursor: null }));
    await waitFor(() => expect(within(panel!).getAllByText("筛选后的最新记录").length).toBeGreaterThan(0));
    expect(panel!.querySelector('[aria-busy="true"]')).toBeNull();
  });

  it("总览钱包最近记录支持空结果和读取失败状态", async () => {
    const user = userEvent.setup();
    let responseMode: "empty" | "error" = "empty";
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/copy-trading/overview")) return json(overview);
      if (url.endsWith("/api/wallets")) return json([wallet]);
      if (url.includes("/api/copy-trading/activities")) {
        return responseMode === "empty"
          ? json({ items: [], next_cursor: null })
          : json({ detail: "服务暂时不可用" }, 503);
      }
      return json({ ...subscription, enabled: false, state: "exit_only" });
    }));

    const { unmount } = render(<PolyCopyWorkspace view="overview" />);
    let panel = (await screen.findByRole("heading", { name: "最近记录" })).closest("section");
    await user.selectOptions(within(panel!).getByLabelText("最近记录目标钱包"), "1");
    expect(await within(panel!).findByText("暂无记录")).toBeInTheDocument();

    unmount();
    responseMode = "error";
    render(<PolyCopyWorkspace view="overview" />);
    panel = (await screen.findByRole("heading", { name: "最近记录" })).closest("section");
    await user.selectOptions(within(panel!).getByLabelText("最近记录目标钱包"), "1");
    expect(await within(panel!).findByText("最近记录读取失败")).toBeInTheDocument();
    expect(within(panel!).getByText("服务暂时不可用")).toBeInTheDocument();
  });

  it("策略列表展示大额加仓跟单模式", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/copy-trading/overview")) {
        return json({
          ...overview,
          strategies: overview.strategies.map((strategy) => ({
            ...strategy,
            subscription: { ...strategy.subscription, strategy_mode: "large_increase" },
          })),
        });
      }
      if (url.endsWith("/api/wallets")) return json([wallet]);
      return json({ ...subscription, enabled: false, state: "exit_only" });
    }));
    render(<PolyCopyWorkspace view="overview" />);
    expect(await screen.findByText("大额加仓跟单")).toBeInTheDocument();
    expect(screen.getByText("上限 $20.00")).toBeInTheDocument();
  });

  it("无成交钱包的总投入显示为零", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/copy-trading/overview")) {
        return json({
          ...overview,
          strategies: overview.strategies.map((strategy) => ({
            ...strategy,
            lifetime_bought_usdc: 0,
            lifetime_copy_order_count: 0,
          })),
        });
      }
      if (url.endsWith("/api/wallets")) return json([wallet]);
      return json({ ...subscription, enabled: false, state: "exit_only" });
    }));
    render(<PolyCopyWorkspace view="overview" />);
    const totalInvestment = (await screen.findByText("钱包总投入")).closest("div");
    expect(totalInvestment).not.toBeNull();
    expect(within(totalInvestment!).getByText("$0.00")).toBeInTheDocument();
    const copyCount = screen.getByText("累计跟单").closest("div");
    expect(copyCount).not.toBeNull();
    expect(within(copyCount!).getByText("0 笔")).toBeInTheDocument();
  });

  it("展示最近 30 日的已实现盈亏柱状图", async () => {
    const user = userEvent.setup();
    render(<PolyCopyWorkspace view="overview" />);
    const chart = await screen.findByRole("region", { name: "每日盈亏" });
    expect(within(chart).getByText("+$2.00")).toBeInTheDocument();
    expect(within(chart).getByText("$15.00")).toBeInTheDocument();
    expect(within(chart).getByText("1")).toBeInTheDocument();
    expect(within(chart).getByText("天")).toBeInTheDocument();
    expect(within(chart).getByRole("img", { name: /2026年8月5日.*\+\$3\.00/ })).toBeInTheDocument();
    expect(within(chart).getByRole("img", { name: /2026年8月6日.*-\$1\.00/ })).toBeInTheDocument();
    expect(within(chart).queryByRole("img", { name: /2026年7月7日/ })).not.toBeInTheDocument();
    expect(within(chart).getByRole("combobox", { name: "目标钱包" })).toHaveValue("all");

    await user.click(within(chart).getByRole("button", { name: "全部" }));
    expect(within(chart).getByText("+$7.00")).toBeInTheDocument();
    expect(within(chart).getByText("$35.00")).toBeInTheDocument();
    expect(within(chart).getByRole("img", { name: /2026年7月7日.*\+\$5\.00/ })).toBeInTheDocument();
  });

  it("每日盈亏可按目标钱包单独查看", async () => {
    const user = userEvent.setup();
    const walletDailyPnl = dailyRealizedPnl.map((item, index) => ({
      ...item,
      realized_pnl: index === 29 ? 4 : index === 30 ? -2 : 0,
      bought_usdc: index === 29 ? 8 : index === 30 ? 2 : 0,
    }));
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push(url);
      if (init?.body) requestBodies.push(JSON.parse(String(init.body)));
      if (url.includes("/api/copy-trading/overview")) {
        if (url.includes("tracked_wallet_id=1")) {
          return json({ ...overview, daily_realized_pnl: walletDailyPnl });
        }
        return json(overview);
      }
      if (url.endsWith("/api/wallets")) return json([wallet]);
      return json({ ...subscription, enabled: false, state: "exit_only" });
    }));

    render(<PolyCopyWorkspace view="overview" />);
    const chart = await screen.findByRole("region", { name: "每日盈亏" });
    const walletSelect = within(chart).getByRole("combobox", { name: "目标钱包" });
    expect(within(chart).getByText("全部策略按北京时间汇总的已实现盈亏。")).toBeInTheDocument();

    await user.selectOptions(walletSelect, "1");
    await waitFor(() => expect(requests.some((url) => url.includes("tracked_wallet_id=1"))).toBe(true));
    expect(await within(chart).findByText("策略一 按北京时间汇总的已实现盈亏。")).toBeInTheDocument();
    expect(within(chart).getByText("+$2.00")).toBeInTheDocument();
    expect(within(chart).getByText("$10.00")).toBeInTheDocument();
    expect(within(chart).getByRole("img", { name: /2026年8月5日.*\+\$4\.00/ })).toBeInTheDocument();
    expect(within(chart).getByRole("img", { name: /2026年8月6日.*-\$2\.00/ })).toBeInTheDocument();
  });

  it("切换目标钱包时汇总与图表同步进入加载态", async () => {
    const user = userEvent.setup();
    let resolveWallet!: (value: Response) => void;
    const walletOverview = new Promise<Response>((resolve) => {
      resolveWallet = resolve;
    });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push(url);
      if (init?.body) requestBodies.push(JSON.parse(String(init.body)));
      if (url.includes("/api/copy-trading/overview")) {
        if (url.includes("tracked_wallet_id=1")) {
          return walletOverview;
        }
        return json(overview);
      }
      if (url.endsWith("/api/wallets")) return json([wallet]);
      return json({ ...subscription, enabled: false, state: "exit_only" });
    }));

    render(<PolyCopyWorkspace view="overview" />);
    const chart = await screen.findByRole("region", { name: "每日盈亏" });
    expect(within(chart).getByText("+$2.00")).toBeInTheDocument();

    await user.selectOptions(within(chart).getByRole("combobox", { name: "目标钱包" }), "1");
    expect(await within(chart).findByText("正在加载钱包盈亏…")).toBeInTheDocument();
    expect(within(chart).getByText("策略一 按北京时间汇总的已实现盈亏。")).toBeInTheDocument();
    expect(within(chart).getAllByText("…").length).toBeGreaterThanOrEqual(2);
    expect(within(chart).queryByText("+$2.00")).not.toBeInTheDocument();

    resolveWallet(json({
      ...overview,
      daily_realized_pnl: dailyRealizedPnl.map((item, index) => ({
        ...item,
        realized_pnl: index === 29 ? 4 : index === 30 ? -2 : 0,
      })),
    }));
    expect(await within(chart).findByText("+$2.00")).toBeInTheDocument();
  });

  it("钱包盈亏读取失败时展示错误与重试", async () => {
    const user = userEvent.setup();
    let failWallet = true;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push(url);
      if (init?.body) requestBodies.push(JSON.parse(String(init.body)));
      if (url.includes("/api/copy-trading/overview")) {
        if (url.includes("tracked_wallet_id=1")) {
          if (failWallet) {
            return json({ detail: "钱包盈亏服务暂时不可用" }, 503);
          }
          return json({
            ...overview,
            daily_realized_pnl: dailyRealizedPnl.map((item, index) => ({
              ...item,
              realized_pnl: index === 29 ? 4 : index === 30 ? -2 : 0,
            })),
          });
        }
        return json(overview);
      }
      if (url.endsWith("/api/wallets")) return json([wallet]);
      return json({ ...subscription, enabled: false, state: "exit_only" });
    }));

    render(<PolyCopyWorkspace view="overview" />);
    const chart = await screen.findByRole("region", { name: "每日盈亏" });
    await user.selectOptions(within(chart).getByRole("combobox", { name: "目标钱包" }), "1");

    expect(await within(chart).findByText("钱包盈亏读取失败")).toBeInTheDocument();
    expect(within(chart).getByText("钱包盈亏服务暂时不可用")).toBeInTheDocument();
    expect(within(chart).queryByText("近 30 日暂无已实现盈亏")).not.toBeInTheDocument();
    expect(within(chart).getAllByText("—").length).toBeGreaterThanOrEqual(2);

    failWallet = false;
    await user.click(within(chart).getByRole("button", { name: "重试" }));
    expect(await within(chart).findByRole("img", { name: /2026年8月5日.*\+\$4\.00/ })).toBeInTheDocument();
    expect(within(chart).getByText("+$2.00")).toBeInTheDocument();
  });

  it("每日盈亏全部为零时展示空状态", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/copy-trading/overview")) {
        return json({
          ...overview,
          daily_realized_pnl: [
            { date: "2026-08-05", realized_pnl: 0 },
            { date: "2026-08-06", realized_pnl: 0 },
          ],
        });
      }
      if (url.endsWith("/api/wallets")) return json([wallet]);
      return json({ ...subscription, enabled: false, state: "exit_only" });
    }));
    render(<PolyCopyWorkspace view="overview" />);
    expect(await screen.findByText("近 30 日暂无已实现盈亏")).toBeInTheDocument();
  });

  it("按钱包独立暂停策略", async () => {
    const user = userEvent.setup();
    render(<PolyCopyWorkspace view="overview" />);
    await user.click(await screen.findByRole("switch", { name: "策略一暂停策略" }));
    await waitFor(() => expect(requests.some((url) => url.includes("/subscriptions/10/enabled"))).toBe(true));
  });

  it("策略开启失败时在策略栏上方展示错误，并在重试成功后清除", async () => {
    const user = userEvent.setup();
    let toggleAttempts = 0;
    let currentOverview = {
      ...overview,
      strategies: [{
        ...overview.strategies[0],
        subscription: { ...subscription, enabled: false, state: "exit_only" },
      }],
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/copy-trading/overview")) return json(currentOverview);
      if (url.endsWith("/api/wallets")) return json([wallet]);
      if (url.includes("/subscriptions/10/enabled")) {
        toggleAttempts += 1;
        if (toggleAttempts === 1) return json({ detail: "执行钱包余额不足" }, 422);
        currentOverview = {
          ...currentOverview,
          strategies: [{
            ...currentOverview.strategies[0],
            subscription: { ...subscription, enabled: true, state: "active" },
          }],
        };
        return json(subscription);
      }
      return json({});
    }));

    render(<PolyCopyWorkspace view="overview" />);
    await user.click(await screen.findByRole("switch", { name: "策略一恢复策略" }));

    const alert = await screen.findByRole("alert");
    const strategyPanel = screen.getByRole("heading", { name: "策略" }).closest("section");
    expect(alert).toHaveTextContent("策略开启失败");
    expect(alert).toHaveTextContent("执行钱包余额不足");
    expect(screen.queryByText("部分数据可能不是最新")).not.toBeInTheDocument();
    expect(alert.compareDocumentPosition(strategyPanel!)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);

    await user.click(screen.getByRole("switch", { name: "策略一恢复策略" }));
    await waitFor(() => expect(screen.queryByText("策略开启失败")).not.toBeInTheDocument());
    expect(await screen.findByRole("switch", { name: "策略一暂停策略" })).toBeInTheDocument();
  });

  it("新增目标时一次配置大额加仓模式和两档参数", async () => {
    const user = userEvent.setup();
    render(<PolyCopyWorkspace view="overview" />);
    await user.click(await screen.findByRole("button", { name: /添加目标/ }));
    const dialog = await screen.findByRole("dialog", { name: "添加目标" });
    await user.type(within(dialog).getByPlaceholderText(/0x/), wallet.address);
    await user.click(within(dialog).getByRole("radio", { name: "大额加仓跟单模式" }));
    expect(within(dialog).getByRole("spinbutton", { name: "第一档加仓阈值" })).toHaveValue(50000);
    expect(within(dialog).getByRole("spinbutton", { name: "第二档跟单比例" })).toHaveValue(0.2);
    await user.click(within(dialog).getByRole("button", { name: "保存钱包" }));
    await waitFor(() => expect(requestBodies).toContainEqual(expect.objectContaining({
      address: wallet.address,
      copy_strategy: expect.objectContaining({
        strategy_mode: "large_increase",
        base_entry_threshold_usdc: 100,
        tier_one_threshold_usdc: 50000,
        tier_one_ratio_percent: 0.1,
        tier_two_threshold_usdc: 100000,
        tier_two_ratio_percent: 0.2,
        position_cap_usdc: 20,
      }),
    })));
  });

  it("明确区分暂停策略与停止清仓", async () => {
    const user = userEvent.setup();
    render(<PolyCopyWorkspace view="overview" />);
    await user.click(await screen.findByLabelText("策略一更多操作"));
    expect(screen.getByRole("button", { name: "停止策略并立即清仓" })).toBeInTheDocument();
  });

  it("可配置并保存大额加仓阈值", async () => {
    const user = userEvent.setup();
    render(<PolyCopyWorkspace view="overview" />);
    await user.click(await screen.findByRole("button", { name: "参数" }));
    const dialog = await screen.findByRole("dialog", { name: /钱包策略设置/ });
    const threshold = within(dialog).getByRole("spinbutton", { name: /^加仓触发金额/ });
    expect(threshold).toHaveValue(100);
    await user.clear(threshold);
    await user.type(threshold, "250");
    await user.click(within(dialog).getByRole("button", { name: "保存参数" }));
    await waitFor(() => expect(requestBodies).toContainEqual(expect.objectContaining({
      large_increase_threshold_usdc: 250,
    })));
  });

  it("明确标记报价缺失的持仓", async () => {
    render(<PolyCopyWorkspace view="positions" />);
    expect((await screen.findAllByText("Will Team A win?")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("未定价").length).toBeGreaterThan(0);
    expect(screen.getByText(/1 个仓位未定价/)).toBeInTheDocument();
  });

  it("记录页展示风控阻止和可读原因", async () => {
    render(<PolyCopyWorkspace view="records" />);
    const table = await screen.findByRole("table");
    expect(within(table).getByText("风控阻止")).toBeInTheDocument();
    expect(within(table).getByText("每日买入上限已用完")).toBeInTheDocument();
  });

  it("记录页展示已完成赎回的到账、盈亏和结算哈希", async () => {
    recordsResponse = {
      items: [{
        ...filledActivity,
        activity_id: "redemption:12",
        activity_type: "redemption",
        source_id: 12,
        operation: "REDEEM",
        requested_size: 44.97959,
        requested_usdc: 44.97959,
        leader_purchase_usdc: null,
        proportional_target_usdc: null,
        executed_size: 44.97959,
        executed_usdc: 44.97959,
        fee_usdc: 0,
        execution_price: 1,
        realized_pnl: 22.3775809,
        status: "completed",
        transaction_id: "relay-redeem",
        transaction_hash: "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        title: "Milwaukee Brewers vs. San Diego Padres",
        outcome: "San Diego Padres",
      }],
      next_cursor: null,
    };
    render(<PolyCopyWorkspace view="records" />);
    const table = await screen.findByRole("table");
    expect(within(table).getByText("赎回")).toBeInTheDocument();
    expect(within(table).getByText("已赎回")).toBeInTheDocument();
    expect(within(table).getAllByText("$44.98").length).toBeGreaterThan(0);
    expect(within(table).getByText(/盈亏 \+\$22\.38/)).toBeInTheDocument();
    expect(within(table).getByText(/结算 0x1234…cdef/)).toBeInTheDocument();
  });

  it("记录页展示部分成交时取消的金额或份数", async () => {
    recordsResponse = {
      items: [
        {
          ...filledActivity,
          activity_id: "order:201",
          source_id: 201,
          operation: "SELL",
          requested_size: 25,
          executed_size: 20,
          status: "partially_filled",
          reason: "FAK 部分成交，剩余已取消",
        },
        {
          ...filledActivity,
          activity_id: "order:202",
          source_id: 202,
          operation: "BUY",
          requested_usdc: 10,
          executed_usdc: 7.75,
          status: "partially_filled",
          reason: "FAK 部分成交，剩余已取消",
        },
      ],
      next_cursor: null,
    };
    render(<PolyCopyWorkspace view="records" />);
    expect((await screen.findAllByText("已取消 5 份")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("已取消 $2.25").length).toBeGreaterThan(0);
  });

  it("设置页允许关闭自动赎回", async () => {
    const user = userEvent.setup();
    render(<PolyCopyWorkspace view="settings" />);
    const autoRedeem = await screen.findByRole("combobox", { name: /市场结算后自动赎回/ });
    await user.selectOptions(autoRedeem, "disabled");
    await user.click(screen.getByRole("button", { name: "保存资金风控" }));
    await waitFor(() => expect(requestBodies).toContainEqual(expect.objectContaining({
      auto_redeem: false,
    })));
  });

  it("策略高级参数不再显示已删除的观测钱包", async () => {
    const deletedWallet = { ...wallet, enabled: false };
    const activeWallet = {
      ...wallet,
      id: 2,
      address: "0x2222222222222222222222222222222222222222",
      proxy_wallet: "0x2222222222222222222222222222222222222222",
      label: "保留策略",
    };
    const activeStrategy = {
      ...overview.strategies[0],
      wallet: activeWallet,
      subscription: {
        ...subscription,
        id: 20,
        tracked_wallet_id: 2,
        tracked_wallet_label: "保留策略",
      },
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/copy-trading/overview")) {
        return json({
          ...overview,
          strategies: [
            { ...overview.strategies[0], wallet: deletedWallet },
            activeStrategy,
          ],
        });
      }
      if (url.endsWith("/api/wallets")) return json([deletedWallet, activeWallet]);
      return json(activeStrategy.subscription);
    }));

    render(<PolyCopyWorkspace view="settings" />);
    const panel = (await screen.findByRole("heading", { name: "策略高级参数" })).closest("section");
    const walletSelect = within(panel!).getByRole("combobox", { name: "目标钱包" });
    expect(within(walletSelect).queryByRole("option", { name: "策略一" })).not.toBeInTheDocument();
    expect(within(walletSelect).getByRole("option", { name: "保留策略" })).toBeInTheDocument();
    expect(walletSelect).toHaveValue("20");
  });

  it("所有钱包筛选下拉框都排除已删除的钱包", async () => {
    const deletedWallet = { ...wallet, enabled: false };
    const activeWallet = {
      ...wallet,
      id: 2,
      address: "0x2222222222222222222222222222222222222222",
      proxy_wallet: "0x2222222222222222222222222222222222222222",
      label: "保留策略",
    };
    const activeStrategy = {
      ...overview.strategies[0],
      wallet: activeWallet,
      subscription: {
        ...subscription,
        id: 20,
        tracked_wallet_id: 2,
        tracked_wallet_label: "保留策略",
      },
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/copy-trading/overview")) {
        return json({
          ...overview,
          strategies: [
            { ...overview.strategies[0], wallet: deletedWallet },
            activeStrategy,
          ],
        });
      }
      if (url.endsWith("/api/wallets")) return json([deletedWallet, activeWallet]);
      if (url.includes("/api/copy-trading/positions")) {
        return json({ items: [], portfolio: overview.strategies[0].portfolio, as_of: overview.as_of });
      }
      if (url.includes("/api/copy-trading/activities")) return json({ items: [], next_cursor: null });
      return json(activeStrategy.subscription);
    }));

    const assertWalletOptions = (select: HTMLElement) => {
      expect(within(select).queryByRole("option", { name: "策略一" })).not.toBeInTheDocument();
      expect(within(select).getByRole("option", { name: "保留策略" })).toBeInTheDocument();
    };

    const overviewView = render(<PolyCopyWorkspace view="overview" />);
    assertWalletOptions(await screen.findByRole("combobox", { name: "目标钱包" }));
    assertWalletOptions(screen.getByRole("combobox", { name: "最近记录目标钱包" }));
    overviewView.unmount();

    const positionsView = render(<PolyCopyWorkspace view="positions" />);
    assertWalletOptions(await screen.findByRole("combobox", { name: "目标钱包" }));
    positionsView.unmount();

    render(<PolyCopyWorkspace view="records" />);
    assertWalletOptions(await screen.findByRole("combobox", { name: "目标钱包" }));
  });
});
