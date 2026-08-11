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

test("server-renders the PolyCopy workspace and product metadata", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(
    html,
    /<title>PolyCopy｜专业 Polymarket 交易工具<\/title>/i,
  );
  assert.match(html, /PolyCopy/);
  assert.match(html, /总览/);
  assert.doesNotMatch(html, /跟单/);
  assert.match(html, /添加目标/);
  assert.match(html, /记录/);
  assert.match(html, /property="og:image"/i);
  assert.match(html, /http:\/\/localhost(?::3000)?\/og\.png/i);
  assert.match(
    html,
    /<link(?=[^>]*rel="icon")(?=[^>]*href="(?:https?:\/\/[^\"]+)?\/icon\.svg(?:\?[^\"]*)?")[^>]*>/i,
  );
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton/i);
});

test("keeps PolyCopy workspace behavior in the client", async () => {
  const [page, workspace, legacy, layout, css, icon] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/components/PolyCopyWorkspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/analysis/legacy.tsx", import.meta.url), "utf8"),
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

  assert.match(page, /PolyCopyWorkspace/);
  assert.match(workspace, /NEXT_PUBLIC_API_BASE/);
  assert.match(workspace, /copy-trading\/overview/);
  assert.match(workspace, /copy-trading\/orders/);
  assert.match(workspace, /跟单比例/);
  assert.match(legacy, /new EventSource/);
  assert.match(legacy, /event\/\$\{eventPath\}\/\$\{encodeURIComponent\(marketSlug\)\}/);
  assert.match(legacy, /average_fill_price/);
  assert.match(legacy, /transaction_hash/);
  assert.match(layout, /lang="zh-CN"/);
  assert.doesNotMatch(layout, /next\/font|Starter Project|codex-preview/);
  assert.match(css, /@media \(max-width: 760px\)/);
  assert.match(css, /\.positionCards/);
  assert.match(icon, /viewBox="0 0 64 64"/);
  assert.match(icon, /polycopy-gradient/);
});
