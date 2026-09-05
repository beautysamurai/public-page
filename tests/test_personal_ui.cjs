const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path");
const { JSDOM } = require("jsdom");
const { fakeClient, tick, deferred } = require("./helpers/personal_fake.cjs");
const site = path.join(__dirname, "../site");
async function setup(configured, restore = false, query = "?archive-view=papers") {
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
    return { ok: true, json: async () => JSON.parse(fs.readFileSync(path.join(site, relative), "utf8")) };
  };
  w.RatesCreateSupabaseClient = () => { creates++; return client; };
  w.RatesPersonalConfig = configured ? { url: "https://abcdefgh.supabase.co", publishableKey: "sb_publishable_" + "x".repeat(25) } : null;
  if (restore) {
    w.localStorage.setItem("rates-personal:abcdefgh.supabase.co:/public-page/", "fake-login");
    client.user = { id: "user-a", email: "a@example.test" };
  }
  for (const name of ["i18n.js", "archive-ui.js", "tex-math.js", "personal-library.js", "personal-ui.js", "app.js"]) {
    w.eval(fs.readFileSync(path.join(site, name), "utf8"));
  }
  await tick(); await tick();
  return { dom, w, client, scrolls, creates: () => creates, id: id => w.document.getElementById(id),
    submit: id => w.document.getElementById(id).dispatchEvent(new w.Event("submit", { bubbles: true, cancelable: true })) };
}

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
      assert.equal(page.w.document.activeElement, configured ? page.id("personal-email") : page.id("personal-library").querySelector("summary"));
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
