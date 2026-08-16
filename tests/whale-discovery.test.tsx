import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import WhaleDiscoveryWorkspace from "../app/components/WhaleDiscoveryWorkspace";
import WhaleRecordsWorkspace from "../app/components/WhaleRecordsWorkspace";

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const settings = {
  enabled: true,
  window_hours: 24,
  cumulative_threshold_usdc: 10000,
  single_trade_threshold_usdc: 10000,
  min_liquidity_usdc: 5000,
  min_remaining_minutes: 30,
  max_price_delta_cents: 5,
  max_follow_amount_usdc: 200,
  scan_interval_seconds: 60,
  last_scan_at: "2026-08-16T09:00:00Z",
  last_scan_error: null,
  consecutive_failures: 0,
  tracked_trade_count: 12,
  entry_count: 2,
  market_count: 1,
};

const whaleMarket = {
  condition_id: `0x${"1".repeat(64)}`,
  title: "LoL: Movistar KOI vs Natus Vincere",
  icon_url: null,
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
        profile_url: null,
        wallet_created_at: null,
        wallet_age_days: null,
        verified_badge: false,
        taker_tier_name: null,
        gross_buy_usdc: 16000,
        gross_buy_size: 30000,
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
        profile_url: null,
        wallet_created_at: "2025-08-16T00:00:00Z",
        wallet_age_days: 365,
        verified_badge: true,
        taker_tier_name: "Tier 2",
        gross_buy_usdc: 10000,
        gross_buy_size: 25000,
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
      }],
    },
  ],
};

afterEach(() => vi.unstubAllGlobals());

describe("巨鲸发现页", () => {
  it("合并渲染双边巨鲸，并在固定确认文案前禁用真实买入", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/whales/settings")) return json(settings);
      if (url.endsWith("/api/whales/tags")) return json([{ id: "64", slug: "esports", label: "Esports", market_count: 1 }]);
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
        total: 1,
        items: [whaleMarket],
      });
      return json({ detail: "not found" }, 404);
    }));

    const user = userEvent.setup();
    render(<WhaleDiscoveryWorkspace />);

    expect(await screen.findByText("双边对赌")).toBeInTheDocument();
    expect(screen.getByText("Movistar KOI")).toBeInTheDocument();
    expect(screen.getByText("Natus Vincere")).toBeInTheDocument();
    expect(screen.getByText("疑似做市/对冲")).toBeInTheDocument();
    expect(screen.getByText("注册 未知")).toBeInTheDocument();
    expect(screen.getAllByText("数据可能过期").length).toBeGreaterThan(0);

    await user.click(screen.getAllByRole("button", { name: "跟随买入" })[0]);
    const execute = screen.getByRole("button", { name: "确认买入" });
    expect(execute).toBeDisabled();

    expect(await screen.findByText("总成本", {}, { timeout: 2000 })).toBeInTheDocument();
    expect(screen.getByText("最大亏损")).toBeInTheDocument();
    expect(screen.getByText("结算可得")).toBeInTheDocument();
    expect(execute).toBeDisabled();

    await user.type(screen.getByLabelText("真实买入确认文案"), "确认真实买入");
    await waitFor(() => expect(execute).toBeEnabled());
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

    expect(await screen.findByText("当前持仓")).toBeInTheDocument();
    expect(screen.getAllByText("20.40 USDC").length).toBeGreaterThan(0);
    expect(screen.getByText("无法估值")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "一键卖出" }));
    const execute = screen.getByRole("button", { name: "确认卖出" });
    expect(execute).toBeDisabled();
    expect(await screen.findByText("预计回收", {}, { timeout: 2000 })).toBeInTheDocument();
    expect(execute).toBeDisabled();
  });
});
