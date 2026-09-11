import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import WhaleDiscoveryWorkspace from "../app/components/WhaleDiscoveryWorkspace";
import WhaleRecordsWorkspace from "../app/components/WhaleRecordsWorkspace";
import WhaleAutoFollowWorkspace from "../app/components/WhaleAutoFollowWorkspace";
import ExecutionSettingsWorkspace from "../app/components/ExecutionSettingsWorkspace";
import EmailRecordsWorkspace from "../app/components/EmailRecordsWorkspace";
import { WhaleRequestMonitorPanel } from "../app/components/WhaleRequestMonitorPanel";

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const settings = {
  dual_match_auto_follow_amount_usdc: null,
  enabled: true,
  window_hours: 24,
  monitor_categories: ["sports", "esports", "politics", "crypto", "science_tech", "entertainment", "other"],
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
  new_account_auto_follow_enabled: false,
  new_account_auto_follow_amount_usdc: 5,
  new_account_auto_follow_min_price: 0.65,
  new_account_auto_follow_max_price: 0.8,
  new_account_auto_follow_categories: ["sports"],
  new_account_auto_follow_low_price_max_price: null,
  new_account_auto_follow_low_price_amount_usdc: null,
  large_amount_auto_follow_enabled: false,
  large_amount_auto_follow_amount_usdc: 10,
  large_amount_auto_follow_min_price: 0.6,
  large_amount_auto_follow_max_price: 0.8,
  large_amount_auto_follow_categories: ["sports"],
  large_amount_auto_follow_low_price_max_price: null,
  large_amount_auto_follow_low_price_amount_usdc: null,
  large_amount_conflict_priority_enabled: true,
  auto_follow_market_max_purchase_count: null,
  auto_follow_market_max_amount_usdc: null,
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
    const requests: string[] = [];
    let savedSettings = settings;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push(`${init?.method ?? "GET"} ${url}`);
      if (url.endsWith("/api/whales/settings") && init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        bodies.push(body);
        savedSettings = { ...settings, ...body };
        return json({
          ...savedSettings,
          single_trade_threshold_usdc: body.new_account_threshold_usdc,
          cumulative_threshold_usdc: body.new_account_threshold_usdc,
        });
      }
      if (url.endsWith("/api/whales/settings")) return json(savedSettings);
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
    const categories = within(screen.getByRole("group", { name: "监测市场分类" }));
    expect(categories.getAllByRole("checkbox")).toHaveLength(7);
    for (const label of ["政治", "加密", "娱乐", "其他"]) {
      await user.click(categories.getByRole("checkbox", { name: label }));
    }
    const saveButton = screen.getByRole("button", { name: "保存监测条件" });
    for (const label of ["传统体育", "电竞", "科学与科技"]) {
      await user.click(categories.getByRole("checkbox", { name: label }));
    }
    await user.click(saveButton);
    expect(await screen.findByRole("alert")).toHaveTextContent("监测至少需要选择一个市场分类。");
    expect(bodies).toHaveLength(0);
    for (const label of ["传统体育", "电竞", "科学与科技"]) {
      await user.click(categories.getByRole("checkbox", { name: label }));
    }
    const form = saveButton.closest("form");
    expect(form).not.toBeNull();
    expect(Array.from(form!.querySelectorAll("input")).filter((input) => !input.checkValidity()).map((input) => input.getAttribute("aria-label"))).toEqual([]);
    await user.click(saveButton);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({
      monitor_categories: ["sports", "esports", "science_tech"],
      registration_window_days: 7,
      new_account_threshold_usdc: 25000,
      large_amount_threshold_usdc: 600000,
    });
    expect(bodies[0]).not.toHaveProperty("new_account_auto_follow_enabled");
    expect(await screen.findByText("巨鲸监测条件已保存，数据已重新扫描。")).toBeInTheDocument();
    const saveIndex = requests.findIndex((request) => request.startsWith("PUT "));
    const reloadIndex = requests.findIndex((request, index) => index > saveIndex && request.includes("/api/whales/markets?"));
    const scanIndex = requests.findIndex((request) => request.includes("/api/whales/scan"));
    expect(reloadIndex).toBeGreaterThan(saveIndex);
    expect(scanIndex).toBeGreaterThan(reloadIndex);
    expect(categories.getByRole("checkbox", { name: "政治" })).not.toBeChecked();
    expect(categories.getByRole("checkbox", { name: "电竞" })).toBeChecked();
  });

  it("分别保存两套真实自动跟单策略和分类", async () => {
    const user = userEvent.setup();
    let saved: Record<string, unknown> | null = null;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/whales/settings") && init?.method === "PUT") {
        saved = JSON.parse(String(init.body));
        return json({ ...settings, ...saved });
      }
      if (url.endsWith("/api/whales/settings")) return json({ ...settings, ...saved });
      if (url.includes("/api/whales/auto-decisions?")) return json({ total: 0, items: [] });
      if (url.endsWith("/api/whales/scan")) return json({ status: "ok" });
      return json({ detail: "not found" }, 404);
    }));

    render(<WhaleAutoFollowWorkspace />);
    expect(await screen.findByLabelText("自动跟单策略概览")).toHaveTextContent("5 USDC");
    expect(screen.queryByRole("checkbox", { name: "开启新号大额自动跟单" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "编辑策略" }));
    await user.click(await screen.findByRole("checkbox", { name: "开启新号大额自动跟单" }));
    expect(screen.getByRole("status")).toHaveTextContent("真实资金功能");
    await user.click(screen.getByRole("button", { name: "关闭真实资金风险提示" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "开启新号大额自动跟单" }));
    await user.click(screen.getByRole("checkbox", { name: "开启新号大额自动跟单" }));
    expect(screen.getByRole("status")).toHaveTextContent("真实资金功能");
    const newStrategy = screen.getByRole("heading", { name: "新号大额自动跟单" }).closest("section");
    expect(newStrategy).not.toBeNull();
    await user.click(within(newStrategy!).getByText("政治"));
    await user.click(within(newStrategy!).getByRole("checkbox", { name: "新号大额自动跟单启用低价小额" }));
    await user.type(within(newStrategy!).getByRole("spinbutton", { name: "新号大额自动跟单低价分界" }), "0.7");
    await user.type(within(newStrategy!).getByRole("spinbutton", { name: "新号大额自动跟单低价金额" }), "3");
    await user.click(screen.getByRole("checkbox", { name: "启用双重命中独立金额" }));
    await user.type(screen.getByRole("spinbutton", { name: "双重命中跟单金额" }), "25");
    await user.click(screen.getByRole("checkbox", { name: "启用单市场共享上限" }));
    await user.type(screen.getByRole("spinbutton", { name: "单市场最大购买次数" }), "2");
    await user.type(screen.getByRole("spinbutton", { name: "单市场累计投入上限" }), "30");
    await user.click(screen.getByRole("checkbox", { name: "启用全量超大额优先规则" }));
    await user.click(screen.getByRole("button", { name: "保存自动跟单策略" }));

    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved).toMatchObject({
      new_account_auto_follow_enabled: true,
      dual_match_auto_follow_amount_usdc: 25,
      new_account_auto_follow_categories: ["sports", "politics"],
      new_account_auto_follow_low_price_max_price: 0.7,
      new_account_auto_follow_low_price_amount_usdc: 3,
      large_amount_auto_follow_enabled: false,
      large_amount_conflict_priority_enabled: false,
      auto_follow_market_max_purchase_count: 2,
      auto_follow_market_max_amount_usdc: 30,
    });
    await waitFor(() => expect(screen.getByText(/双重命中金额：25 USDC/)).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "编辑策略" }));
    expect(screen.getByRole("spinbutton", { name: "双重命中跟单金额" })).toHaveValue(25);
    await user.click(screen.getByRole("checkbox", { name: "启用双重命中独立金额" }));
    await user.click(screen.getByRole("button", { name: "保存自动跟单策略" }));
    await waitFor(() => expect(saved).toMatchObject({ dual_match_auto_follow_amount_usdc: null }));
    expect(screen.queryByText(/模拟/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "自动跟单" })).toHaveClass("active");

    const ruleFilter = screen.getByRole("combobox", { name: "命中规则" });
    expect(ruleFilter.tagName).toBe("BUTTON");
    await user.click(ruleFilter);
    await user.click(screen.getByRole("option", { name: "新号大额" }));
    expect(ruleFilter).toHaveTextContent("新号大额");

    const statusFilter = screen.getByRole("combobox", { name: "决策状态" });
    statusFilter.focus();
    await user.keyboard("{ArrowDown}{Enter}");
    expect(statusFilter).toHaveTextContent("等待处理");
  });

  it.each([
    ["strategy_protected", "策略保护", "warning", "实际买价 0.97400000000000000000 高于策略最高价 0.75000000000000000000", "实际买价 0.974 高于策略最高价 0.75"],
    ["strategy_protected", "策略保护", "warning", "巨鲸持有双向仓位", "巨鲸持有双向仓位"],
    ["failed", "执行失败", "danger", "Polymarket 接口请求过于频繁", "Polymarket 接口请求过于频繁"],
  ])("自动决策展示 %s 状态、原因和可打开的市场信息", async (status, label, tone, reason, displayedReason) => {
    const wallet = "0x1111111111111111111111111111111111111111";
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/whales/settings")) return json(settings);
      if (url.includes("/api/whales/auto-decisions?")) return json({
        total: 1,
        items: [{
          id: 1,
          entry_id: 2,
          proxy_wallet: wallet,
          asset_id: "asset-yes",
          condition_id: `0x${"c".repeat(64)}`,
          title: "Championship winner",
          market_slug: "championship-winner",
          event_slug: "championship-final",
          outcome: "Yes",
          matched_rules: ["large_amount"],
          selected_rule: "large_amount",
          category: "esports",
          category_label: "电竞",
          configured_amount_usdc: 10,
          selected_amount_usdc: 10,
          configured_min_price: 0.5,
          configured_max_price: 0.75,
          observed_best_ask: 0.7,
          status,
          reason,
          buy_order_id: 3,
          latest_sell_order_id: null,
          followed_wallet_count: 1,
          processed_at: "2026-08-28T10:00:00Z",
          created_at: "2026-08-28T10:00:00Z",
          updated_at: "2026-08-28T10:00:00Z",
        }],
      });
      return json({ detail: "not found" }, 404);
    }));

    render(<WhaleAutoFollowWorkspace />);

    const walletLink = await screen.findByRole("link", { name: /0x11111…11111.*全量/ });
    expect(walletLink).toHaveAttribute("href", `https://polymarket.com/profile/${wallet}`);
    expect(walletLink).toHaveAttribute("target", "_blank");
    const marketLink = screen.getByRole("link", { name: /Championship winner.*Yes/ });
    expect(marketLink).toHaveAttribute("href", "https://polymarket.com/event/championship-final");
    expect(marketLink).toHaveAttribute("target", "_blank");
    expect(screen.getByText(displayedReason)).toBeInTheDocument();
    expect(screen.getByText(label)).toHaveClass(tone);
    expect(screen.queryByText(/0\.75000000000000000000/)).not.toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("combobox", { name: "决策状态" }));
    await user.click(screen.getByRole("option", { name: "策略保护" }));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("status=strategy_protected"))).toBe(true));
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

