import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Home from "../app/analysis/legacy";

type FetchInput = Parameters<typeof fetch>[0];
type FetchInit = Parameters<typeof fetch>[1];
type FetchHandler = (
  url: URL,
  init: FetchInit,
) => Response | Promise<Response>;

const walletOne = {
  id: 1,
  address: "0x1111111111111111111111111111111111111111",
  proxy_wallet: "0x1111111111111111111111111111111111111111",
  label: "观察一号",
  wallet_role: "tracked",
  enabled: true,
  status: "ok",
  last_success_at: "2026-07-30T10:00:00Z",
  last_error: null,
  created_at: "2026-07-30T09:00:00Z",
};

const walletTwo = {
  id: 2,
  address: "0x2222222222222222222222222222222222222222",
  proxy_wallet: "0x2222222222222222222222222222222222222222",
  label: "新钱包",
  wallet_role: "tracked",
  enabled: true,
  status: "ok",
  last_success_at: "2026-07-30T10:01:00Z",
  last_error: null,
  created_at: "2026-07-30T10:00:30Z",
};

const myWallet = {
  id: 9,
  address: "0x9999999999999999999999999999999999999999",
  proxy_wallet: "0x9999999999999999999999999999999999999999",
  label: "我的主钱包",
  wallet_role: "self",
  enabled: true,
  status: "ok",
  last_success_at: "2026-07-30T10:02:00Z",
  last_error: null,
  created_at: "2026-07-30T08:00:00Z",
};

type MockPurchaseLot = {
  purchase_date: string;
  size: number;
  avg_price: number;
  initial_value: number;
  current_value: number;
  cash_pnl: number;
  percent_pnl: number;
};

type MockCycleTrade = {
  id: number;
  type: "opened" | "increased" | "decreased";
  size: number;
  price: number;
  amount: number;
  timestamp: string;
  transaction_hash: string | null;
};

type MockPosition = {
  wallet_id: number;
  asset_id: string;
  condition_id: string;
  title: string;
  outcome: string;
  icon_url: null;
  event_slug: string;
  market_slug: string;
  size: number;
  avg_price: number;
  current_price: number;
  initial_value: number;
  current_value: number;
  cash_pnl: number;
  percent_pnl: number;
  end_date: string;
  first_opened_at: string;
  first_opened_at_source: "trade" | "first_seen";
  opened_date: string;
  cycle_trades: MockCycleTrade[];
  cycle_history_complete: boolean;
  purchase_lots?: MockPurchaseLot[];
};

function makePosition(
  assetId: string,
  title: string,
  currentValue: number,
): MockPosition {
  return {
    wallet_id: 1,
    asset_id: assetId,
    condition_id: `condition-${assetId}`,
    title,
    outcome: "Yes",
    icon_url: null,
    event_slug: `event-${assetId}`,
    market_slug: `market-${assetId}`,
    size: 100,
    avg_price: 0.3,
    current_price: 0.4,
    initial_value: 30,
    current_value: currentValue,
    cash_pnl: currentValue - 30,
    percent_pnl: ((currentValue - 30) / 30) * 100,
    end_date: "2026-08-30T00:00:00Z",
    first_opened_at: "2026-07-29T03:04:05Z",
    first_opened_at_source: "trade",
    opened_date: "2026-07-29",
    cycle_trades: [],
    cycle_history_complete: true,
  };
}

function positionPayload(
  items: MockPosition[],
  stale = false,
) {
  const purchaseDates = [
    ...new Set(
      items.flatMap((item) =>
        (item.purchase_lots ?? []).map((lot) => lot.purchase_date),
      ),
    ),
  ].sort((left, right) => right.localeCompare(left));
  const openedDates = [
    ...new Set(items.map((item) => item.opened_date)),
  ].sort((left, right) => right.localeCompare(left));
  return {
    items,
    summary: {
      current_value: items.reduce(
        (total, item) => total + item.current_value,
        0,
      ),
      initial_value: items.reduce(
        (total, item) => total + item.initial_value,
        0,
      ),
      cash_pnl: items.reduce((total, item) => total + item.cash_pnl, 0),
      count: items.length,
    },
    opened_dates: openedDates,
    purchase_dates: purchaseDates,
    purchase_history_complete: items.every(
      (item) => (item.purchase_lots ?? []).length > 0,
    ),
    purchase_history_error: null,
    as_of: "2026-07-30T10:00:00Z",
    stale,
  };
}

function makeEvent(id: number, title: string) {
  return {
    id,
    wallet_id: 1,
    asset_id: `asset-${id}`,
    type: "increased",
    title,
    outcome: "Yes",
    event_slug: `event-${id}`,
    delta_size: 10,
    before_size: 20,
    after_size: 30,
    before_avg_price: 0.3,
    after_avg_price: 0.32,
    average_fill_price: 0.36,
    current_value: 12,
    reconciliation_status: "matched",
    first_detected_at: "2026-07-30T09:59:00Z",
    settled_at: "2026-07-30T10:00:00Z",
    payout_amount: null,
    redemption_cost_basis: null,
    redemption_entry_price: null,
    redemption_price: null,
    redemption_profit: null,
    redemption_profit_percent: null,
    redemption_cost_complete: null,
    close_cost_basis: null,
    close_proceeds: null,
    close_profit: null,
    close_profit_percent: null,
    close_profit_complete: false,
    transaction_hash: null,
    fills: [],
    copy_recommendation: {
      action: "buy",
      ratio_percent: 10,
      shares: 1,
      estimated_usdc: 0.36,
    },
  };
}

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function inputUrl(input: FetchInput) {
  if (typeof input === "string") return new URL(input);
  if (input instanceof URL) return input;
  return new URL(input.url);
}

function groupEventPayload(payload: {
  items: ReturnType<typeof makeEvent>[];
  next_cursor: string | number | null;
}) {
  const byAsset = new Map<string, ReturnType<typeof makeEvent>[]>();
  for (const event of payload.items) {
    byAsset.set(event.asset_id, [...(byAsset.get(event.asset_id) ?? []), event]);
  }
  const items = [...byAsset.values()].map((events) => {
    const ordered = [...events].sort((left, right) =>
      left.settled_at.localeCompare(right.settled_at),
    );
    const latest = ordered.at(-1)!;
    const counts = {
      opened: 0,
      increased: 0,
      decreased: 0,
      closed: 0,
      redeemed: 0,
    };
    for (const event of ordered) {
      counts[event.type as keyof typeof counts] += 1;
    }
    const status =
      latest.type === "closed"
        ? "closed"
        : latest.type === "redeemed"
          ? "redeemed"
          : "open";
    return {
      wallet_id: latest.wallet_id,
      asset_id: latest.asset_id,
      condition_id: `condition-${latest.asset_id}`,
      title: latest.title,
      outcome: latest.outcome,
      event_slug: latest.event_slug,
      status,
      event_count: ordered.length,
      event_counts: counts,
      cycle_count: 1,
      first_recorded_at: ordered[0].settled_at,
      latest_recorded_at: latest.settled_at,
      latest_event_id: latest.id,
      confirmed_realized_pnl: latest.close_profit ?? latest.redemption_profit ?? 0,
      incomplete_profit_events: 0,
      cycles: [
        {
          cycle_number: 1,
          status,
          start_source: ordered[0].type === "opened" ? "opened" : "first_recorded",
          history_complete: ordered[0].type === "opened",
          started_at: ordered[0].settled_at,
          ended_at: status === "open" ? null : latest.settled_at,
          confirmed_realized_pnl: latest.close_profit ?? latest.redemption_profit ?? 0,
          incomplete_profit_events: 0,
          events: ordered,
        },
      ],
    };
  });
  return {
    items,
    pnl: {
      recorded_since: "2026-07-30T08:00:00Z",
      confirmed_realized_pnl: 0,
      current_unrealized_pnl: 0,
      confirmed_total_pnl: 0,
      incomplete_realized_events: 0,
      complete: true,
    },
    next_cursor:
      payload.next_cursor === null ? null : String(payload.next_cursor),
  };
}

