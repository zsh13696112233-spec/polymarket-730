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
  copy_ratio_percent: 10,
  position_cap_usdc: 20,
  large_increase_threshold_usdc: 100,
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
  reason: null,
  created_at: "2026-08-06T08:00:00Z",
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
    open_positions: 1,
    stale: false,
  }],
  recent_orders: [filledOrder],
  as_of: "2026-08-06T08:00:00Z",
};

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

describe("PolyCopy workspace", () => {
  const requests: string[] = [];
  const requestBodies: unknown[] = [];

  beforeEach(() => {
    requests.length = 0;
    requestBodies.length = 0;
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
      if (url.includes("/api/copy-trading/orders")) return json({ items: [{ ...filledOrder, status: "blocked", reason: "每日买入上限已用完" }], next_cursor: null });
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
    expect(screen.getAllByText("源钱包交易金额").length).toBeGreaterThan(0);
    expect(screen.getAllByText("按比例目标金额").length).toBeGreaterThan(0);
    expect(screen.getAllByText("执行预算").length).toBeGreaterThan(0);
    expect(screen.getAllByText("实际执行金额").length).toBeGreaterThan(0);
    expect(screen.getAllByText("$100.00").length).toBeGreaterThan(0);
    const totalInvestment = screen.getByText("钱包总投入").closest("div");
    expect(totalInvestment).not.toBeNull();
    expect(within(totalInvestment!).getByText("$42.50")).toBeInTheDocument();
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
  });

  it("展示最近 30 日的已实现盈亏柱状图", async () => {
    const user = userEvent.setup();
    render(<PolyCopyWorkspace view="overview" />);
    const chart = await screen.findByRole("region", { name: "每日盈亏" });
    expect(within(chart).getByText("+$2.00")).toBeInTheDocument();
    expect(within(chart).getByText("$15.00")).toBeInTheDocument();
    expect(within(chart).getByText("1 天")).toBeInTheDocument();
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
});