describe("系统邮件设置", () => {
  it("执行钱包验证成功后显示成功状态", async () => {
    const user = userEvent.setup();
    const savedAccounts: unknown[] = [];
    const account = {
      signer_address: "0x1111111111111111111111111111111111111111",
      funder_address: "0x2222222222222222222222222222222222222222",
      signature_type: 3,
      credentials_configured: true,
      status: "ready",
      budget_usdc: 400,
      cash_reserve_usdc: 240,
      max_total_exposure_usdc: 160,
      daily_buy_limit_usdc: 80,
      daily_loss_limit_usdc: 40,
      auto_redeem: false,
      collateral_balance: 300,
      last_balance_at: "2026-08-29T16:00:00Z",
      last_error: null,
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/execution-account")) {
        if (init?.method === "PUT") savedAccounts.push(JSON.parse(String(init.body)));
        return json(account);
      }
      if (url.endsWith("/api/email-settings")) return json({
        notifications_enabled: false,
        notification_recipients: [],
        smtp_host: "smtp.163.com",
        smtp_port: 465,
        smtp_security: "ssl",
        smtp_username: null,
        smtp_from_email: null,
        smtp_from_name: "PolyCopy",
        smtp_authorization_code_configured: false,
        smtp_configured: false,
      });
      return json({ detail: "not found" }, 404);
    }));

    render(<ExecutionSettingsWorkspace />);
    expect(await screen.findByLabelText("钱包模式：Deposit Wallet")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "钱包类型" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Poly Proxy/)).not.toBeInTheDocument();
    await screen.findByDisplayValue(account.signer_address);
    const localRedeem = screen.getByRole("checkbox", { name: "启用本地主动赎回兜底" });
    expect(localRedeem).not.toBeChecked();
    await user.click(localRedeem);

    await user.click(screen.getByRole("button", { name: "保存配置" }));
    expect(savedAccounts).toContainEqual(expect.objectContaining({
      signature_type: 3,
      auto_redeem: true,
    }));

    await user.click(await screen.findByRole("button", { name: "验证密钥与授权" }));

    const notice = await screen.findByText("执行钱包验证完成。");
    expect(notice).toHaveClass("pcFormSuccess");
    expect(notice).not.toHaveClass("pcFormError");
  });

  it("通过链上环境测试工具完成 outcome 识别、买入和卖出", async () => {
    const user = userEvent.setup();
    const calls: Array<{ url: string; body: unknown }> = [];
    const account = {
      signer_address: "0x1111111111111111111111111111111111111111",
      funder_address: "0x2222222222222222222222222222222222222222",
      signature_type: 3,
      credentials_configured: true,
      status: "ready",
      budget_usdc: 400,
      cash_reserve_usdc: 240,
      max_total_exposure_usdc: 160,
      daily_buy_limit_usdc: 80,
      daily_loss_limit_usdc: 40,
      auto_redeem: false,
      collateral_balance: 300,
      last_balance_at: "2026-08-29T16:00:00Z",
      last_error: null,
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      if (url.includes("/chain-test/")) calls.push({ url, body });
      if (url.endsWith("/chain-test/resolve")) return json({
        resolution_id: "resolution-1",
        expires_at: "2026-08-30T12:10:00Z",
        market_url: "https://polymarket.com/event/test-event",
        event_title: "测试事件",
        markets: [{
          condition_id: `0x${"a".repeat(64)}`,
          title: "测试市场会通过吗？",
          market_slug: "test-market",
          event_slug: "test-event",
          closed: false,
          active: true,
          accepting_orders: true,
          outcomes: [
            { asset_id: "asset-yes", label: "Yes", outcome_index: 0, reference_price: 0.51 },
            { asset_id: "asset-no", label: "No", outcome_index: 1, reference_price: 0.49 },
          ],
        }],
      });
      if (url.endsWith("/chain-test/buy/preview")) return json({
        confirmation_id: "buy-preview-1",
        title: "测试市场会通过吗？",
        outcome: "Yes",
        amount_usdc: 5,
        best_ask: 0.51,
        worst_price: 0.53,
        minimum_order_usdc: 2.65,
        estimated_shares: 9.4339,
        estimated_fee_usdc: 0.02,
        total_cost_usdc: 5.02,
        immediate_exit_price: 0.47,
        immediate_exit_proceeds_usdc: 4.43,
        immediate_exit_pnl_usdc: -0.59,
        immediate_exit_unavailable_reason: null,
        available_balance_usdc: 300,
        reserve_warning: false,
      });
      if (url.endsWith("/chain-test/buy/execute")) return json({
        id: 11,
        position_id: 7,
        side: "BUY",
        title: "测试市场会通过吗？",
        outcome: "Yes",
        filled_size: 9.4,
        filled_usdc: 4.99,
        fee_usdc: 0.02,
        status: "filled",
        reason: null,
      });
      if (url.endsWith("/orders/11/sell/preview")) return json({
        confirmation_id: "sell-preview-1",
        position_id: 7,
        size: 9.4,
        best_bid: 0.49,
        worst_price: 0.47,
        minimum_order_size: 5,
        estimated_proceeds_usdc: 4.418,
        estimated_fee_usdc: 0.02,
        cost_basis_usdc: 5.01,
        estimated_pnl_usdc: -0.612,
        estimated_pnl_percent: -12.21,
      });
      if (url.endsWith("/orders/11/sell/execute")) return json({
        id: 12,
        position_id: 7,
        side: "SELL",
        title: "测试市场会通过吗？",
        outcome: "Yes",
        filled_size: 9.4,
        filled_usdc: 4.6,
        fee_usdc: 0.02,
        status: "filled",
        reason: null,
      });
      if (url.endsWith("/api/execution-account")) return json(account);
      if (url.endsWith("/api/email-settings")) return json({
        notifications_enabled: false,
        notification_recipients: [],
        smtp_host: "smtp.163.com",
        smtp_port: 465,
        smtp_security: "ssl",
        smtp_username: null,
        smtp_from_email: null,
        smtp_from_name: "PolyCopy",
        smtp_authorization_code_configured: false,
        smtp_configured: false,
      });
      return json({ detail: "not found" }, 404);
    }));

    render(<ExecutionSettingsWorkspace />);
    await user.type(await screen.findByRole("textbox", { name: "Polymarket 市场链接" }), "https://polymarket.com/event/test-event");
    await user.click(screen.getByRole("button", { name: "识别 outcome" }));
    await user.click(await screen.findByRole("radio", { name: /Yes/ }));
    const amount = screen.getByRole("spinbutton", { name: "链上测试买入金额" });
    await user.clear(amount);
    await user.type(amount, "5");
    await user.click(screen.getByRole("button", { name: "预览真实买入" }));
    await user.click(await screen.findByRole("button", { name: /确认真实买入/ }));
    await user.click(await screen.findByRole("button", { name: "一键卖出本次成交" }));
    await user.click(await screen.findByRole("button", { name: "确认卖出本次成交" }));

    expect(await screen.findByText("买卖链路验证完成")).toBeInTheDocument();
    expect(calls.map((call) => call.body)).toEqual(expect.arrayContaining([
      { market_url: "https://polymarket.com/event/test-event" },
      { resolution_id: "resolution-1", asset_id: "asset-yes", amount_usdc: 5 },
      { confirmation_id: "buy-preview-1", confirmation_text: "确认真实买入" },
      { confirmation_id: "sell-preview-1", confirmation_text: "确认真实卖出" },
    ]));
  });

  it("在系统设置工作台配置 163 SMTP 并测试连接", async () => {
    const user = userEvent.setup();
    const bodies: unknown[] = [];
    const emailSettings = {
      notifications_enabled: false,
      notification_recipients: [],
      smtp_host: "smtp.163.com",
      smtp_port: 465,
      smtp_security: "ssl",
      smtp_username: null,
      smtp_from_email: null,
      smtp_from_name: "PolyCopy",
      smtp_authorization_code_configured: false,
      smtp_configured: false,
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/execution-account")) return json(null);
      if (url.endsWith("/api/email-settings") && init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        bodies.push(body);
        return json({
          ...emailSettings,
          ...body,
          smtp_authorization_code_configured: true,
          smtp_configured: true,
        });
      }
      if (url.endsWith("/api/email-settings")) return json(emailSettings);
      if (url.endsWith("/api/email-settings/test")) return json({
        status: "ok",
        connection: "ok",
        tls: "ok",
        authentication: "ok",
        message_sent: false,
        detail: "163 SMTP 连接及认证成功",
      });
      return json({ detail: "not found" }, 404);
    }));

    render(<ExecutionSettingsWorkspace />);
    const notificationCheckbox = await screen.findByRole("checkbox", { name: "启用系统邮件通知" });
    await user.click(screen.getByText("启用系统邮件通知"));
    expect(notificationCheckbox).not.toBeChecked();
    await user.click(notificationCheckbox);
    await user.type(screen.getByRole("textbox", { name: "系统通知收件邮箱" }), "alerts@example.com");
    await user.type(await screen.findByRole("textbox", { name: "163 发件邮箱" }), "sender@163.com");
    await user.type(screen.getByLabelText("163 客户端授权码"), "authorization-code");
    await user.click(screen.getByRole("button", { name: "测试连接" }));

    expect(await screen.findByText("163 SMTP 连接及认证成功")).toBeInTheDocument();
    expect(bodies).toContainEqual(expect.objectContaining({
      smtp_host: "smtp.163.com",
      smtp_port: 465,
      smtp_security: "ssl",
      smtp_username: "sender@163.com",
      smtp_authorization_code: "authorization-code",
      notifications_enabled: true,
      notification_recipients: ["alerts@example.com"],
    }));
  });
});

