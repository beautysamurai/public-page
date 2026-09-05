const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path");
const { JSDOM } = require("jsdom");
const api = require("../site/classics.js");
const root = path.join(__dirname, "..");
const read = (name) => fs.readFileSync(path.join(root, "site", name), "utf8");
const data = JSON.parse(read("data/classics.json"));
const tick = () => new Promise((resolve) => setImmediate(resolve));
async function page(query = "", responses = [data]) {
  const dom = new JSDOM(read("classics/index.html"), { url: "https://example.org/public-page/classics/" + query, runScripts: "outside-only" });
  const calls = [];
  dom.window.fetch = async (url, options) => {
    calls.push({ url, options });
    const result = responses.shift();
    if (result instanceof Error) throw result;
    return { ok: true, json: async () => result };
  };
  for (const script of ["i18n.js", "static-page.js", "classics.js"]) dom.window.eval(read(script));
  await tick();
  return { dom, window: dom.window, document: dom.window.document, calls };
}
function change(window, id, value, type = "change") {
  const input = window.document.getElementById(id); input.value = value;
  input.dispatchEvent(new window.Event(type, { bubbles: true }));
}

test("curated records are bilingual, safe, distinct from arXiv editions, and explicit about book access", () => {
  assert.equal(api.validate(data), data);
  assert.equal(data.items.length, 17);
  assert.equal(data.items.filter((item) => item.kind === "paper").length, 10);
  assert.equal(data.items.filter((item) => item.kind === "book").length, 7);
  assert.ok(data.items.filter((item) => item.kind === "book").every((item) => item.freeUrl === null));
  assert.ok(data.items.every((item) => !("importance" in item) && !("citationCount" in item) && !("arxivId" in item)));
  for (const item of data.items) for (const field of ["why", "use", "prerequisite", "accessNote"]) {
    assert.ok(!/[\u3040-\u30ff\u3400-\u9fff]/u.test(item[field].en), item.id + field);
  }
  const bad = structuredClone(data); bad.items[0].freeUrl = "javascript:alert(1)";
  assert.throws(() => api.validate(bad));
  for (const link of ["http://example.org", "https://user:pass@example.org", "data:text/html,x", "/relative", null]) assert.equal(api.safeUrl(link), false);
  const duplicate = structuredClone(data); duplicate.items.push(duplicate.items[0]); assert.throws(() => api.validate(duplicate));
  const missing = structuredClone(data); delete missing.items[0].id; assert.throws(() => api.validate(missing));
  const untranslated = structuredClone(data); delete untranslated.items[0].why.en; assert.throws(() => api.validate(untranslated));
  const topic = structuredClone(data); topic.items[0].topics = ["unknown"]; assert.throws(() => api.validate(topic));
});

test("type, topic, full-text access, and bilingual search combine without changing catalogue order", () => {
  assert.equal(api.filter(data.items, { kind: "book" }, {}).length, 7);
  assert.deepEqual(api.filter(data.items, { kind: "book", access: "free" }, {}), []);
  assert.ok(api.filter(data.items, { topic: "rates", access: "publisher" }, {}).every((item) => item.topics.includes("rates") && !item.freeUrl));
  assert.equal(api.filter(data.items, { q: "SABR" }, {})[0].id, "hagan-smile-risk-2002");
  assert.ok(api.filter(data.items, { q: "逆選択" }, {}).length > 0);
  assert.equal(api.filter(data.items, { q: "  ShReVe " }, {})[0].id, "shreve-finance-ii-2004");
});

