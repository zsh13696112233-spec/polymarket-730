import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

const templateRoot = new URL("../", import.meta.url);

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
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
  assert.match(html, /我的跟单/);
  assert.match(html, /自动跟单/);
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