describe("邮件记录工作台", () => {
  it("在邮件记录页启用每周汇总并展示周报发送记录", async () => {
    const user = userEvent.setup();
    const bodies: unknown[] = [];
    const settings = {
      notifications_enabled: true,
      weekly_summary_enabled: false,
      weekly_summary_enabled_at: null,
      weekly_summary_last_sent_at: "2026-08-17T16:00:05Z",
      weekly_summary_next_run_at: null,
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/email-settings") && init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        bodies.push(body);
        return json({
          ...settings,
          ...body,
          weekly_summary_enabled_at: "2026-08-29T02:00:00Z",
          weekly_summary_next_run_at: "2026-08-30T16:00:00Z",
        });
      }
      if (url.endsWith("/api/email-settings")) return json(settings);
      if (url.includes("/api/email-notifications?")) return json({
        total: 1,
        items: [{
          id: 20,
          entry_id: null,
          notification_kind: "weekly_summary",
          condition_id: "weekly-summary",
          entry_ids: [],
          rules: [],
          recipient_email: "alerts@example.com",
          market_title: "每周邮件命中率｜2026-08-17 至 2026-08-23",
          wallet_label: "命中 6 · 未命中 4 · 待结算 3",
          market_summaries: [],
          subject: "[PolyCopy] 每周邮件命中率｜08-17 至 08-23",
          body_text: "有效样本：10\n命中：6\n未命中：4\n有效命中率：60%",
          result: "not_applicable",
          status: "sent",
          attempt_count: 1,
          next_attempt_at: null,
          last_error: null,
          created_at: "2026-08-23T16:00:00Z",
          sent_at: "2026-08-23T16:00:05Z",
        }],
      });
      return json({ detail: "not found" }, 404);
    }));

    render(<EmailRecordsWorkspace />);

    const checkbox = await screen.findByRole("checkbox", { name: "启用每周命中率汇总" });
    expect(checkbox).not.toBeChecked();
    expect(await screen.findByText("每周汇总")).toBeInTheDocument();
    expect(screen.getByText("命中 6 · 未命中 4 · 待结算 3")).toBeInTheDocument();
    expect(screen.queryByText("买入摘要")).not.toBeInTheDocument();
    await user.click(checkbox);
    await waitFor(() => expect(bodies).toContainEqual({ weekly_summary_enabled: true }));
    expect(await screen.findByText("每周命中率汇总已启用，将从下一个周一开始发送。")).toBeInTheDocument();
    expect(checkbox).toBeChecked();
  });

  it("独立展示逐收件人投递记录并支持状态筛选", async () => {
    const requestedUrls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      requestedUrls.push(url);
      if (url.includes("/api/email-notifications?")) return json({
        total: 1,
        items: [{
          id: 12,
          entry_id: 7,
          notification_kind: "entry",
          condition_id: `0x${"c".repeat(64)}`,
          entry_ids: [7],
          rules: ["new_account", "large_amount"],
          recipient_email: "alerts@example.com",
          market_title: "Will Bitcoin reach $150k?",
          wallet_label: "Alpha Whale",
          market_summaries: [{
            category_label: "加密",
            outcome: "Yes",
            avg_buy_price: 0.5,
            gross_buy_usdc: 600000,
          }],
          subject: "[PolyCopy] 新号大额 + 全量超大额提醒",
          body_text: "命中规则：新号大额 + 全量超大额\n近 24 小时累计买入：600,000 USDC",
          result: "miss",
          status: "failed",
          attempt_count: 5,
          next_attempt_at: null,
          last_error: "SMTP authentication failed",
          created_at: "2026-08-26T01:00:00Z",
          sent_at: null,
        }],
      });
      return json({ detail: "not found" }, 404);
    }));

    const user = userEvent.setup();
    render(<EmailRecordsWorkspace />);

    expect(await screen.findByRole("heading", { name: "发送记录" })).toBeInTheDocument();
    expect(await screen.findByText("alerts@example.com")).toBeInTheDocument();
    expect(screen.getByText("新号大额 / 全量超大额")).toBeInTheDocument();
    expect(screen.queryByText("发送内容")).not.toBeInTheDocument();
    expect(screen.queryByText("[PolyCopy] 新号大额 + 全量超大额提醒")).not.toBeInTheDocument();
    expect(screen.queryByText(/近 24 小时累计买入：600,000 USDC/)).not.toBeInTheDocument();
    expect(screen.getByText("未命中")).toBeInTheDocument();
    expect(screen.getByText("SMTP authentication failed")).toBeInTheDocument();
    expect(screen.getByText("市场种类")).toBeInTheDocument();
    expect(screen.getByText("买入均价")).toBeInTheDocument();
    expect(screen.getByText("买入总额")).toBeInTheDocument();
    expect(screen.getByText("加密")).toBeInTheDocument();
    expect(screen.getByText("Yes")).toBeInTheDocument();
    expect(screen.getByText("600.0K USDC")).toHaveAttribute("title", "600,000.00 USDC");
    await user.selectOptions(screen.getByRole("combobox", { name: "邮件状态筛选" }), "failed");
    await waitFor(() => expect(requestedUrls.some((url) => url.includes("status=failed"))).toBe(true));
  });

  it("将分歧通知展示为不参与单侧命中统计的市场级提醒", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/email-notifications?")) return json({
        total: 1,
        items: [{
          id: 13,
          entry_id: null,
          notification_kind: "divergence",
          condition_id: `0x${"d".repeat(64)}`,
          entry_ids: [54, 55],
          rules: ["large_amount"],
          recipient_email: "alerts@example.com",
          market_title: "Real Madrid CF vs. Real Sociedad de Fútbol: O/U 3.5",
          wallet_label: "2 个钱包 · 2 个方向",
          market_summaries: [
            { category_label: "传统体育", outcome: "Over", avg_buy_price: 0.5189, gross_buy_usdc: 521964.41 },
            { category_label: "传统体育", outcome: "Under", avg_buy_price: 0.4825, gross_buy_usdc: 512653.33 },
          ],
          subject: "[PolyCopy] 分歧市场提醒｜Real Madrid CF vs. Real Sociedad de Fútbol: O/U 3.5",
          body_text: "结论：方向高度分歧，不应把任一侧视为明确跟单信号。",
          result: "not_applicable",
          status: "sent",
          attempt_count: 1,
          next_attempt_at: null,
          last_error: null,
          created_at: "2026-08-26T18:29:31Z",
          sent_at: "2026-08-26T18:29:36Z",
        }],
      });
      return json({ detail: "not found" }, 404);
    }));

    render(<EmailRecordsWorkspace />);

    expect(await screen.findByText("分歧市场")).toBeInTheDocument();
    expect(screen.getByText("市场级提醒 · 2 条关联记录")).toBeInTheDocument();
    expect(screen.getByText("不适用")).toBeInTheDocument();
    expect(screen.getAllByText("传统体育")).toHaveLength(2);
    expect(screen.getByText("Over")).toBeInTheDocument();
    expect(screen.getByText("Under")).toBeInTheDocument();
    expect(screen.queryByText("待结算")).not.toBeInTheDocument();
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

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("巨鲸请求监测面板", () => {
  it("左右分栏展示成功与失败请求，并在同一次请求重试成功后隐藏旧失败", async () => {
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
      source: "http",
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

    expect(await screen.findByText("success")).toBeInTheDocument();
    expect(screen.getByText("16:00:00")).toBeInTheDocument();
    expect(screen.getByLabelText("Request Monitor")).not.toHaveClass("pcPanel");
    expect(screen.queryByRole("heading", { name: "Request Monitor" })).not.toBeInTheDocument();
    expect(screen.getByText("HTTP 200 · 321ms").closest("code")).toHaveTextContent(
      "HTTP · GEThttps://data-api.polymarket.com/tradesHTTP 200 · 321ms",
    );
    expect(screen.getByText("HTTP 200 · 321ms")).toBeInTheDocument();
    const successColumn = screen.getByRole("region", { name: "成功日志" });
    const failedColumn = screen.getByRole("region", { name: "错误日志" });
    expect(within(successColumn).getByText("success")).toBeInTheDocument();
    expect(within(successColumn).getByText("pending")).toBeInTheDocument();
    expect(within(failedColumn).getByText("暂无失败请求。")).toBeInTheDocument();
    expect(successColumn.nextElementSibling).toBe(failedColumn);
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
    expect(await screen.findByText("failed")).toBeInTheDocument();
    expect(within(failedColumn).getByText(/Polymarket 接口返回 503/)).toBeInTheDocument();
    expect(within(successColumn).queryByText("failed")).not.toBeInTheDocument();
    expect(within(failedColumn).queryByText("success")).not.toBeInTheDocument();

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
    expect(screen.queryByText("failed")).not.toBeInTheDocument();
    expect(screen.queryByText(/Polymarket 接口返回 503/)).not.toBeInTheDocument();

    FakeEventSource.instance?.onmessage?.(new MessageEvent("message", {
      data: JSON.stringify({
        ...pending,
        id: 9,
        source: "sdk",
        status: "success",
        finished_at: "2026-08-23T08:00:05Z",
        http_status: 200,
        duration_ms: 280,
      }),
    }));
    expect(await screen.findByText("16:00:05")).toBeInTheDocument();
    expect(screen.getByText("SDK · GET")).toBeInTheDocument();

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
    await waitFor(() => expect(screen.getAllByText("success")).toHaveLength(6));
    expect(screen.getByText("16:00:00")).toBeInTheDocument();
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
    expect(screen.getByText("暂无成功或进行中的请求。")).toBeInTheDocument();
    expect(screen.getByText("暂无失败请求。")).toBeInTheDocument();
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
    expect(recentHistory).toHaveTextContent("+400.0K USDC");
    expect(recentHistory.querySelector("time")).toHaveTextContent("触发 08/16 16:00");
    await user.click(screen.getByRole("button", { name: "展开完整历史" }));
    expect(await screen.findByLabelText("新号大额历史记录")).toBeInTheDocument();
    expect(screen.getAllByText("+400.0K USDC")).toHaveLength(2);
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
    expect(screen.queryByLabelText("Request Monitor")).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "分歧市场" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("巨鲸分歧市场")).toHaveTextContent("1 个市场"));
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
      category: "all",
      subcategory: "all",
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
      category_breakdown: [
        { key: "esports", label: "电竞", metrics },
        { key: "sports", label: "传统体育", metrics: { ...metrics, settled_count: 0, effective_sample_count: 0, hit_count: 0, miss_count: 0, pending_count: 0, hit_rate_percent: null } },
        { key: "politics", label: "政治", metrics: { ...metrics, settled_count: 0, effective_sample_count: 0, hit_count: 0, miss_count: 0, pending_count: 0, hit_rate_percent: null } },
        { key: "crypto", label: "加密", metrics: { ...metrics, settled_count: 0, effective_sample_count: 0, hit_count: 0, miss_count: 0, pending_count: 0, hit_rate_percent: null } },
        { key: "science_tech", label: "科学与科技", metrics: { ...metrics, settled_count: 0, effective_sample_count: 0, hit_count: 0, miss_count: 0, pending_count: 0, hit_rate_percent: null } },
        { key: "entertainment", label: "娱乐", metrics: { ...metrics, settled_count: 0, effective_sample_count: 0, hit_count: 0, miss_count: 0, pending_count: 0, hit_rate_percent: null } },
        { key: "other", label: "其他", metrics: { ...metrics, settled_count: 0, effective_sample_count: 0, hit_count: 0, miss_count: 0, pending_count: 0, hit_rate_percent: null } },
      ],
      subcategory_breakdown: [],
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
      category: "esports",
      category_label: "电竞",
      subcategory: "dota-2",
      subcategory_label: "Dota 2",
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
      if (url.includes("/api/whales/statistics?")) {
        const esportsSelected = url.includes("category=esports");
        return json({
          ...statistics,
          range: url.includes("range=7d") ? "7d" : "all",
          category: esportsSelected ? "esports" : "all",
          subcategory: url.includes("subcategory=dota-2") ? "dota-2" : "all",
          subcategory_breakdown: esportsSelected
            ? [
              { key: "dota-2", label: "Dota 2", metrics },
              { key: "counter-strike-2", label: "CS2", metrics: { ...metrics, effective_sample_count: 0, hit_rate_percent: null } },
              { key: "league-of-legends", label: "英雄联盟", metrics: { ...metrics, effective_sample_count: 0, hit_rate_percent: null } },
              { key: "other-esports", label: "其他电竞", metrics: { ...metrics, effective_sample_count: 0, hit_rate_percent: null } },
            ]
            : [],
        });
      }
      return json({ detail: "not found" }, 404);
    }));

    const user = userEvent.setup();
    render(<WhaleDiscoveryWorkspace />);
    await user.click(await screen.findByRole("button", { name: /统计/ }));

    expect(await screen.findByRole("heading", { name: "链上信号表现" })).toBeInTheDocument();
    expect(screen.queryByLabelText("巨鲸监测设置")).not.toBeInTheDocument();
    expect(screen.getByLabelText("整体统计")).toHaveTextContent("50.0%");
    expect(screen.getByLabelText("规则表现对比")).toHaveTextContent("样本不足");
    expect(await screen.findByText("Winning Whale ↗")).toBeInTheDocument();
    expect(screen.getByText("持有至结算，未计手续费")).toBeInTheDocument();
    expect(screen.getByLabelText("分类表现")).toHaveTextContent("样本偏少");
    expect(screen.getByText("Dota 2")).toBeInTheDocument();

    await user.click(within(screen.getByLabelText("统计分类筛选")).getByRole("button", { name: "电竞" }));
    await waitFor(() => expect(requests.some((url) => url.includes("category=esports&subcategory=all"))).toBe(true));
    await user.selectOptions(await screen.findByLabelText("统计子分类筛选"), "dota-2");
    await waitFor(() => expect(requests.some((url) => url.includes("category=esports&subcategory=dota-2"))).toBe(true));

    await user.click(screen.getByRole("button", { name: "近 7 天" }));
    await waitFor(() => expect(requests.some((url) => url.includes("statistics?range=7d"))).toBe(true));
    await user.selectOptions(screen.getByLabelText("统计结果筛选"), "hit");
    await waitFor(() => expect(requests.some((url) => url.includes("result=hit"))).toBe(true));
  });
});

