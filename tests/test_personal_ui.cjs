const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path");
const { JSDOM } = require("jsdom");
const { fakeClient, tick, deferred } = require("./helpers/personal_fake.cjs");
const site = path.join(__dirname, "../site");
async function setup(configured, restore = false, query = "?archive-view=papers", reportGate = null, transform = null, prepare = null) {
  const dom = new JSDOM(fs.readFileSync(path.join(site, "index.html"), "utf8"), {
    url: "https://example.test/public-page/" + query, runScripts: "outside-only"
  });
  const w = dom.window, client = fakeClient();
  let creates = 0;
  const scrolls = [];
  w.HTMLElement.prototype.scrollIntoView = function () { scrolls.push(this.id); };
  w.fetch = async url => {
    const relative = new URL(url).pathname.replace(/^\/public-page\//, "");
    if (!relative.startsWith("data/")) throw new Error("Unexpected request");
    if ((relative === "data/latest.json" || /^data\/archive\/(?!index\.json)/.test(relative)) && reportGate) await reportGate;
    return { ok: true, json: async () => {
      const value = JSON.parse(fs.readFileSync(path.join(site, relative), "utf8"));
      return transform ? transform(value, relative) : value;
    } };
  };
  w.RatesCreateSupabaseClient = (_url, _key, options) => { creates++; client.options = options; return client; };
  w.RatesPersonalConfig = configured ? { url: "https://abcdefgh.supabase.co", publishableKey: "sb_publishable_" + "x".repeat(25) } : null;
  if (restore) {
    w.localStorage.setItem("rates-personal:abcdefgh.supabase.co:/public-page/", "fake-login");
    client.user = { id: "user-a", email: "a@example.test" };
  }
  if (prepare) prepare(w, client);
  for (const name of ["i18n.js", "archive-ui.js", "tex-math.js", "personal-library.js", "personal-oauth.js", "personal-ui.js", "app.js"]) {
    w.eval(fs.readFileSync(path.join(site, name), "utf8"));
  }
  await tick(); await tick();
  return { dom, w, client, scrolls, creates: () => creates, id: id => w.document.getElementById(id),
    submit: id => w.document.getElementById(id).dispatchEvent(new w.Event("submit", { bubbles: true, cancelable: true })) };
}

test("paper cards separate actual API metadata and label historical usage as unrecorded", async () => {
  for (const recorded of [false, true]) {
    const page = await setup(false, false, "?edition=2026-09-01-daily-openai-01", null, (value, relative) => {
      if (recorded && relative.endsWith("2026-09-01-daily-openai-01.json")) {
        value.papers[0].schedulerSummary += "\n\n論文解析: gpt-5.6-sol / medium · トークン 入力 1,000 / 出力 300（うち推論 100） · 全51ページ（50ページ超）：abstractとIntroductionのみ。全文未解析";
      }
      return value;
    });
    try {
      const notes = [...page.w.document.querySelectorAll(".research-run-note")];
      assert.ok(notes.length > 0);
      if (recorded) {
        assert.ok(notes.some(n => n.textContent.includes("gpt-5.6-sol / medium")));
        assert.ok(notes.some(n => n.textContent.includes("全文未解析")));
      } else {
        assert.ok(notes.every(n => n.textContent.includes("未記録")));
      }
      assert.ok(notes.every(n => n.children.length === 0), "metadata stays plain text, not HTML");
    } finally { page.dom.window.close(); }
  }
});

const githubPendingKey = "rates-personal:abcdefgh.supabase.co:/public-page/:github-pending";
function seedGithub(w, changes = {}) {
  w.sessionStorage.setItem(githubPendingKey, JSON.stringify({ flowId: "test-flow-12345", createdAt: Date.now(),
    returnTo: "https://example.test/public-page/?edition=2026-09-01-daily-openai-01&lang=en&archive-view=papers&archive-rating=8#review-2608.29423", ...changes }));
}

test("GitHub is primary, email stays optional, and normal reading makes no auth request", async () => {
  const page = await setup(true);
  try {
    assert.equal(page.creates(), 0);
    assert.equal(page.id("personal-github-login").hidden, false);
    assert.equal(page.id("personal-email-option").open, false);
    assert.ok(page.id("personal-email-option").contains(page.id("personal-login")));
    assert.equal(page.id("personal-library").open, false);
    assert.ok(page.id("personal-github").textContent.includes("GitHub"));
  } finally { page.dom.window.close(); }
});

test("GitHub callback exchanges exactly once, restores review/filter/language and syncs only its user", async () => {
  const page = await setup(true, false, "?code=one-time-code-123", null, null, (w, client) => {
    seedGithub(w);
    client.tables.research_bookmarks = [{ user_id: "user-a", paper_id: "2608.29423" }, { user_id: "user-b", paper_id: "2608.30321" }];
  });
  try {
    await tick();
    assert.deepEqual(page.client.exchanges.map(e => e.code), ["one-time-code-123"]);
    assert.equal(page.client.exchanges[0].options.flowId, "test-flow-12345");
    assert.equal(page.client.options.auth.flowType, "pkce");
    assert.equal(page.client.options.auth.detectSessionInUrl, false);
    assert.equal(page.id("personal-account").hidden, false);
    assert.equal(page.id("personal-github-login").hidden, true);
    assert.equal(page.id("personal-library").open, false);
    assert.equal(page.scrolls.at(-1), "review-2608.29423");
    assert.equal(new URL(page.w.location.href).searchParams.get("lang"), "en");
    assert.equal(new URL(page.w.location.href).searchParams.get("archive-rating"), "8");
    assert.equal(page.w.sessionStorage.getItem(githubPendingKey), null);
    assert.equal(page.w.RatesPersonalOAuth.takeCallback(), null);
    for (const link of page.w.document.querySelectorAll("a[href]")) assert.ok(!link.href.includes("one-time-code"));
    assert.ok(!page.w.document.body.textContent.includes("one-time-code"));
    assert.ok(page.client.queries.every(q => q.filters.user_id === "user-a"));
    page.id("personal-signout").click(); await tick();
    assert.equal(page.id("personal-account").hidden, true);
    assert.equal(page.id("personal-github-login").hidden, false);
  } finally { page.dom.window.close(); }
});

test("unsolicited, expired, cancelled and implicit-token callbacks fail closed and scrub the URL", async () => {
  for (const [query, pending] of [
    ["?code=unknown-code-123", null],
    ["?code=expired-code-123", { createdAt: Date.now() - 11 * 60 * 1000 }],
    ["?error=access_denied&error_description=untrusted-secret", {}],
    ["#access_token=untrusted-secret&refresh_token=untrusted-secret", {}]
  ]) {
    const page = await setup(true, false, query, null, null, w => { if (pending) seedGithub(w, pending); });
    try {
      assert.equal(page.creates(), 0, "no exchange or remote auth for an invalid callback");
      assert.equal(page.id("personal-account").hidden, true);
      assert.equal(page.id("personal-library").open, true);
      assert.ok(page.id("personal-message").textContent.length > 0);
      assert.ok(!page.w.location.href.includes("code="));
      assert.ok(!page.w.location.href.includes("untrusted-secret"));
      assert.ok(!page.w.document.body.textContent.includes("untrusted-secret"));
    } finally { page.dom.window.close(); }
  }
});

test("provider failure is sanitized, and a late callback cannot replace an existing account", async () => {
  for (const restored of [false, true]) {
    const page = await setup(true, restored, "?code=callback-code-123", null, null, (w, client) => {
      seedGithub(w);
      client.exchangeResult = { error: new Error("sensitive-provider-response") };
    });
    try {
      assert.equal((page.client.exchanges || []).length, restored ? 0 : 1);
      assert.ok(!page.w.document.body.textContent.includes("sensitive-provider-response"));
      assert.ok(page.id("personal-message").textContent.includes("GitHub"));
      assert.equal(page.id("personal-account").hidden, !restored);
      assert.equal(page.id("personal-github").disabled, false);
    } finally { page.dom.window.close(); }
  }
});

test("paper titles, review buttons and other appearances link to the paper and preserve filters/language", async () => {
  const page = await setup(false, false, "?lang=en&archive-view=papers&archive-rating=6");
  try {
    const cards = [...page.id("archive-paper-list").children];
    assert.ok(cards.length > 0);
    for (const card of cards) {
      const id = card.querySelector("[data-bookmark-id]").dataset.bookmarkId;
      const links = [card.querySelector(".archive-paper-title a"), card.querySelector(".paper-actions a.paper-link"),
        ...card.querySelectorAll(".archive-paper-reviews a")];
      for (const link of links) {
        const url = new URL(link.href);
        assert.equal(url.hash, "#review-" + id.replace(/\//g, "-"));
        assert.ok(url.searchParams.get("edition"));
        assert.equal(url.searchParams.get("lang"), "en");
        assert.equal(url.searchParams.get("archive-rating"), "6");
      }
    }
  } finally { page.dom.window.close(); }
});

test("all archived papers have unique direct targets, preferring the review text", async () => {
  const catalogue = JSON.parse(fs.readFileSync(path.join(site, "data/papers.json"), "utf8"));
  for (const edition of catalogue.editions.filter(e => e.papers.length)) {
    const last = edition.papers.at(-1);
    const anchor = id => "review-" + id.replace(/v\d+$/i, "").toLowerCase().replace(/\//g, "-");
    const page = await setup(false, false, "?edition=" + edition.editionId + "#" + anchor(last.arxivId));
    try {
      for (const paper of edition.papers) {
        const target = page.id(anchor(paper.arxivId));
        assert.ok(target, edition.editionId + " / " + paper.arxivId);
        assert.ok(target.classList.contains("review-target"));
        assert.ok(page.id("source-document").contains(target) || page.id("paper-list").contains(target));
        if (edition.editionId.includes("openai")) {
          assert.ok(page.id("source-document").contains(target), "API review opens its full review section");
          assert.ok(target.textContent.includes(paper.title));
        }
      }
      const ids = [...page.w.document.querySelectorAll("[id]")].map(n => n.id);
      assert.equal(new Set(ids).size, ids.length);
      assert.equal(page.scrolls.at(-1), anchor(last.arxivId));
      assert.equal(page.w.document.activeElement, page.id(anchor(last.arxivId)));
      assert.equal(page.id("research-filters").open, false);
    } finally { page.dom.window.close(); }
  }
});

test("English review targets wait for text, and hash navigation reaches another paper", async () => {
  const gate = deferred();
  const page = await setup(false, false, "?edition=2026-09-01-daily-openai-01&lang=en#review-2608.29423", gate.promise);
  try {
    assert.equal(page.id("review-2608.29423"), null);
    gate.resolve(); await tick(); await tick();
    assert.equal(page.scrolls.at(-1), "review-2608.29423");
    assert.ok(page.id("source-document").contains(page.id("review-2608.29423")));
    assert.equal(page.id("source-document").lang, "en");
    page.w.location.hash = "review-2608.30321"; await tick();
    assert.equal(page.scrolls.at(-1), "review-2608.30321");
    assert.equal(page.w.document.activeElement, page.id("review-2608.30321"));
  } finally { gate.resolve(); page.dom.window.close(); }
});

test("late paper links cannot override subsequent navigation to tools", async () => {
  const gate = deferred();
  const page = await setup(true, false, "?edition=2026-09-01-daily-openai-01#review-2608.29423", gate.promise);
  try {
    page.id("archive-edit-filters").click();
    const priorScrolls = page.scrolls.length;
    gate.resolve(); await tick(); await tick();
    assert.equal(page.scrolls.length, priorScrolls);
    assert.equal(page.w.document.activeElement, page.id("research-filters-summary"));
  } finally { gate.resolve(); page.dom.window.close(); }
});

test("unmatched legacy prose falls back to its card and restores a filtered-out target", async () => {
  const page = await setup(false, false, "?edition=2026-09-01-daily-openai-01#review-2608.29423", null,
    (value, relative) => {
      if (relative.endsWith("2026-09-01-daily-openai-01.json")) value.sourceText = "## Legacy summary\n\nNo identifiable paper heading.";
      return value;
    });
  try {
    assert.ok(page.id("paper-list").contains(page.id("review-2608.29423")));
    assert.equal(page.scrolls.at(-1), "review-2608.29423");
    page.id("paper-search").value = "no matching title";
    page.id("paper-search").dispatchEvent(new page.w.Event("input"));
    assert.equal(page.id("review-2608.30321"), null);
    page.w.location.hash = "review-2608.30321"; await tick();
    assert.equal(page.scrolls.at(-1), "review-2608.30321");
    assert.equal(page.id("paper-search").value, "");
    const scrollCount = page.scrolls.length;
    page.w.location.hash = "review-missing"; await tick();
    assert.equal(page.scrolls.length, scrollCount, "unknown paper never redirects to an unrelated review");
  } finally { page.dom.window.close(); }
});

test("unconfigured and configured anonymous reading both remain available without auth requests", async () => {
  for (const configured of [false, true]) {
    const page = await setup(configured);
    try {
      assert.equal(page.creates(), 0);
      assert.ok(page.id("archive-paper-list").children.length > 0);
      assert.equal(page.id("personal-not-configured").hidden, configured);
      assert.equal(page.id("personal-login").hidden, !configured);
      assert.equal(page.id("personal-code-form").hidden, true);
      const button = page.w.document.querySelector("[data-bookmark-id]");
      button.click(); assert.equal(page.id("personal-library").open, true);
      assert.equal(page.scrolls.at(-1), "personal-library");
      assert.equal(page.w.document.activeElement, configured ? page.id("personal-github") : page.id("personal-library").querySelector("summary"));
      assert.equal(page.client.writes.length, 0);
      page.id("archive-saved-only").checked = true;
      page.id("archive-saved-only").dispatchEvent(new page.w.Event("change"));
      assert.equal(page.id("archive-paper-list").children.length, 0);
      assert.ok(page.id("archive-paper-status").textContent.includes("ログイン"));
    } finally { page.dom.window.close(); }
  }
});

test("OTP login, bookmark synchronization, saved filter, preset apply/delete, and logout", async () => {
  const page = await setup(true);
  try {
    page.id("personal-email").value = "a@example.test";
    page.submit("personal-login"); await tick();
    assert.equal(page.creates(), 1);
    assert.equal(page.client.requestedEmail, "a@example.test");
    assert.equal(page.id("personal-code-form").hidden, false);
    assert.equal(page.id("personal-send").disabled, true, "resend cooldown");
    page.id("personal-token").value = "12345678";
    page.submit("personal-code-form"); await tick(); await tick();
    assert.equal(page.client.verified.type, "email");
    assert.equal(page.id("personal-account").hidden, false);
    assert.equal(page.id("personal-code-form").hidden, true);
    const button = page.w.document.querySelector("#archive-paper-list [data-bookmark-id]");
    const savedId = button.dataset.bookmarkId;
    button.click(); await tick();
    const matching = page.w.document.querySelectorAll(`[data-bookmark-id="${savedId}"]`);
    for (const button of matching) assert.equal(button.getAttribute("aria-pressed"), "true");
    page.id("personal-show-saved").click(); await tick();
    assert.equal(page.id("archive-paper-list").children.length, 1);
    assert.equal(new URL(page.w.location.href).searchParams.get("archive-saved"), "1");
    assert.equal(page.w.location.hash, "#archive");
    assert.equal(page.w.document.activeElement, page.id("archive-title"));
    page.id("archive-clear").click();
    page.id("archive-rating").value = "8"; page.id("archive-rating").dispatchEvent(new page.w.Event("change"));
    page.id("personal-preset-name").value = "<img src=x onerror=alert(1)>";
    page.submit("personal-preset-form"); await tick();
    assert.equal(page.id("personal-presets").querySelectorAll("img").length, 0, "preset text is not HTML");
    assert.equal(page.client.tables.research_presets[0].filters.minRating, "8");
    page.id("archive-clear").click();
    page.id("personal-presets").querySelector(".personal-preset-apply").click();
    assert.equal(page.id("archive-rating").value, "8");
    assert.equal(page.scrolls.at(-1), "archive");
    page.id("personal-presets").querySelector(".personal-preset-delete").click(); await tick();
    assert.equal(page.client.tables.research_presets.length, 0);
    page.id("personal-signout").click(); await tick();
    assert.equal(page.id("personal-account").hidden, true);
    assert.equal(page.id("personal-identity").textContent, "");
    assert.equal(page.id("personal-presets").children.length, 0);
    assert.ok(!page.w.document.body.textContent.includes("a@example.test"));
    for (const button of page.w.document.querySelectorAll("[data-bookmark-id]")) assert.equal(button.getAttribute("aria-pressed"), "false");
  } finally { page.dom.window.close(); }
});

test("all filtering and login controls are unique, above the hero, and outside the sticky header", async () => {
  const page = await setup(true);
  try {
    const tools = page.id("research-tools"), hero = page.w.document.querySelector(".hero");
    assert.equal(tools.parentElement.tagName, "MAIN");
    assert.ok(tools.compareDocumentPosition(hero) & page.w.Node.DOCUMENT_POSITION_FOLLOWING);
    const ids = Array.from(page.w.document.querySelectorAll("[id]"), element => element.id);
    assert.equal(new Set(ids).size, ids.length);
    for (const id of ["archive-rating", "archive-tag", "archive-from", "archive-to", "archive-search", "archive-saved-only", "paper-search", "topic-filters", "personal-login", "personal-code-form", "personal-preset-form"]) {
      assert.ok(tools.contains(page.id(id)), id);
    }
    assert.ok(!tools.contains(page.id("archive-results")), "results stay visible outside collapsed tools");
    assert.ok(page.id("archive").contains(page.id("archive-results")));
    assert.ok(!page.w.document.querySelector(".site-header").contains(tools));
  } finally { page.dom.window.close(); }
});

test("home and shared filter URLs start collapsed even with a restored login", async () => {
  for (const query of ["", "?archive-view=papers&archive-rating=8&archive-saved=1#archive"]) {
    const page = await setup(true, true, query);
    try {
      assert.equal(page.id("research-filters").open, false);
      assert.equal(page.id("personal-library").open, false);
      page.client.emit(page.client.user, "TOKEN_REFRESHED"); await tick();
      assert.equal(page.id("personal-library").open, false, "background auth cannot open the panel");
      if (query) assert.equal(page.scrolls.at(-1), "archive", "existing fragment restoration is preserved");
    } finally { page.dom.window.close(); }
  }
});

test("typing keeps focus; explicit results navigation closes tools without resetting conditions", async () => {
  const page = await setup(true);
  try {
    page.id("research-filters").querySelector("summary").click();
    assert.equal(page.id("research-filters").open, true);
    const input = page.id("archive-search");
    input.focus(); input.value = "rates";
    const priorScrolls = page.scrolls.length;
    input.dispatchEvent(new page.w.Event("input"));
    assert.equal(page.w.document.activeElement, input);
    assert.equal(page.id("research-filters").open, true);
    assert.equal(page.scrolls.length, priorScrolls);
    page.id("archive-show-results").click();
    assert.equal(page.id("research-filters").open, false);
    assert.equal(page.w.location.hash, "#archive");
    assert.equal(page.w.document.activeElement, page.id("archive-title"));
    assert.equal(new URL(page.w.location.href).searchParams.get("archive-query"), "rates");
    page.id("archive-edit-filters").click();
    assert.equal(page.id("research-filters").open, true);
    assert.equal(page.w.document.activeElement, page.id("research-filters-summary"));
    assert.equal(input.value, "rates");
    page.id("research-filters").querySelector("summary").click();
    assert.equal(page.id("research-filters").open, false);
    assert.equal(page.client.writes.length, 0);
  } finally { page.dom.window.close(); }
});

test("explicit links open only the requested disclosure and current-issue navigation stays separate", async () => {
  for (const id of ["research-filters", "personal-library"]) {
    const page = await setup(true, false, "#" + id);
    try {
      assert.equal(page.id(id).open, true);
      assert.equal(page.id(id === "personal-library" ? "research-filters" : "personal-library").open, false);
      assert.equal(page.scrolls.at(-1), id);
      page.id("digest-show-results").click();
      assert.equal(page.w.location.hash, "#paper-list");
      assert.equal(page.w.document.activeElement, page.id("paper-list"));
      assert.equal(page.id(id).open, false);
    } finally { page.dom.window.close(); }
  }
});

test("late initial report loading cannot scroll away from explicitly opened top tools", async () => {
  for (const target of ["research-filters", "personal-library"]) {
    const gate = deferred(), page = await setup(true, false, "?archive-view=papers#archive", gate.promise);
    try {
      if (target === "research-filters") page.id("archive-edit-filters").click();
      else page.w.document.querySelector("#archive-paper-list [data-bookmark-id]").click();
      assert.equal(page.id(target).open, true);
      assert.equal(page.w.location.hash, "#" + target);
      const scrollCount = page.scrolls.length;
      gate.resolve(); await tick(); await tick();
      assert.equal(page.scrolls.length, scrollCount, "old #archive navigation must not be restored");
      assert.equal(page.w.document.activeElement, page.id(target === "research-filters" ? "research-filters-summary" : "personal-github"));
    } finally { gate.resolve(); page.dom.window.close(); }
  }
});

test("restored session, account switch, failed sync, and English controls", async () => {
  const page = await setup(true, true, "?archive-view=papers&archive-saved=1&lang=en");
  try {
    assert.equal(page.id("personal-account").hidden, false);
    assert.equal(page.id("personal-show-saved").textContent, "Show bookmarks across all dates");
    page.client.failRead = true;
    page.id("personal-refresh").click(); await tick();
    assert.ok(page.id("personal-status").textContent.includes("Sync failed"));
    assert.equal(page.id("archive-paper-list").children.length, 0);
    assert.equal(page.id("archive-results").textContent, "");
    assert.equal(page.id("personal-preset-save").disabled, true);
    page.client.failRead = false;
    page.client.emit({ id: "user-b", email: "b@example.test" });
    assert.equal(page.id("personal-identity").textContent, "b@example.test");
    assert.equal(page.id("personal-presets").children.length, 0);
    await tick(); assert.equal(page.id("personal-preset-save").disabled, false);
  } finally { page.dom.window.close(); }
});

test("an old OTP response cannot reopen a code prompt after another account transition", async () => {
  const page = await setup(true), pending = deferred();
  try {
    page.client.auth.signInWithOtp = () => pending.promise;
    page.id("personal-email").value = "a@example.test";
    page.submit("personal-login"); await tick();
    page.client.emit({ id: "user-b", email: "b@example.test" }); page.client.emit(null);
    pending.resolve({ error: null }); await tick();
    assert.equal(page.id("personal-email").value, "");
    assert.equal(page.id("personal-code-form").hidden, true);
    assert.equal(page.id("personal-message").textContent, "");
    assert.equal(page.id("personal-send").disabled, false);
  } finally { page.dom.window.close(); }
});
