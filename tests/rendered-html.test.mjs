import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

const templateRoot = new URL("../", import.meta.url);

async function render(path = "/") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request(`http://localhost${path}`, {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the home workspace and product metadata", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(
    html,
    /<title>PolyCopy｜Polymarket 链上资金监测<\/title>/i,
  );
  assert.match(html, /PolyCopy/);
  assert.match(html, /链上监测/);
  assert.match(html, /成交记录/);
  assert.doesNotMatch(html, /我的跟单/);
  assert.doesNotMatch(html, /持仓管理|href="\/positions"/);
  assert.match(html, /跟单决策/);
  assert.doesNotMatch(html, /添加目标/);
  assert.doesNotMatch(html, /最近记录/);
  assert.doesNotMatch(html, />策略</);
  assert.match(html, /property="og:image"/i);
  assert.match(html, /http:\/\/localhost(?::3000)?\/og\.png/i);
  assert.match(
    html,
    /<link(?=[^>]*rel="icon")(?=[^>]*href="(?:https?:\/\/[^\"]+)?\/icon\.svg(?:\?[^\"]*)?")[^>]*>/i,
  );
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton/i);
});

test("keeps home, chain monitoring and execution settings in the client", async () => {
  const [page, whalePage, home, workspace, settings, layout, css, icon] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/whales/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/components/HomeWorkspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/components/WhaleDiscoveryWorkspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/components/ExecutionSettingsWorkspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFile(new URL("../app/icon.svg", import.meta.url), "utf8"),
  ]);

  await assert.rejects(
    access(new URL("app/_sites-preview/SkeletonPreview.tsx", templateRoot)),
  );
  await assert.rejects(
    access(new URL("app/_sites-preview/preview.css", templateRoot)),
  );

  assert.match(page, /HomeWorkspace/);
  assert.match(home, /api\/home\/overview/);
  assert.match(whalePage, /WhaleDiscoveryWorkspace/);
  assert.match(workspace, /whaleApi/);
  assert.match(workspace, /api\/whales\/markets/);
  assert.match(workspace, /api\/whales\/follow\/preview/);
  assert.match(settings, /api\/execution-account/);
  assert.doesNotMatch(settings, /copy-trading/);
  assert.match(layout, /lang="zh-CN"/);
  assert.doesNotMatch(layout, /next\/font|Starter Project|codex-preview/);
  assert.match(css, /@media \(max-width: 760px\)/);
  assert.match(css, /\.positionCards/);
  assert.match(icon, /viewBox="0 0 64 64"/);
  assert.match(icon, /polycopy-gradient/);
});


test("removed position-management route returns 404", async () => {
  const response = await render("/positions");
  assert.equal(response.status, 404);
});


test("server-renders follow records without duplicate position management", async () => {
  const response = await render("/whales/records");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /<h1>成交记录<\/h1>/);
  assert.match(html, /跟单汇总/);
  assert.match(html, /历史流水/);
  assert.doesNotMatch(html, /持仓管理|href="\/positions"/);
  assert.doesNotMatch(html, /当前持仓|暂无巨鲸跟单持仓|一键卖出|展开完整流水/);
});


test("email page is retired and settings contain no email controls", async () => {
  assert.equal((await render("/email-records")).status, 404);
  const response = await render("/settings");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.doesNotMatch(html, /邮件记录|SMTP|收件人|email-records|email-settings/);
});


test("settings centralize all configuration sections and business pages link to them", async () => {
  const settingsHtml = await (await render("/settings")).text();
  assert.match(settingsHtml, /aria-label="设置分区"/);
  for (const id of ["execution-wallet", "whale-monitor-settings", "auto-follow-settings", "chain-test"]) {
    assert.match(settingsHtml, new RegExp(`id="${id}"`));
    assert.match(settingsHtml, new RegExp(`href="#${id}"`));
  }
  assert.equal((settingsHtml.match(/<main\b/g) ?? []).length, 1);
  const monitorHtml = await (await render("/whales")).text();
  assert.match(monitorHtml, /href="\/settings#whale-monitor-settings"/);
  assert.doesNotMatch(monitorHtml, /保存监测条件|打开监测设置/);
  const autoHtml = await (await render("/whales/auto-follow")).text();
  assert.match(autoHtml, /href="\/settings#auto-follow-settings"/);
  assert.match(autoHtml, /<h1>跟单决策<\/h1>/);
  assert.match(autoHtml, /查看成交记录/);
  assert.match(autoHtml, /自动跟单决策/);
  assert.doesNotMatch(autoHtml, /编辑策略|保存自动跟单策略/);
  const legacy = await render("/whales/settings");
  assert.equal(legacy.status, 307);
  assert.equal(new URL(legacy.headers.get("location"), "http://localhost").href, "http://localhost/settings#whale-monitor-settings");
});