describe("巨鲸跟单记录页", () => {
  it("保留汇总和流水筛选，持仓操作统一跳转持仓管理", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/whales/records?")) return json({
        items: [{
          id: 1, position_id: 9, order_id: null, type: "sell", source: "wallet_manual",
          title: "历史卖出市场", outcome: "Yes", size: 10, price: 0.6,
          amount_usdc: 6, fee_usdc: 0, realized_pnl: 1,
          transaction_hash: null, detail: null, timestamp: "2026-08-16T09:00:00Z",
        }],
        summary: {
          total_invested_usdc: 20.4,
          total_proceeds_usdc: 0,
          total_fee_usdc: 0.4,
          realized_pnl: 0,
          unrealized_pnl: null,
          total_pnl: null,
          open_position_count: 1,
          closed_position_count: 5,
          win_count: 2,
          loss_count: 1,
          excluded_conflict_exit_count: 2,
          excluded_chain_test_count: 1,
          win_rate_percent: 66.6667,
          average_profit_ratio_percent: null,
        },
      });
      return json({ detail: "not found" }, 404);
    }));

    const user = userEvent.setup();
    render(<WhaleRecordsWorkspace />);

    expect(await screen.findByText("历史卖出市场")).toBeInTheDocument();
    expect(screen.getByText("持仓管理卖出")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "自动跟单决策" })).not.toBeInTheDocument();
    expect(screen.getAllByText("20.40 USDC").length).toBeGreaterThan(0);
    expect(screen.getByText("+66.7%")).toBeInTheDocument();
    expect(screen.getByText("2 胜 / 1 负 · 不含 2 笔分歧退出、1 笔链路测试")).toBeInTheDocument();

    expect(screen.queryByRole("heading", { name: "当前持仓" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "一键卖出" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "展开完整流水" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看持仓管理" })).toHaveAttribute("href", "/positions");

    await user.type(screen.getByLabelText("流水开始日期"), "2026-08-16");
    await user.click(screen.getByRole("button", { name: "筛选" }));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("start_date=2026-08-16"))).toBe(true));
    await user.click(screen.getByRole("button", { name: "清除" }));
    await waitFor(() => expect(screen.getByRole("button", { name: /刷新记录/ })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: /刷新记录/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /刷新记录/ })).toBeEnabled());
    expect(vi.mocked(fetch).mock.calls.every(([url]) => String(url).includes("/api/whales/records?"))).toBe(true);
  });
});

