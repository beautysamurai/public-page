"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const script = fs.readFileSync(path.join(__dirname, "../site/research-status.js"), "utf8");

async function render(status, fail = false) {
  const dom = new JSDOM('<html lang="ja"><aside id="research-update-status" hidden></aside></html>', { url: "https://example.com/public-page/", runScripts: "outside-only" });
  dom.window.fetch = async () => ({ ok: !fail, json: async () => status });
  dom.window.eval(script);
  await new Promise((resolve) => setImmediate(resolve));
  return dom;
}

test("incomplete updates and period retries remain visible independently of completed editions", async () => {
  const dom = await render({ schemaVersion: 1, daily: { status: "UPDATE_NOT_CONFIRMED", pendingBatchDate: "2026-09-07", lastCompletedBatchDate: "2026-09-04" }, pendingPeriods: [{ reportKind: "weekly", periodEnd: "2026-09-04", ready: true }] });
  const notice = dom.window.document.getElementById("research-update-status");
  assert.equal(notice.hidden, false);
  assert.match(notice.textContent, /新着ゼロではありません/);
  assert.match(notice.textContent, /週次 2026-09-04/);
  assert.match(notice.querySelector("summary").textContent, /未完了のレビュー：1件/);
  assert.equal(notice.querySelector("details").open, false);
  assert.match(notice.querySelector("details").previousElementSibling.textContent, /新着ゼロではありません/);
  notice.querySelector("details").open = true;
  dom.window.document.documentElement.lang = "en";
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(notice.textContent, /not zero new papers/);
  assert.match(notice.querySelector("summary").textContent, /Pending reviews: 1/);
  assert.equal(notice.querySelector("details").open, true);
  dom.window.close();
});

test("status outage is not silently represented as no new papers", async () => {
  const dom = await render(null, true);
  const notice = dom.window.document.getElementById("research-update-status");
  assert.equal(notice.hidden, false);
  assert.match(notice.textContent, /取得できません/);
  dom.window.close();
});

test("untrusted status values cannot create markup or arbitrary period labels", async () => {
  const dom = await render({ schemaVersion: 1, daily: { pendingBatchDate: '<img src=x onerror="alert(1)">' }, pendingPeriods: [{ reportKind: "<script>", periodEnd: "2026-09-04" }] });
  assert.equal(dom.window.document.querySelector("img,script"), null);
  assert.equal(dom.window.document.getElementById("research-update-status").hidden, true);
  dom.window.close();
});