test("default shelf is collapsed, localized, accessible, and makes only one same-origin data request", async (t) => {
  const p = await page(); t.after(() => p.window.close());
  assert.equal(p.document.getElementById("classics-filters").open, false);
  assert.equal(p.document.querySelectorAll(".classic-card").length, 17);
  assert.equal(p.document.querySelectorAll(".classic-notes[open]").length, 0);
  assert.equal(p.document.documentElement.lang, "ja");
  assert.match(p.document.title, /定番論文/);
  assert.equal(p.document.getElementById("classics-list").getAttribute("aria-busy"), "false");
  assert.deepEqual(JSON.parse(JSON.stringify(p.calls)), [{ url: "../data/classics.json", options: { credentials: "omit" } }]);
  for (const link of p.document.querySelectorAll(".classic-links a")) {
    assert.match(link.href, /^https:\/\//); assert.equal(link.target, "_blank");
    assert.equal(link.rel, "noopener noreferrer");
  }
  const ids = [...p.document.querySelectorAll("[id]")].map((node) => node.id); assert.equal(new Set(ids).size, ids.length);
  for (const label of p.document.querySelectorAll("label")) assert.ok(p.document.getElementById(label.htmlFor));
  assert.equal(p.document.querySelector("[src*='personal']"), null);
});

test("filtering preserves focus, language, URL state, collapsed preference, and clear/empty behavior", async (t) => {
  const p = await page("?lang=en&kind=book"); t.after(() => p.window.close());
  assert.equal(p.document.querySelectorAll(".classic-card").length, 7);
  assert.equal(p.document.documentElement.lang, "en");
  assert.match(p.document.querySelector(".classic-card").textContent, /Book/);
  assert.ok(p.document.querySelector(".primary-nav a").href.includes("lang=en"));
  p.document.getElementById("classics-filters").open = true;
  const search = p.document.getElementById("classics-search"); search.focus();
  change(p.window, "classics-search", "<script>no-match</script>", "input");
  assert.equal(p.document.querySelectorAll(".classic-card").length, 0);
  assert.match(p.document.getElementById("classics-status").textContent, /No references match/);
  assert.equal(p.document.activeElement, search);
  assert.equal(p.document.getElementById("classics-filters").open, true);
  assert.equal(new URL(p.window.location.href).searchParams.get("lang"), "en");
  p.document.getElementById("classics-clear").click();
  assert.equal(p.document.querySelectorAll(".classic-card").length, 17);
  assert.equal(new URL(p.window.location.href).searchParams.get("kind"), null);
  assert.equal(new URL(p.window.location.href).searchParams.get("lang"), "en");
  p.window.history.pushState(null, "", "?lang=en&topic=rates&access=free");
  p.window.dispatchEvent(new p.window.PopStateEvent("popstate"));
  assert.equal(p.document.querySelectorAll(".classic-card").length, 1);
  assert.equal(p.document.querySelector(".classic-card").id, "hagan-smile-risk-2002");
});

test("invalid URL options fall back to visible All options and unknown search stays text", async (t) => {
  const p = await page("?kind=invalid&topic=all&access=unknown"); t.after(() => p.window.close());
  for (const id of ["classics-kind", "classics-topic", "classics-access"]) {
    assert.equal(p.document.getElementById(id).selectedIndex, 0);
  }
  assert.equal(p.document.querySelectorAll(".classic-card").length, 17);
});

test("failed fetch or invalid catalogue shows retry rather than false zero results", async (t) => {
  for (const failure of [new Error("offline"), { schemaVersion: 999 }]) {
    const p = await page("", [failure, data]); t.after(() => p.window.close());
    assert.equal(p.document.getElementById("classics-retry").hidden, false);
    assert.match(p.document.getElementById("classics-status").textContent, /読み込めません/);
    p.document.getElementById("classics-retry").click(); await tick();
    assert.equal(p.document.getElementById("classics-retry").hidden, true);
    assert.equal(p.document.querySelectorAll(".classic-card").length, 17);
  }
});

test("untrusted catalogue strings render as text, never as markup", async (t) => {
  const payload = structuredClone(data);
  payload.items[0].title = '<img src="https://example.com/track" onerror="alert(1)">';
  const p = await page("", [payload]); t.after(() => p.window.close());
  assert.equal(p.document.querySelectorAll(".classic-card img").length, 0);
  assert.equal(p.document.querySelector(".classic-card h2").textContent, payload.items[0].title);
});

test("all primary navigation surfaces reach the independent shelf and all its locale keys exist", async (t) => {
  for (const file of ["index.html", "theory/index.html", "theory/black-scholes/index.html", "theory/sabr/index.html", "theory/zabr/index.html", "theory/hjb/index.html"]) {
    const dom = new JSDOM(read(file), { url: "https://example.org/public-page/" + file }); t.after(() => dom.window.close());
    const link = dom.window.document.querySelector('[data-i18n="nav.classics"]');
    assert.ok(link, file); assert.equal(new URL(link.href).pathname, "/public-page/classics/");
  }
  const p = await page(); t.after(() => p.window.close());
  const copy = p.window.RatesI18n.copy;
  for (const node of p.document.querySelectorAll("[data-i18n]")) {
    assert.equal(typeof copy.ja[node.dataset.i18n], "string"); assert.equal(typeof copy.en[node.dataset.i18n], "string");
  }
  for (const topic of new Set(data.items.flatMap((item) => item.topics))) {
    assert.equal(typeof copy.ja["classics.topic." + topic], "string"); assert.equal(typeof copy.en["classics.topic." + topic], "string");
  }
});