it("展示持仓补充来源、减仓和窗口外对冲，并禁用不合格跟单", async () => {
  const market = {
    ...whaleMarket,
    sides: [{
      ...whaleMarket.sides[0],
      entries: [{
        ...whaleMarket.sides[0].entries[0],
        discovery_source: "positions",
        trade_count: 0,
        status: "reduced",
        net_ratio: 10,
        position_cost_usdc: 1500,
        opposite_size: 2000,
        directional_size: 1000,
        hedged: true,
        position_checked_at: "2026-08-16T09:00:00Z",
        follow_eligible: false,
        follow_ineligible_reason: "position_hedged",
      }],
    }],
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/whales/settings")) return json(settings);
    if (url.includes("/api/whales/markets?")) return json({ generated_at: "2026-08-16T09:00:00Z", total: 1, items: [market] });
    if (url.includes("/api/whales/history?")) return json({ total: 0, items: [] });
    return json({ detail: "not found" }, 404);
  }));
  render(<WhaleDiscoveryWorkspace />);
  expect(await screen.findByText("持仓补充发现")).toBeInTheDocument();
  expect(screen.getByText("发现时持仓成本")).toBeInTheDocument();
  expect(screen.getByText("首次发现 · 买入时间未知")).toBeInTheDocument();
  expect(screen.getByText("已减仓 · 保留 10.0%")).toBeInTheDocument();
  expect(screen.getByText("反向 2000.00 份 · 净方向 1000.00 份")).toBeInTheDocument();
  expect(screen.getByText("钱包对冲")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "跟单" })).toBeDisabled();
  expect(screen.queryByText("窗口累计买入")).not.toBeInTheDocument();
});


