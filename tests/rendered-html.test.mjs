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

test("server-renders the wallet monitor shell and product metadata", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(
    html,
    /<title>仓位观察｜Polymarket 钱包持仓监控<\/title>/i,
  );
  assert.match(html, /仓位观察/);
  assert.match(html, /看清持仓，安静跟随。/);
  assert.match(html, /添加钱包/);
  assert.match(html, /只读监控/);
  assert.match(html, /property="og:image"/i);
  assert.match(html, /http:\/\/localhost(?::3000)?\/og\.png/i);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton/i);
});

test("removes the disposable starter and keeps monitor behavior in the client", async () => {
  const [page, layout, css] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  await assert.rejects(
    access(new URL("app/_sites-preview/SkeletonPreview.tsx", templateRoot)),
  );
  await assert.rejects(
    access(new URL("app/_sites-preview/preview.css", templateRoot)),
  );

  assert.match(page, /NEXT_PUBLIC_API_BASE/);
  assert.match(page, /http:\/\/127\.0\.0\.1:8730/);
  assert.match(page, /new EventSource/);
  assert.match(page, /current_value\) - toNumber\(left\.current_value/);
  assert.match(page, /event\/\$\{eventPath\}\/\$\{encodeURIComponent\(marketSlug\)\}/);
  assert.match(page, /average_fill_price/);
  assert.match(page, /transaction_hash/);
  assert.match(layout, /lang="zh-CN"/);
  assert.doesNotMatch(layout, /next\/font|Starter Project|codex-preview/);
  assert.match(css, /@media \(max-width: 760px\)/);
  assert.match(css, /\.positionCards/);
});
