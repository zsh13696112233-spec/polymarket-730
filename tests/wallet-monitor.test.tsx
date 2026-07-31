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
  enabled: true,
  status: "ok",
  last_success_at: "2026-07-30T10:01:00Z",
  last_error: null,
  created_at: "2026-07-30T10:00:30Z",
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
    fills: [],
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
    await waitFor(() => {
      const rows = within(table).getAllByRole("row").slice(1);
      expect(rows).toHaveLength(2);
      expect(rows[0]).toHaveTextContent("较高市值市场");
      expect(rows[1]).toHaveTextContent("较低市值市场");
    });
    expect(screen.getByText("数据更新暂时中断")).toBeInTheDocument();
    expect(screen.getByText("已按持仓价值从高到低排列")).toBeInTheDocument();
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
      await screen.findByRole("tab", { name: /加减仓明细/ }),
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