describe("监测均价筛选", () => {
  it("默认筛选、切换规则共用选择，并在重新进入时记住取消状态", async () => {
    const user = userEvent.setup();
    const requests: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      requests.push(url);
      if (url.includes("/api/whales/settings")) return json(settings);
      if (url.includes("/api/whales/markets?")) return json({ total: 0, items: [] });
      if (url.includes("/api/whales/history?")) return json({ total: 0, items: [] });
      return json({});
    }));
    const view = render(<WhaleDiscoveryWorkspace />);
    const checkbox = await screen.findByRole("checkbox", { name: "仅显示均价在策略区间内" });
    expect(checkbox).toBeChecked();
    await waitFor(() => expect(requests.some((url) => url.includes("rule=new_account") && url.includes("filter_strategy_price=true"))).toBe(true));
    expect(await screen.findByText("当前策略均价区间内暂无持仓信号")).toBeInTheDocument();
    await user.click(checkbox);
    await waitFor(() => expect(requests.some((url) => url.includes("filter_strategy_price=false"))).toBe(true));
    await user.click(screen.getByRole("button", { name: /全量超大额/ }));
    await waitFor(() => expect(requests.some((url) => url.includes("rule=large_amount") && url.includes("filter_strategy_price=false"))).toBe(true));
    expect(checkbox).not.toBeChecked();
    expect(requests.some((url) => url.includes("/history?rule=new_account") && url.includes("filter_strategy_price=true"))).toBe(true);
    expect(requests.some((url) => url.includes("/history?rule=large_amount") && url.includes("filter_strategy_price=false"))).toBe(true);
    view.unmount();
    requests.length = 0;
    render(<WhaleDiscoveryWorkspace />);
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "仅显示均价在策略区间内" })).not.toBeChecked());
    await waitFor(() => expect(requests.some((url) => url.includes("filter_strategy_price=false"))).toBe(true));
    expect(requests.some((url) => url.includes("filter_strategy_price=true"))).toBe(false);
  });
});


