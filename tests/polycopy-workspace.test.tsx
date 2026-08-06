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

  beforeEach(() => {
    requests.length = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      requests.push(url);
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

  it("展示全局资金、策略状态和最近跟单记录", async () => {
    render(<PolyCopyWorkspace view="overview" />);
    expect(await screen.findByText("执行钱包余额")).toBeInTheDocument();
    expect(screen.getByText("策略一")).toBeInTheDocument();
    expect(screen.getAllByText("Will Team A win?").length).toBeGreaterThan(0);
    expect(screen.getAllByText("$7.50").length).toBeGreaterThan(0);
  });

  it("按钱包独立暂停跟单", async () => {
    const user = userEvent.setup();
    render(<PolyCopyWorkspace view="overview" />);
    await user.click(await screen.findByRole("switch", { name: "策略一暂停跟单" }));
    await waitFor(() => expect(requests.some((url) => url.includes("/subscriptions/10/enabled"))).toBe(true));
  });

  it("明确区分暂停跟单与停止清仓", async () => {
    const user = userEvent.setup();
    render(<PolyCopyWorkspace view="overview" />);
    await user.click(await screen.findByLabelText("策略一更多操作"));
    expect(screen.getByRole("button", { name: "停止策略并立即清仓" })).toBeInTheDocument();
  });

  it("可打开策略配置窗口", async () => {
    const user = userEvent.setup();
    render(<PolyCopyWorkspace view="overview" />);
    await user.click(await screen.findByRole("button", { name: "参数" }));
    expect(await screen.findByRole("dialog", { name: /快速设置/ })).toBeInTheDocument();
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
});
