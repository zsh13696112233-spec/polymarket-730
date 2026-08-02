import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Home from "../app/page";

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

function mockFetch(handler: FetchHandler) {
  const fetchMock = vi.fn(
    async (input: FetchInput, init?: FetchInit): Promise<Response> =>
      handler(inputUrl(input), init),
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
  it("展示模拟跟单当前持仓估值并可切换历史盈亏", async () => {
    const subscription = {
      id: 1,
      tracked_wallet_id: walletOne.id,
      tracked_wallet_label: walletOne.label,
      mode: "paper",
      state: "active",
      copy_ratio_percent: 2,
      open_exposure_usdc: 6,
      daily_bought_usdc: 6,
      daily_buy_limit_usdc: 80,
      daily_realized_pnl: 2,
      market_slippage_cents: 5,
      last_trade_poll_at: "2026-08-02T10:00:00Z",
      last_trade_error: null,
      last_error: null,
    };
    const currentCopyPosition = {
      id: 11,
      title: "Paris 37°C 模拟持仓",
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
          account: null,
          subscription,
          positions: [currentCopyPosition, historicalCopyPosition],
          orders: [],
          signals: [],
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

    expect(await screen.findByText("模拟跟单持仓")).toBeInTheDocument();
    expect(screen.getAllByText("Paris 37°C 模拟持仓").length).toBeGreaterThan(0);
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
    expect(screen.getByText("盈亏未计交易手续费", { exact: false })).toBeInTheDocument();
  });

  it("保存全局跟单比例并按新比例展示历史建议", async () => {
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
      name: "跟单比例 10%，修改",
    });
    expect(
      (await screen.findAllByText("10% 跟单目标")).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText("10 shares").length).toBeGreaterThan(0);
    expect(
      screen.getAllByText("按现价约 $4.00 USDC").length,
    ).toBeGreaterThan(0);
    await user.click(settingsButton);
    const dialog = screen.getByRole("dialog", { name: "设置跟单比例" });
    const input = within(dialog).getByLabelText("全局跟单比例");
    await user.clear(input);
    await user.type(input, "25");
    await user.click(within(dialog).getByRole("button", { name: "保存比例" }));

    await waitFor(() => {
      expect(submittedBody).toEqual({ copy_ratio_percent: 25 });
    });
    expect(
      await screen.findByRole("button", { name: "跟单比例 25%，修改" }),
    ).toBeInTheDocument();
    expect(
      (await screen.findAllByText("25% 跟单目标")).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText("25 shares").length).toBeGreaterThan(0);
    expect(
      screen.getAllByText("按现价约 $10.00 USDC").length,
    ).toBeGreaterThan(0);

    await user.click(
      await screen.findByRole("tab", { name: /仓位变动明细/ }),
    );
    expect(screen.getAllByText("25% 跟单建议").length).toBeGreaterThan(0);
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
      if (url.pathname === "/api/overlap-alerts" && method === "GET") {
        return jsonResponse({
          items: [
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
    expect(within(activity).getByText("10% 跟单建议")).toBeInTheDocument();
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
    expect(within(table).getByText("跟单目标")).toBeInTheDocument();
    await waitFor(() => {
      const rows = within(table).getAllByRole("row").slice(1);
      expect(rows).toHaveLength(2);
      expect(rows[0]).toHaveTextContent("较高市值市场");
      expect(rows[0]).toHaveTextContent("10% 跟单目标");
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

  it("可按购买日期查看同一仓位的独立买入批次", async () => {
    const splitPosition = {
      ...makePosition("dated", "分批建仓市场", 120),
      size: 150,
      avg_price: 70 / 150,
      current_price: 0.8,
      initial_value: 70,
      current_value: 120,
      cash_pnl: 50,
      percent_pnl: (50 / 70) * 100,
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
      name: "按购买日期筛选持仓",
    });
    expect(dateFilter).toHaveValue("all");
    await user.selectOptions(dateFilter, "2026-08-01");

    const table = screen.getByRole("table");
    expect(within(table).getByText("2026年8月1日")).toBeInTheDocument();
    expect(within(table).getByText("$30.00")).toBeInTheDocument();
    expect(within(table).getByText("$40.00")).toBeInTheDocument();
    expect(within(table).getByText("$10.00")).toBeInTheDocument();

    const summaryRegion = screen.getByRole("region", { name: "钱包总览" });
    const costCard = within(summaryRegion)
      .getByText("持仓成本")
      .closest("article");
    expect(costCard).not.toBeNull();
    expect(within(costCard!).getByText("$30.00")).toBeInTheDocument();
    expect(within(costCard!).getByText("2026年8月1日")).toBeInTheDocument();
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

  it("点击加载更早记录后按游标合并事件", async () => {
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
      screen.getByRole("button", { name: "加载更早记录" }),
    );
    expect(
      await screen.findByRole("link", { name: /更早的加仓市场/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /较新的加仓市场/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "加载更早记录" }),
    ).not.toBeInTheDocument();
  });
});