it.each(["markets", "history"])("切换均价筛选会取消 %s 旧请求，迟到的错误不覆盖新列表", async (endpoint) => {
  const user = userEvent.setup();
  let finishOld: ((response: Response) => void) | undefined;
  let oldSignal: AbortSignal | null | undefined;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/whales/settings")) return json(settings);
    if (url.includes(`/api/whales/${endpoint}?`) && url.includes("filter_strategy_price=true")) {
      oldSignal = init?.signal;
      return await new Promise<Response>((resolve) => { finishOld = resolve; });
    }
    return json({ total: 0, items: [] });
  }));
  render(<WhaleDiscoveryWorkspace />);
  await waitFor(() => expect(finishOld).toBeDefined());
  await user.click(screen.getByRole("checkbox", { name: "仅显示均价在策略区间内" }));
  expect(oldSignal?.aborted).toBe(true);
  expect(await screen.findByText("暂未发现仍在持有的巨鲸钱包")).toBeInTheDocument();
  await act(async () => { finishOld?.(json({ detail: "旧筛选请求失败" }, 500)); });
  expect(screen.queryByText("旧筛选请求失败")).not.toBeInTheDocument();
  expect(screen.getByText("暂未发现仍在持有的巨鲸钱包")).toBeInTheDocument();
});