function mockFetch(handler: FetchHandler) {
  const fetchMock = vi.fn(
    async (input: FetchInput, init?: FetchInit): Promise<Response> => {
      const url = inputUrl(input);
      if (url.pathname !== "/api/position-event-groups") {
        return handler(url, init);
      }
      const legacyUrl = new URL(url);
      legacyUrl.pathname = "/api/position-events";
      const response = await handler(legacyUrl, init);
      if (!response.ok) return response;
      const payload = await response.json();
      if (payload && typeof payload === "object" && "pnl" in payload) {
        return jsonResponse(payload, response.status);
      }
      return jsonResponse(groupEventPayload(payload), response.status);
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

class MockEventSource {
  static instances: MockEventSource[] = [];

  readonly url: string;
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  close = vi.fn();

  constructor(url: string | URL) {
    this.url = String(url);
    MockEventSource.instances.push(this);
  }

  emit(payload: unknown) {
    this.onmessage?.(
      new MessageEvent("message", { data: JSON.stringify(payload) }),
    );
  }
}

function emptyEvents() {
  return { items: [], next_cursor: null };
}

beforeEach(() => {
  MockEventSource.instances = [];
  vi.stubGlobal("EventSource", MockEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Polymarket 钱包监控页", () => {
  it("展示实盘策略当前持仓估值并可切换历史盈亏", async () => {
    const subscription = {
      id: 1,
      tracked_wallet_id: walletOne.id,
      tracked_wallet_label: walletOne.label,
      enabled: true,
      state: "active",
      copy_ratio_percent: 2,
      position_cap_usdc: 20,
      total_exposure_cap_usdc: 80,
      open_exposure_usdc: 6,
      daily_bought_usdc: 6,
      daily_buy_limit_usdc: 80,
      daily_realized_pnl: 2,
      market_slippage_cents: 5,
      last_error: null,
    };
    const currentCopyPosition = {
      id: 11,
      title: "Paris 37°C 实盘持仓",
      outcome: "Yes",
      event_slug: "highest-temperature-in-paris-on-august-3-2026",
      attributed_size: 12.765957,
      attributed_cost: 6,
      realized_pnl: 0,
      status: "open",
      updated_at: "2026-08-02T10:00:00Z",
      average_entry_price: 0.47,
      current_bid: 0.46,
      current_value: 5.87234,
      unrealized_pnl: -0.12766,
      unrealized_pnl_percent: -2.12767,
      total_pnl: -0.12766,
      lifetime_bought_size: 12.765957,
      lifetime_bought_usdc: 6,
      lifetime_sold_size: 0,
      lifetime_sold_usdc: 0,
      lifetime_average_buy_price: 0.47,
      valuation_status: "ok",
      valued_at: "2026-08-02T10:00:00Z",
    };
    const historicalCopyPosition = {
      ...currentCopyPosition,
      id: 12,
      title: "London 17°C 历史持仓",
      attributed_size: 0,
      attributed_cost: 0,
      realized_pnl: 2.94,
      status: "closed",
      average_entry_price: null,
      current_bid: null,
      current_value: null,
      unrealized_pnl: null,
      unrealized_pnl_percent: null,
      total_pnl: 2.94,
      lifetime_bought_size: 12,
      lifetime_bought_usdc: 6,
      lifetime_sold_size: 12,
      lifetime_sold_usdc: 8.94,
      lifetime_average_buy_price: 0.5,
      valuation_status: "not_applicable",
      valued_at: null,
    };

    mockFetch((url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.pathname === "/api/settings") {
        return jsonResponse({ copy_ratio_percent: 10 });
      }
      if (url.pathname === "/api/wallets") {
        return jsonResponse([walletOne]);
      }
      if (url.pathname === "/api/positions") {
        return jsonResponse(positionPayload([]));
      }
      if (url.pathname === "/api/position-events") {
        return jsonResponse(emptyEvents());
      }
      if (url.pathname === "/api/copy-trading/dashboard") {
        return jsonResponse({
          live_copy_enabled: true,
          account: null,
          subscription,
          positions: [currentCopyPosition, historicalCopyPosition],
          orders: [],
          portfolio: {
            open_cost_usdc: 6,
            market_value_usdc: 5.87234,
            unrealized_pnl: -0.12766,
            realized_pnl: 2.94,
            total_pnl: 2.81234,
            valuation_complete: true,
            unpriced_positions: 0,
            valued_at: "2026-08-02T10:00:00Z",
          },
        });
      }
      throw new Error(`未处理的请求：${method} ${url}`);
    });

    const user = userEvent.setup();
    render(<Home />);

    expect(await screen.findByText("实盘归因持仓")).toBeInTheDocument();
    expect(screen.getAllByText("Paris 37°C 实盘持仓").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "关闭实盘策略" })).toBeInTheDocument();
    expect(screen.getAllByText("47¢").length).toBeGreaterThan(0);
    expect(screen.getAllByText("46¢").length).toBeGreaterThan(0);
    expect(screen.getAllByText("-$0.13").length).toBeGreaterThan(0);
    expect(screen.getAllByText("-2.13%").length).toBeGreaterThan(0);
    expect(screen.queryByText("London 17°C 历史持仓")).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /历史记录 1/ }));
    expect(screen.getAllByText("London 17°C 历史持仓").length).toBeGreaterThan(0);
    expect(screen.getAllByText("已清仓").length).toBeGreaterThan(0);
    expect(screen.getAllByText("$8.94").length).toBeGreaterThan(0);
    expect(screen.getAllByText("$2.94").length).toBeGreaterThan(0);
    expect(
      screen.getByText("实盘成本计入已回报的实际费用", { exact: false }),
    ).toBeInTheDocument();
  });

  it("按钱包显示实盘状态并通过确认开关启用", async () => {
    let submittedBody: unknown;
    const subscription = {
      id: 81,
      tracked_wallet_id: walletOne.id,
      tracked_wallet_label: walletOne.label,
      enabled: false,
      state: "disabled",
      copy_ratio_percent: 10,
      position_cap_usdc: 20,
      total_exposure_cap_usdc: 80,
      market_slippage_cents: 5,
      open_exposure_usdc: 0,
      daily_bought_usdc: 0,
      daily_realized_pnl: 0,
      last_error: null,
    };
    const account = {
      wallet_id: myWallet.id,
      signer_address: myWallet.address,
      funder_address: myWallet.address,
      signature_type: 3,
      status: "ready",
      credentials_configured: true,
      budget_usdc: 400,
      cash_reserve_usdc: 240,
      collateral_balance: 400,
      last_error: null,
    };

    mockFetch((url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.pathname === "/api/settings") {
        return jsonResponse({ copy_ratio_percent: 10 });
      }
      if (url.pathname === "/api/wallets") {
        return jsonResponse([walletOne, myWallet]);
      }
      if (url.pathname === "/api/positions") {
        return jsonResponse(positionPayload([]));
      }
      if (url.pathname === "/api/position-events") {
        return jsonResponse(emptyEvents());
      }
      if (url.pathname === "/api/copy-trading/subscriptions" && method === "GET") {
        return jsonResponse([subscription]);
      }
      if (url.pathname === "/api/copy-trading/dashboard") {
        return jsonResponse({
          live_copy_enabled: true,
          account,
          subscription,
          positions: [],
          orders: [],
          portfolio: null,
        });
      }
      if (
        url.pathname === `/api/copy-trading/subscriptions/${subscription.id}/enabled` &&
        method === "PUT"
      ) {
        submittedBody = JSON.parse(String(init?.body));
        return jsonResponse({ ...subscription, enabled: true, state: "active" });
      }
      throw new Error(`未处理的请求：${method} ${url}`);
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    render(<Home />);

    expect(await screen.findByText("已关闭")).toBeInTheDocument();
    await user.click(
      await screen.findByRole("button", { name: "开启实盘策略" }),
    );
    await waitFor(() => {
      expect(submittedBody).toEqual({ enabled: true, confirm_live: true });
    });
    expect(confirm).toHaveBeenCalledTimes(1);
  });

  it("在界面修改执行钱包现金保留额并保留其余全局风控", async () => {
    let submittedBody: Record<string, unknown> | null = null;
    const account = {
      wallet_id: myWallet.id,
      signer_address: myWallet.address,
      funder_address: myWallet.proxy_wallet,
      signature_type: 3,
      status: "insufficient_balance",
      credentials_configured: true,
      budget_usdc: 400,
      cash_reserve_usdc: 240,
      max_total_exposure_usdc: 160,
      daily_buy_limit_usdc: 80,
      daily_loss_limit_usdc: 40,
      auto_redeem: true,
      collateral_balance: 168.63,
      last_error: null,
    };

    mockFetch((url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.pathname === "/api/settings") {
        return jsonResponse({ copy_ratio_percent: 10 });
      }
      if (url.pathname === "/api/wallets") {
        return jsonResponse([walletOne, myWallet]);
      }
      if (url.pathname === "/api/positions") {
        return jsonResponse(positionPayload([]));
      }
      if (url.pathname === "/api/position-events") {
        return jsonResponse(emptyEvents());
      }
      if (url.pathname === "/api/copy-trading/subscriptions") {
        return jsonResponse([]);
      }
      if (url.pathname === "/api/copy-trading/dashboard") {
        return jsonResponse({
          live_copy_enabled: true,
          account,
          subscription: null,
          positions: [],
          orders: [],
          portfolio: null,
        });
      }
      if (url.pathname === "/api/copy-trading/account" && method === "PUT") {
        submittedBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
        return jsonResponse({
          ...account,
          ...submittedBody,
          status: "configured",
          collateral_balance: null,
        });
      }
      throw new Error(`未处理的请求：${method} ${url}`);
    });

    const user = userEvent.setup();
    render(<Home />);
    await user.click(await screen.findByRole("button", { name: "资金风控" }));
    const dialog = screen.getByRole("dialog", { name: "执行钱包资金风控" });
    const reserve = within(dialog).getByRole("spinbutton", { name: /现金保留额/ });
    await user.clear(reserve);
    await user.type(reserve, "100");
    expect(within(dialog).getByText("$68.63")).toBeInTheDocument();
    await user.click(
      within(dialog).getByRole("button", { name: "保存资金风控" }),
    );

    await waitFor(() => {
      expect(submittedBody).toEqual({
        wallet_id: myWallet.id,
        signer_address: myWallet.address,
        funder_address: myWallet.proxy_wallet,
        signature_type: 3,
        budget_usdc: 400,
        cash_reserve_usdc: 100,
        max_total_exposure_usdc: 160,
        daily_buy_limit_usdc: 80,
        daily_loss_limit_usdc: 40,
        auto_redeem: true,
      });
    });
  });

  it("展示并保存包含大额加仓阈值的低频策略配置", async () => {
    let submittedBody: Record<string, number> | null = null;
    let savedSubscription: Record<string, unknown> | null = null;
    const descriptions = [
      "观察钱包首次建仓或发生大额加仓时，按相应成本比例执行买入。",
      "每个市场周期首次建仓和大额加仓允许投入的累计最高金额。",
      "观察钱包单次净加仓达到此金额时，按执行比例跟随加仓。",
      "该观察钱包运行时预留的最高额度；多个钱包的额度共享执行账户总上限。",
      "FAK 买入/卖出相对当前最优价格允许的最差偏移，超出范围不成交。",
    ];

    mockFetch((url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.pathname === "/api/settings") {
        return jsonResponse({ copy_ratio_percent: 10 });
      }
      if (url.pathname === "/api/wallets") {
        return jsonResponse([walletOne]);
      }
      if (url.pathname === "/api/positions") {
        return jsonResponse(positionPayload([]));
      }
      if (url.pathname === "/api/position-events") {
        return jsonResponse(emptyEvents());
      }
      if (url.pathname === "/api/copy-trading/dashboard") {
        return jsonResponse({
          live_copy_enabled: true,
          account: null,
          subscription: savedSubscription,
          positions: [],
          orders: [],
          portfolio: null,
        });
      }
      if (
        url.pathname === "/api/copy-trading/subscriptions" &&
        method === "POST"
      ) {
        submittedBody = JSON.parse(String(init?.body)) as Record<string, number>;
        savedSubscription = {
          id: 71,
          tracked_wallet_id: walletOne.id,
          tracked_wallet_label: walletOne.label,
          enabled: false,
          state: "disabled",
          ...submittedBody,
          open_exposure_usdc: 0,
          daily_bought_usdc: 0,
          daily_realized_pnl: 0,
          last_error: null,
        };
        return jsonResponse(savedSubscription, 201);
      }
      throw new Error(`未处理的请求：${method} ${url}`);
    });

    const user = userEvent.setup();
    render(<Home />);
    await user.click(
      await screen.findByRole("button", { name: "配置实盘策略" }),
    );
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("低频策略设置")).toBeInTheDocument();
    for (const description of descriptions) {
      expect(within(dialog).getByText(description)).toBeInTheDocument();
    }

    const ratio = within(dialog).getByRole("spinbutton", {
      name: /^执行比例/,
    });
    const positionCap = within(dialog).getByRole("spinbutton", {
      name: /^单仓最大投入/,
    });
    const largeIncreaseThreshold = within(dialog).getByRole("spinbutton", {
      name: /^大额加仓阈值/,
    });
    await user.clear(ratio);
    await user.type(ratio, "12");
    await user.clear(positionCap);
    await user.type(positionCap, "18");
    await user.clear(largeIncreaseThreshold);
    await user.type(largeIncreaseThreshold, "250");
    await user.click(within(dialog).getByRole("button", { name: "保存风控" }));

    await waitFor(() => {
      expect(submittedBody).toEqual(
        expect.objectContaining({
          tracked_wallet_id: walletOne.id,
          copy_ratio_percent: 12,
          position_cap_usdc: 18,
          large_increase_threshold_usdc: 250,
          total_exposure_cap_usdc: 160,
          market_slippage_cents: 5,
        }),
      );
    });
  });

  it("保存全局执行比例并按新比例展示历史建议", async () => {
    let ratio = 10;
    let submittedBody: unknown;
    const currentPosition = makePosition(
      "current-copy",
      "当前持仓跟单市场",
      40,
    );

    mockFetch((url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.pathname === "/api/settings" && method === "GET") {
        return jsonResponse({ copy_ratio_percent: ratio });
      }
      if (url.pathname === "/api/settings" && method === "PUT") {
        submittedBody = JSON.parse(String(init?.body));
        ratio = Number(
          (submittedBody as { copy_ratio_percent: number })
            .copy_ratio_percent,
        );
        return jsonResponse({ copy_ratio_percent: ratio });
      }
      if (url.pathname === "/api/wallets") {
        return jsonResponse([walletOne]);
      }
      if (url.pathname === "/api/positions") {
        return jsonResponse(positionPayload([currentPosition]));
      }
      if (url.pathname === "/api/position-events") {
        const event = makeEvent(101, "跟单建议市场");
        event.copy_recommendation = {
          action: "buy",
          ratio_percent: ratio,
          shares: (10 * ratio) / 100,
          estimated_usdc: (10 * ratio * 0.36) / 100,
        };
        return jsonResponse({ items: [event], next_cursor: null });
      }
      throw new Error(`未处理的请求：${method} ${url}`);
    });

    const user = userEvent.setup();
    render(<Home />);

    const settingsButton = await screen.findByRole("button", {
      name: "执行比例 10%，修改",
    });
    expect(
      (await screen.findAllByText("10% 目标")).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText("10 shares").length).toBeGreaterThan(0);
    expect(
      screen.getAllByText("按现价约 $4.00 USDC").length,
    ).toBeGreaterThan(0);
    await user.click(settingsButton);
    const dialog = screen.getByRole("dialog", { name: "设置执行比例" });
    const input = within(dialog).getByLabelText("全局执行比例");
    await user.clear(input);
    await user.type(input, "25");
    await user.click(within(dialog).getByRole("button", { name: "保存比例" }));

    await waitFor(() => {
      expect(submittedBody).toEqual({ copy_ratio_percent: 25 });
    });
    expect(
      await screen.findByRole("button", { name: "执行比例 25%，修改" }),
    ).toBeInTheDocument();
    expect(
      (await screen.findAllByText("25% 目标")).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText("25 shares").length).toBeGreaterThan(0);
    expect(
      screen.getAllByText("按现价约 $10.00 USDC").length,
    ).toBeGreaterThan(0);

    await user.click(
      await screen.findByRole("tab", { name: /仓位变动明细/ }),
    );
    expect(screen.getAllByText("25% 建议").length).toBeGreaterThan(0);
    expect(screen.getAllByText("买入 2.5 shares").length).toBeGreaterThan(0);
    expect(
      screen.getAllByText("估算 $0.90 USDC").length,
    ).toBeGreaterThan(0);
  });

  it("可将只读地址设置为独立的我的钱包", async () => {
    let configured = false;
    let submittedBody: unknown;

    mockFetch(async (url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.pathname === "/api/my-wallet" && method === "PUT") {
        submittedBody = JSON.parse(String(init?.body));
        configured = true;
        return jsonResponse(myWallet);
      }
      if (url.pathname === "/api/wallets") {
        return jsonResponse(
          configured ? [walletOne, myWallet] : [walletOne],
        );
      }
      if (url.pathname === "/api/positions") {
        return jsonResponse(positionPayload([]));
      }
      if (url.pathname === "/api/position-events") {
        return jsonResponse(emptyEvents());
      }
      if (url.pathname === "/api/position-overlaps") {
        return jsonResponse({
          my_wallet_id: myWallet.id,
          tracked_wallet_id: walletOne.id,
          items: [],
          overlap_count: 0,
          my_as_of: myWallet.last_success_at,
          tracked_as_of: walletOne.last_success_at,
          my_stale: false,
          tracked_stale: false,
        });
      }
      if (url.pathname === "/api/overlap-alerts") {
        return jsonResponse({ items: [], unread_count: 0 });
      }
      throw new Error(`未处理的请求：${method} ${url}`);
    });

    const user = userEvent.setup();
    render(<Home />);
    await screen.findByRole("tab", { name: /观察一号/ });

    await user.click(
      screen.getByRole("button", { name: /我的钱包.*尚未设置/ }),
    );
    await user.type(
      screen.getByLabelText("钱包地址或个人页链接"),
      myWallet.address,
    );
    await user.type(screen.getByLabelText(/钱包备注/), myWallet.label);
    await user.click(screen.getByRole("button", { name: "保存并同步" }));

    await waitFor(() => {
      expect(submittedBody).toEqual({
        address: myWallet.address,
        label: myWallet.label,
      });
    });
    expect(
      await screen.findByRole("button", {
        name: /我的钱包.*我的主钱包.*更换/,
      }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: /我的主钱包/ }),
    ).not.toBeInTheDocument();
  });

  it("高亮共同持仓并展示双方份额比例详情", async () => {
    const trackedPosition = makePosition("shared", "共同市场", 40);
    trackedPosition.size = 100;
    const mine = {
      ...makePosition("shared", "共同市场", 4),
      wallet_id: 9,
      size: 10,
      initial_value: 3,
      cash_pnl: 1,
    };
    let alertRead = false;
    let alertDeleted = false;
    const reductionAlert = {
      id: 77,
      my_wallet_id: myWallet.id,
      tracked_wallet_id: walletOne.id,
      asset_id: "shared",
      condition_id: "condition-shared",
      title: "共同市场",
      outcome: "Yes",
      event_slug: "event-shared",
      market_slug: "market-shared",
      type: "decreased",
      before_size: 120,
      after_size: 100,
      delta_size: -20,
      detected_at: "2026-07-30T10:03:00Z",
      created_at: "2026-07-30T10:04:00Z",
      read_at: null,
    };

    mockFetch((url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.pathname === "/api/wallets") {
        return jsonResponse([walletOne, myWallet]);
      }
      if (url.pathname === "/api/positions") {
        return jsonResponse(positionPayload([trackedPosition]));
      }
      if (url.pathname === "/api/position-events") {
        return jsonResponse(emptyEvents());
      }
      if (url.pathname === "/api/position-overlaps") {
        return jsonResponse({
          my_wallet_id: myWallet.id,
          tracked_wallet_id: walletOne.id,
          items: [
            {
              asset_id: "shared",
              condition_id: "condition-shared",
              my_size: 10,
              tracked_size: 100,
              my_to_tracked_percent: 10,
              my_ratio: 1,
              tracked_ratio: 10,
            },
          ],
          overlap_count: 1,
          my_as_of: myWallet.last_success_at,
          tracked_as_of: walletOne.last_success_at,
          my_stale: false,
          tracked_stale: false,
        });
      }
      if (url.pathname === "/api/position-overlaps/shared") {
        return jsonResponse({
          my_wallet: myWallet,
          tracked_wallet: walletOne,
          mine,
          tracked: trackedPosition,
          my_to_tracked_percent: 10,
          my_ratio: 1,
          tracked_ratio: 10,
          my_stale: false,
          tracked_stale: false,
        });
      }
      if (url.pathname === "/api/overlap-alerts/77/read") {
        alertRead = true;
        return jsonResponse({
          ...reductionAlert,
          read_at: "2026-07-30T10:05:00Z",
        });
      }
      if (url.pathname === "/api/overlap-alerts/77" && method === "DELETE") {
        alertDeleted = true;
        return new Response(null, { status: 204 });
      }
      if (url.pathname === "/api/overlap-alerts" && method === "GET") {
        return jsonResponse({
          items: alertDeleted
            ? []
            : [
                {
                  ...reductionAlert,
                  read_at: alertRead ? "2026-07-30T10:05:00Z" : null,
                },
              ],
          unread_count: alertRead ? 0 : 1,
        });
      }
      throw new Error(`未处理的请求：${url}`);
    });

    const user = userEvent.setup();
    render(<Home />);

    expect(await screen.findByText("共同 1")).toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: /我的主钱包/ }),
    ).not.toBeInTheDocument();
    const badge = screen.getAllByRole("button", {
      name: /查看共同持仓对比.*10%/,
    })[0];
    expect(badge.closest("tr")).toHaveClass("overlapRow");
    expect(screen.getByText("共同持仓动态")).toBeInTheDocument();
    expect(screen.getByText("提醒 1")).toBeInTheDocument();
    const changeBadge = screen.getAllByRole("button", {
      name: /对方刚减仓.*标为已读/,
    })[0];
    expect(changeBadge.closest("tr")).toHaveClass("overlapRow");

    await user.click(changeBadge);
    await waitFor(() => expect(alertRead).toBe(true));
    expect(
      screen.queryByRole("button", {
        name: /对方刚减仓.*标为已读/,
      }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("已读")).not.toBeInTheDocument();
    const expandRead = screen.getByRole("button", {
      name: "展开已读 (1)",
    });
    await user.click(expandRead);
    expect(screen.getByText("已读")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "收起已读" }),
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "删除已读提醒：共同市场" }),
    );
    await waitFor(() => expect(alertDeleted).toBe(true));
    expect(screen.queryByText("共同持仓动态")).not.toBeInTheDocument();

    await user.click(badge);
    const dialog = await screen.findByRole("dialog", {
      name: /共同市场/,
    });
    expect(within(dialog).getByText("10%")).toBeInTheDocument();
    expect(within(dialog).getByText("1 : 10")).toBeInTheDocument();
    expect(within(dialog).getByText("我的主钱包")).toBeInTheDocument();
    expect(within(dialog).getByText("观察一号")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(
      screen.queryByRole("dialog", { name: /共同市场/ }),
    ).not.toBeInTheDocument();
  });

  it("对方加仓时在共同持仓行和动态区提醒", async () => {
    const trackedPosition = makePosition("shared", "共同市场", 56);
    trackedPosition.size = 140;
    const increaseAlert = {
      id: 78,
      my_wallet_id: myWallet.id,
      tracked_wallet_id: walletOne.id,
      asset_id: "shared",
      condition_id: "condition-shared",
      title: "共同市场",
      outcome: "Yes",
      event_slug: "event-shared",
      market_slug: "market-shared",
      type: "increased",
      before_size: 100,
      after_size: 140,
      delta_size: 40,
      detected_at: "2026-07-30T10:03:00Z",
      created_at: "2026-07-30T10:04:00Z",
      read_at: null,
      copy_recommendation: {
        action: "buy",
        ratio_percent: 10,
        shares: 4,
        estimated_usdc: 1.8,
      },
    };

    mockFetch((url) => {
      if (url.pathname === "/api/wallets") {
        return jsonResponse([walletOne, myWallet]);
      }
      if (url.pathname === "/api/positions") {
        return jsonResponse(positionPayload([trackedPosition]));
      }
      if (url.pathname === "/api/position-events") {
        return jsonResponse(emptyEvents());
      }
      if (url.pathname === "/api/position-overlaps") {
        return jsonResponse({
          my_wallet_id: myWallet.id,
          tracked_wallet_id: walletOne.id,
          items: [
            {
              asset_id: "shared",
              condition_id: "condition-shared",
              my_size: 10,
              tracked_size: 140,
              my_to_tracked_percent: 7.142857,
              my_ratio: 1,
              tracked_ratio: 14,
            },
          ],
          overlap_count: 1,
          my_as_of: myWallet.last_success_at,
          tracked_as_of: walletOne.last_success_at,
          my_stale: false,
          tracked_stale: false,
        });
      }
      if (url.pathname === "/api/overlap-alerts") {
        return jsonResponse({ items: [increaseAlert], unread_count: 1 });
      }
      throw new Error(`未处理的请求：${url}`);
    });

    render(<Home />);

    const activity = await screen.findByRole("region", {
      name: "共同持仓动态",
    });
    expect(within(activity).getByText("加仓")).toBeInTheDocument();
    expect(within(activity).getByText("10% 建议")).toBeInTheDocument();
    expect(within(activity).getByText("买入 4 shares")).toBeInTheDocument();
    expect(within(activity).getByText("估算 $1.80 USDC")).toBeInTheDocument();
    expect(
      within(activity).getByText(
        (_, element) =>
          element?.tagName === "SMALL" &&
          element.textContent?.includes("40 shares") === true,
      ),
    ).toBeInTheDocument();
    const badges = screen.getAllByRole("button", {
      name: /对方刚加仓.*100.*140.*标为已读/,
    });
    expect(badges[0]).toHaveClass("increased");
    expect(badges[0].closest("tr")).toHaveClass("overlapRow");
  });

  it("清仓提醒保留在共同持仓动态区且不创建持仓行", async () => {
    let markedAll = false;
    const closedAlert = {
      id: 88,
      my_wallet_id: myWallet.id,
      tracked_wallet_id: walletOne.id,
      asset_id: "closed-shared",
      condition_id: "condition-closed-shared",
      title: "已经清仓的共同市场",
      outcome: "No",
      event_slug: "event-closed-shared",
      market_slug: "market-closed-shared",
      type: "closed",
      before_size: 80,
      after_size: 0,
      delta_size: -80,
      detected_at: "2026-07-30T11:00:00Z",
      created_at: "2026-07-30T11:01:00Z",
      read_at: null,
    };

    mockFetch((url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.pathname === "/api/wallets") {
        return jsonResponse([walletOne, myWallet]);
      }
      if (url.pathname === "/api/positions") {
        return jsonResponse(positionPayload([]));
      }
      if (url.pathname === "/api/position-events") {
        return jsonResponse(emptyEvents());
      }
      if (url.pathname === "/api/position-overlaps") {
        return jsonResponse({
          my_wallet_id: myWallet.id,
          tracked_wallet_id: walletOne.id,
          items: [],
          overlap_count: 0,
          my_as_of: myWallet.last_success_at,
          tracked_as_of: walletOne.last_success_at,
          my_stale: false,
          tracked_stale: false,
        });
      }
      if (
        url.pathname === "/api/overlap-alerts/read-all" &&
        method === "POST"
      ) {
        markedAll = true;
        return new Response(null, { status: 204 });
      }
      if (url.pathname === "/api/overlap-alerts") {
        return jsonResponse({
          items: [closedAlert],
          unread_count: 1,
        });
      }
      throw new Error(`未处理的请求：${method} ${url}`);
    });

    const user = userEvent.setup();
    render(<Home />);

    expect(await screen.findByText("共同持仓动态")).toBeInTheDocument();
    expect(screen.getByText("对方已清仓")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /已经清仓的共同市场/ }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "全部已读" }));
    await waitFor(() => expect(markedAll).toBe(true));
    expect(screen.queryByText("提醒 1")).not.toBeInTheDocument();
    expect(screen.queryByText("已读")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /1 条已读提醒已收起/ }),
    ).toBeInTheDocument();
  });

  it("加载钱包后按当前市值降序展示，并提示过期数据", async () => {
    const lowerPosition = makePosition("low", "较低市值市场", 24);
    const higherPosition = makePosition("high", "较高市值市场", 88);

    mockFetch((url) => {
      if (url.pathname === "/api/wallets") {
        return jsonResponse([walletOne]);
      }
      if (url.pathname === "/api/positions") {
        return jsonResponse(
          positionPayload([lowerPosition, higherPosition], true),
        );
      }
      if (url.pathname === "/api/position-events") {
        return jsonResponse(emptyEvents());
      }
      throw new Error(`未处理的请求：${url}`);
    });

    render(<Home />);

    const table = await screen.findByRole("table");
    expect(within(table).getByText("目标")).toBeInTheDocument();
    await waitFor(() => {
      const rows = within(table).getAllByRole("row").slice(1);
      expect(rows).toHaveLength(2);
      expect(rows[0]).toHaveTextContent("较高市值市场");
      expect(rows[0]).toHaveTextContent("10% 目标");
      expect(rows[0]).toHaveTextContent("10 shares");
      expect(rows[1]).toHaveTextContent("较低市值市场");
    });
    expect(screen.getByText("数据更新暂时中断")).toBeInTheDocument();
    expect(screen.getByText("已按持仓价值从高到低排列")).toBeInTheDocument();
  });

  it("当前持仓展示精确到秒的初次建仓时间", async () => {
    const position = {
      ...makePosition("opened-at", "建仓时间市场", 52),
      first_opened_at: "2026-07-29T03:04:05Z",
      first_opened_at_source: "first_seen" as const,
    };

    mockFetch((url) => {
      if (url.pathname === "/api/wallets") {
        return jsonResponse([walletOne]);
      }
      if (url.pathname === "/api/positions") {
        return jsonResponse(positionPayload([position]));
      }
      if (url.pathname === "/api/position-events") {
        return jsonResponse(emptyEvents());
      }
      throw new Error(`未处理的请求：${url}`);
    });

    render(<Home />);

    const table = await screen.findByRole("table");
    expect(within(table).getByText("初次建仓")).toBeInTheDocument();
    expect(
      within(table).getByText((text) => /:04:05$/.test(text)),
    ).toBeInTheDocument();
    expect(within(table).getByText("首次监测")).toBeInTheDocument();
  });

  it("添加钱包会发送 POST，并自动选中新钱包", async () => {
    let created = false;
    let postedBody: unknown;

    mockFetch(async (url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.pathname === "/api/wallets" && method === "POST") {
        postedBody = JSON.parse(String(init?.body));
        created = true;
        return jsonResponse(walletTwo, 201);
      }
      if (url.pathname === "/api/wallets") {
        return jsonResponse(created ? [walletOne, walletTwo] : [walletOne]);
      }
      if (url.pathname === "/api/positions") {
        const walletId = url.searchParams.get("wallet_id");
        return jsonResponse(
          walletId === "2"
            ? positionPayload([
                {
                  ...makePosition("new-wallet", "新钱包持仓市场", 65),
                  wallet_id: 2,
                },
              ])
            : positionPayload([]),
        );
      }
      if (url.pathname === "/api/position-events") {
        return jsonResponse(emptyEvents());
      }
      throw new Error(`未处理的请求：${method} ${url}`);
    });

    const user = userEvent.setup();
    render(<Home />);
    await screen.findByRole("tab", { name: /观察一号/ });

    await user.click(screen.getByText("添加钱包").closest("button")!);
    await user.type(
      screen.getByLabelText("钱包地址或个人页链接"),
      walletTwo.address,
    );
    await user.type(screen.getByLabelText(/钱包备注/), walletTwo.label);
    await user.click(screen.getByRole("button", { name: "开始监控" }));

    const newWalletTab = await screen.findByRole("tab", { name: /新钱包/ });
    expect(newWalletTab).toHaveAttribute("aria-selected", "true");
    expect(postedBody).toEqual({
      address: walletTwo.address,
      label: walletTwo.label,
    });
    expect(
      within(await screen.findByRole("table")).getByText("新钱包持仓市场"),
    ).toBeInTheDocument();
  });

  it("删除当前观测钱包会二次确认，并自动切换到下一个钱包", async () => {
    let deletedWalletId: string | null = null;

    mockFetch((url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.pathname === "/api/wallets/1" && method === "DELETE") {
        deletedWalletId = "1";
        return new Response(null, { status: 204 });
      }
      if (url.pathname === "/api/wallets") {
        return jsonResponse(
          deletedWalletId ? [walletTwo] : [walletOne, walletTwo],
        );
      }
      if (url.pathname === "/api/positions") {
        const walletId = url.searchParams.get("wallet_id");
        return jsonResponse(
          positionPayload(
            walletId === "2"
              ? [
                  {
                    ...makePosition("remaining", "保留钱包持仓", 30),
                    wallet_id: 2,
                  },
                ]
              : [],
          ),
        );
      }
      if (url.pathname === "/api/position-events") {
        return jsonResponse(emptyEvents());
      }
      throw new Error(`未处理的请求：${method} ${url}`);
    });

    const user = userEvent.setup();
    render(<Home />);
    await screen.findByRole("tab", { name: /观察一号/ });

    await user.click(screen.getByRole("button", { name: "删除观测" }));
    const dialog = screen.getByRole("alertdialog", {
      name: "删除观测钱包？",
    });
    expect(within(dialog).getByText(/观察一号/)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /观察一号/ })).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "确认删除" }));

    expect(
      await screen.findByRole("tab", { name: /新钱包/ }),
    ).toHaveAttribute("aria-selected", "true");
    expect(
      screen.queryByRole("tab", { name: /观察一号/ }),
    ).not.toBeInTheDocument();
    expect(
      within(await screen.findByRole("table")).getByText("保留钱包持仓"),
    ).toBeInTheDocument();
    expect(deletedWalletId).toBe("1");
  });

  it("收到 positions.updated 后重新请求并刷新持仓", async () => {
    let positionRequests = 0;

    mockFetch((url) => {
      if (url.pathname === "/api/wallets") {
        return jsonResponse([walletOne]);
      }
      if (url.pathname === "/api/positions") {
        positionRequests += 1;
        return jsonResponse(
          positionPayload([
            makePosition(
              "live",
              "实时刷新市场",
              positionRequests === 1 ? 10 : 25,
            ),
          ]),
        );
      }
      if (url.pathname === "/api/position-events") {
        return jsonResponse(emptyEvents());
      }
      throw new Error(`未处理的请求：${url}`);
    });

    render(<Home />);
    await waitFor(() => expect(positionRequests).toBe(1));
    expect(screen.getAllByText("$10.00").length).toBeGreaterThan(0);

    const eventSource = MockEventSource.instances[0];
    expect(eventSource).toBeDefined();
    act(() => {
      eventSource.emit({ type: "positions.updated", wallet_id: 1 });
    });

    await waitFor(() => expect(positionRequests).toBe(2), { timeout: 1_500 });
    expect(screen.getAllByText("$25.00").length).toBeGreaterThan(0);
  });

  it("收到 overlap-alerts.created 后刷新共同持仓提醒", async () => {
    let alertRequests = 0;
    const alert = {
      id: 99,
      my_wallet_id: myWallet.id,
      tracked_wallet_id: walletOne.id,
      asset_id: "alert-live",
      condition_id: "condition-alert-live",
      title: "实时提醒市场",
      outcome: "Yes",
      event_slug: "event-alert-live",
      market_slug: "market-alert-live",
      type: "closed",
      before_size: 30,
      after_size: 0,
      delta_size: -30,
      detected_at: "2026-07-30T12:00:00Z",
      created_at: "2026-07-30T12:01:00Z",
      read_at: null,
    };

    mockFetch((url) => {
      if (url.pathname === "/api/wallets") {
        return jsonResponse([walletOne, myWallet]);
      }
      if (url.pathname === "/api/positions") {
        return jsonResponse(positionPayload([]));
      }
      if (url.pathname === "/api/position-events") {
        return jsonResponse(emptyEvents());
      }
      if (url.pathname === "/api/position-overlaps") {
        return jsonResponse({
          my_wallet_id: myWallet.id,
          tracked_wallet_id: walletOne.id,
          items: [],
          overlap_count: 0,
          my_as_of: myWallet.last_success_at,
          tracked_as_of: walletOne.last_success_at,
          my_stale: false,
          tracked_stale: false,
        });
      }
      if (url.pathname === "/api/overlap-alerts") {
        alertRequests += 1;
        return jsonResponse(
          alertRequests === 1
            ? { items: [], unread_count: 0 }
            : { items: [alert], unread_count: 1 },
        );
      }
      throw new Error(`未处理的请求：${url}`);
    });

    render(<Home />);
    await waitFor(() => expect(alertRequests).toBe(1));
    expect(screen.queryByText("实时提醒市场")).not.toBeInTheDocument();

    act(() => {
      MockEventSource.instances[0].emit({
        type: "overlap-alerts.created",
        wallet_id: walletOne.id,
      });
    });

    expect(await screen.findByText("实时提醒市场")).toBeInTheDocument();
    expect(alertRequests).toBe(2);
  });

  it("严格按建仓日期筛选并保留后续加仓后的完整仓位", async () => {
    const splitPosition = {
      ...makePosition("dated", "分批建仓市场", 120),
      first_opened_at: "2026-07-31T03:00:00Z",
      opened_date: "2026-07-31",
      size: 150,
      avg_price: 70 / 150,
      current_price: 0.8,
      initial_value: 70,
      current_value: 120,
      cash_pnl: 50,
      percent_pnl: (50 / 70) * 100,
      cycle_trades: [
        {
          id: 1,
          type: "opened" as const,
          size: 100,
          price: 0.4,
          amount: 40,
          timestamp: "2026-07-31T03:00:00Z",
          transaction_hash: "0x1111111111111111111111111111111111111111",
        },
        {
          id: 2,
          type: "increased" as const,
          size: 50,
          price: 0.6,
          amount: 30,
          timestamp: "2026-08-01T03:00:00Z",
          transaction_hash: null,
        },
      ],
      purchase_lots: [
        {
          purchase_date: "2026-07-31",
          size: 100,
          avg_price: 0.4,
          initial_value: 40,
          current_value: 80,
          cash_pnl: 40,
          percent_pnl: 100,
        },
        {
          purchase_date: "2026-08-01",
          size: 50,
          avg_price: 0.6,
          initial_value: 30,
          current_value: 40,
          cash_pnl: 10,
          percent_pnl: 100 / 3,
        },
      ],
    };

    mockFetch((url) => {
      if (url.pathname === "/api/wallets") {
        return jsonResponse([walletOne]);
      }
      if (url.pathname === "/api/positions") {
        return jsonResponse(positionPayload([splitPosition]));
      }
      if (url.pathname === "/api/position-events") {
        return jsonResponse(emptyEvents());
      }
      throw new Error(`未处理的请求：${url}`);
    });

    const user = userEvent.setup();
    render(<Home />);

    const dateFilter = await screen.findByRole("combobox", {
      name: "按建仓日期筛选持仓",
    });
    expect(dateFilter).toHaveValue("all");
    expect(
      within(dateFilter).queryByRole("option", { name: "2026年8月1日" }),
    ).not.toBeInTheDocument();
    await user.selectOptions(dateFilter, "2026-07-31");

    const table = screen.getByRole("table");
    expect(within(table).getByText("2026年7月31日")).toBeInTheDocument();
    expect(within(table).getByText("$70.00")).toBeInTheDocument();
    expect(within(table).getByText("$120.00")).toBeInTheDocument();
    expect(within(table).getByText("$50.00")).toBeInTheDocument();

    const summaryRegion = screen.getByRole("region", { name: "钱包总览" });
    const costCard = within(summaryRegion)
      .getByText("持仓成本")
      .closest("article");
    expect(costCard).not.toBeNull();
    expect(within(costCard!).getByText("$70.00")).toBeInTheDocument();
    expect(within(costCard!).getByText("2026年7月31日")).toBeInTheDocument();

    await user.click(within(table).getByRole("button", { name: "查看明细" }));
    expect(within(table).getByText("建仓")).toBeInTheDocument();
    expect(within(table).getByText("加仓")).toBeInTheDocument();
    expect(within(table).getByText("无交易哈希")).toBeInTheDocument();
    expect(
      within(table).getByRole("link", { name: /0x1111…1111/ }),
    ).toHaveAttribute(
      "href",
      "https://polygonscan.com/tx/0x1111111111111111111111111111111111111111",
    );
  });

  it("仓位变动明细把链上赎回与主动清仓分开显示", async () => {
    const redeemedEvent = {
      ...makeEvent(60, "已结算赎回市场"),
      type: "redeemed",
      delta_size: -350.1,
      before_size: 350.1,
      after_size: 0,
      average_fill_price: null,
      current_value: 0,
      reconciliation_status: "onchain",
      first_detected_at: "2026-07-31T06:30:53Z",
      settled_at: "2026-07-31T06:30:53Z",
      payout_amount: 350.1,
      redemption_cost_basis: 30.909,
      redemption_entry_price: 0.0882862039,
      redemption_price: 1,
      redemption_profit: 319.191,
      redemption_profit_percent: 1032.6798,
      redemption_cost_complete: true,
      transaction_hash:
        "0x08e8e1fbec07cd67e67f6c972138acc72e4075a2222fa62837cee9d21d930feb",
    };

    mockFetch((url) => {
      if (url.pathname === "/api/wallets") {
        return jsonResponse([walletOne]);
      }
      if (url.pathname === "/api/positions") {
        return jsonResponse(positionPayload([]));
      }
      if (url.pathname === "/api/position-events") {
        return jsonResponse({ items: [redeemedEvent], next_cursor: null });
      }
      throw new Error(`未处理的请求：${url}`);
    });

    const user = userEvent.setup();
    render(<Home />);

    await user.click(
      await screen.findByRole("tab", { name: /仓位变动明细/ }),
    );
    const marketLink = await screen.findByRole("link", {
      name: /已结算赎回市场/,
    });
    const card = marketLink.closest("details");
    expect(card).not.toBeNull();
    expect(within(card!).getAllByText("赎回")).toHaveLength(2);
    expect(within(card!).getAllByText("赎回份额").length).toBeGreaterThan(0);
    expect(within(card!).getAllByText("$350.10").length).toBeGreaterThan(0);
    expect(within(card!).getAllByText(/0\.088286/).length).toBeGreaterThan(0);
    expect(within(card!).getAllByText(/1\.000000/).length).toBeGreaterThan(0);
    expect(within(card!).getAllByText("$319.191").length).toBeGreaterThan(0);
    expect(within(card!).getAllByText("+1032.68%").length).toBeGreaterThan(0);
    expect(within(card!).queryByText("清仓")).not.toBeInTheDocument();

    await user.click(card!.querySelector("summary")!);
    expect(within(card!).getByText("链上赎回")).toBeInTheDocument();
    expect(within(card!).getByText("链上已确认")).toBeInTheDocument();
    expect(
      within(card!).getByRole("link", { name: "0x08e8…0feb" }),
    ).toHaveAttribute(
      "href",
      "https://polygonscan.com/tx/0x08e8e1fbec07cd67e67f6c972138acc72e4075a2222fa62837cee9d21d930feb",
    );
  });

  it("清仓事件展示盈利、亏损、持平和成交数据不完整", async () => {
    function makeClosedEvent(
      id: number,
      title: string,
      proceeds: number | null,
    ) {
      const complete = proceeds !== null;
      const profit = complete ? proceeds - 4 : null;
      return {
        ...makeEvent(id, title),
        type: "closed",
        delta_size: -10,
        before_size: 10,
        after_size: 0,
        before_avg_price: 0.4,
        after_avg_price: 0,
        average_fill_price: complete ? proceeds / 10 : 0.5,
        current_value: 0,
        reconciliation_status: complete ? "matched" : "partial",
        close_cost_basis: complete ? 4 : null,
        close_proceeds: proceeds,
        close_profit: profit,
        close_profit_percent: profit === null ? null : (profit / 4) * 100,
        close_profit_complete: complete,
        fills: complete
          ? [
              {
                id: id * 10,
                side: "SELL",
                size: 10,
                price: proceeds / 10,
                amount: proceeds,
                timestamp: "2026-07-31T06:30:53Z",
                transaction_hash: null,
              },
            ]
          : [],
        copy_recommendation: {
          action: "sell",
          ratio_percent: 10,
          shares: 1,
          estimated_usdc: proceeds === null ? null : proceeds / 10,
        },
      };
    }

    const events = [
      makeClosedEvent(61, "清仓盈利市场", 5),
      makeClosedEvent(62, "清仓亏损市场", 3),
      makeClosedEvent(63, "清仓持平市场", 4),
      makeClosedEvent(64, "清仓数据不完整市场", null),
    ];
    mockFetch((url) => {
      if (url.pathname === "/api/wallets") {
        return jsonResponse([walletOne]);
      }
      if (url.pathname === "/api/positions") {
        return jsonResponse(positionPayload([]));
      }
      if (url.pathname === "/api/position-events") {
        return jsonResponse({ items: events, next_cursor: null });
      }
      throw new Error(`未处理的请求：${url}`);
    });

    const user = userEvent.setup();
    render(<Home />);
    await user.click(
      await screen.findByRole("tab", { name: /仓位变动明细/ }),
    );

    const profitCard = (
      await screen.findByRole("link", { name: /清仓盈利市场/ })
    ).closest("details");
    const lossCard = screen
      .getByRole("link", { name: /清仓亏损市场/ })
      .closest("details");
    const flatCard = screen
      .getByRole("link", { name: /清仓持平市场/ })
      .closest("details");
    const incompleteCard = screen
      .getByRole("link", { name: /清仓数据不完整市场/ })
      .closest("details");

    expect(within(profitCard!).getByText("本次清仓盈利")).toBeInTheDocument();
    expect(within(profitCard!).getAllByText("$1.00").length).toBeGreaterThan(0);
    expect(within(profitCard!).getAllByText("+25.00%").length).toBeGreaterThan(0);
    expect(within(lossCard!).getByText("本次清仓亏损")).toBeInTheDocument();
    expect(within(flatCard!).getByText("本次清仓持平")).toBeInTheDocument();
    expect(
      within(incompleteCard!).getAllByText("成交数据不完整").length,
    ).toBeGreaterThan(0);

    await user.click(profitCard!.querySelector("summary")!);
    expect(within(profitCard!).getByText("清仓成本")).toBeInTheDocument();
    expect(within(profitCard!).getByText("卖出金额")).toBeInTheDocument();
    expect(within(profitCard!).getByText("$4.00")).toBeInTheDocument();
    expect(within(profitCard!).getAllByText("$5.00").length).toBeGreaterThan(0);
    expect(within(profitCard!).getByText("盈利 · $1.00")).toBeInTheDocument();
  });

  it("将同一仓位的多轮事件归组并展示钱包记录以来盈亏", async () => {
    const firstOpen = {
      ...makeEvent(71, "多轮仓位市场"),
      asset_id: "asset-multi-cycle",
      type: "opened" as const,
      before_size: 0,
      after_size: 10,
      delta_size: 10,
      settled_at: "2026-07-30T08:00:00Z",
    };
    const firstClose = {
      ...makeEvent(72, "多轮仓位市场"),
      asset_id: "asset-multi-cycle",
      type: "closed" as const,
      before_size: 10,
      after_size: 0,
      delta_size: -10,
      close_profit: 2.5,
      close_profit_complete: true,
      settled_at: "2026-07-30T09:00:00Z",
    };
    const secondOpen = {
      ...makeEvent(73, "多轮仓位市场"),
      asset_id: "asset-multi-cycle",
      type: "opened" as const,
      before_size: 0,
      after_size: 8,
      delta_size: 8,
      settled_at: "2026-07-31T08:00:00Z",
    };
    const groupedPayload = {
      items: [
        {
          wallet_id: walletOne.id,
          asset_id: "asset-multi-cycle",
          condition_id: "condition-multi-cycle",
          title: "多轮仓位市场",
          outcome: "Yes",
          event_slug: "multi-cycle-market",
          status: "open",
          event_count: 3,
          event_counts: {
            opened: 2,
            increased: 0,
            decreased: 0,
            closed: 1,
            redeemed: 0,
          },
          cycle_count: 2,
          first_recorded_at: firstOpen.settled_at,
          latest_recorded_at: secondOpen.settled_at,
          latest_event_id: secondOpen.id,
          confirmed_realized_pnl: 2.5,
          incomplete_profit_events: 1,
          cycles: [
            {
              cycle_number: 1,
              status: "closed",
              start_source: "opened",
              history_complete: true,
              started_at: firstOpen.settled_at,
              ended_at: firstClose.settled_at,
              confirmed_realized_pnl: 2.5,
              incomplete_profit_events: 0,
              events: [firstOpen, firstClose],
            },
            {
              cycle_number: 2,
              status: "open",
              start_source: "opened",
              history_complete: false,
              started_at: secondOpen.settled_at,
              ended_at: null,
              confirmed_realized_pnl: 0,
              incomplete_profit_events: 1,
              events: [secondOpen],
            },
          ],
        },
      ],
      pnl: {
        recorded_since: firstOpen.settled_at,
        confirmed_realized_pnl: 2.5,
        current_unrealized_pnl: -1,
        confirmed_total_pnl: 1.5,
        incomplete_realized_events: 1,
        complete: false,
      },
      next_cursor: null,
    };

    mockFetch((url) => {
      if (url.pathname === "/api/wallets") {
        return jsonResponse([walletOne]);
      }
      if (url.pathname === "/api/positions") {
        return jsonResponse(positionPayload([]));
      }
      if (url.pathname === "/api/position-events") {
        return jsonResponse(groupedPayload);
      }
      throw new Error(`未处理的请求：${url}`);
    });

    const user = userEvent.setup();
    render(<Home />);
    await user.click(
      await screen.findByRole("tab", { name: /仓位变动明细/ }),
    );

    const pnlPanel = await screen.findByRole("region", {
      name: "记录以来盈亏",
    });
    expect(within(pnlPanel).getByText("$2.50")).toBeInTheDocument();
    expect(within(pnlPanel).getByText("-$1.00")).toBeInTheDocument();
    expect(within(pnlPanel).getByText("$1.50")).toBeInTheDocument();
    expect(within(pnlPanel).getByText(/有 1 笔.*未计入/)).toBeInTheDocument();

    const group = (
      await screen.findByRole("link", { name: /多轮仓位市场/ })
    ).closest("details");
    expect(within(group!).getByText("2 轮 · 3 条动态")).toBeInTheDocument();
    expect(within(group!).getByText("新建仓 2 · 清仓 1")).toBeInTheDocument();
    await user.click(group!.querySelector("summary")!);
    expect(within(group!).getByText("第 1 轮")).toBeInTheDocument();
    const secondCycle = within(group!).getByText("第 2 轮").closest("section");
    expect(secondCycle).not.toBeNull();
    expect(within(secondCycle!).getByText(/已确认盈亏 暂无可靠数据/)).toBeInTheDocument();
    expect(within(group!).getByText("历史存在断点")).toBeInTheDocument();
  });

  it("点击加载更早仓位后按游标合并仓位组", async () => {
    const newerEvent = makeEvent(50, "较新的加仓市场");
    const olderEvent = makeEvent(40, "更早的加仓市场");

    mockFetch((url) => {
      if (url.pathname === "/api/wallets") {
        return jsonResponse([walletOne]);
      }
      if (url.pathname === "/api/positions") {
        return jsonResponse(positionPayload([]));
      }
      if (url.pathname === "/api/position-events") {
        if (url.searchParams.get("cursor") === "40") {
          return jsonResponse({ items: [olderEvent], next_cursor: null });
        }
        return jsonResponse({ items: [newerEvent], next_cursor: 40 });
      }
      throw new Error(`未处理的请求：${url}`);
    });

    const user = userEvent.setup();
    render(<Home />);

    await user.click(
      await screen.findByRole("tab", { name: /仓位变动明细/ }),
    );
    expect(
      await screen.findByRole("link", { name: /较新的加仓市场/ }),
    ).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "加载更早仓位" }),
    );
    expect(
      await screen.findByRole("link", { name: /更早的加仓市场/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /较新的加仓市场/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "加载更早仓位" }),
    ).not.toBeInTheDocument();
  });
});
