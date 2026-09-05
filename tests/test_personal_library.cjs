const { test } = require("node:test");
const assert = require("node:assert/strict");
const lib = require("../site/personal-library.js");
const { fakeClient, tick, deferred } = require("./helpers/personal_fake.cjs");
const A = { id: "user-a", email: "a@example.test" }, B = { id: "user-b", email: "b@example.test" };

test("public config rejects privileged keys, foreign hosts and partial settings", () => {
  const good = { url: "https://abcdefgh.supabase.co/", publishableKey: "sb_publishable_" + "x".repeat(25) };
  assert.equal(lib.validateConfig(good).url, "https://abcdefgh.supabase.co");
  for (const bad of [null, {}, { ...good, url: "http://abcdefgh.supabase.co" },
    { ...good, url: "https://abcdefgh.supabase.co.evil.test" }, { ...good, url: good.url + "?x=1" },
    { ...good, publishableKey: "sb_secret_" + "a".repeat(25) }, { ...good, publishableKey: "eyJ.fake.jwt" }]) {
    assert.throws(() => lib.validateConfig(bad));
  }
});
test("bookmark IDs and presets are normalized without identities or result snapshots", () => {
  assert.equal(lib.paperId("2609.03115v2"), "2609.03115");
  assert.equal(lib.paperId("cond-mat/9901001v3"), "cond-mat/9901001");
  assert.throws(() => lib.paperId("<script>"));
  assert.throws(() => lib.presetName(" "));
  const filters = lib.presetFilters({ user_id: "someone", results: ["secret"], from: "2026-02-30", to: "2026-09-05", minRating: "8", tag: " RATES ", query: "a".repeat(300) });
  assert.equal(filters.from, ""); assert.equal(filters.tag, "rates"); assert.equal(filters.query.length, 180);
  assert.equal(filters.minRating, "8"); assert.equal(filters.user_id, undefined); assert.equal(filters.results, undefined);
  assert.equal(lib.presetFilters({ from: "2026-09-05", to: "2026-09-01" }).from, "2026-09-01");
});
test("bookmarks and presets save per row, paginate, and refresh across devices", async () => {
  const client = fakeClient();
  client.tables.research_bookmarks = Array.from({ length: 215 }, (_, i) => ({ user_id: A.id, paper_id: "2609." + String(i).padStart(5, "0") }));
  client.tables.research_bookmarks.push({ user_id: B.id, paper_id: "2609.99999" });
  const store = lib.createStore(client); client.emit(A); await tick();
  assert.equal(store.snapshot().bookmarks.length, 215);
  await store.setBookmark("2609.03115v2", true);
  assert.ok(store.snapshot().bookmarks.includes("2609.03115"));
  assert.equal(client.writes[0].options.onConflict, "user_id,paper_id");
  await store.setBookmark("2609.03115v1", false);
  assert.ok(!store.snapshot().bookmarks.includes("2609.03115"));
  await store.savePreset("Rates", { minRating: "8", tag: "rates" });
  const preset = store.snapshot().presets[0];
  assert.equal(preset.filters.minRating, "8");
  await store.deletePreset(preset.id); assert.equal(store.snapshot().presets.length, 0);
  client.tables.research_bookmarks.push({ user_id: A.id, paper_id: "2609.12345" });
  await store.refresh(); assert.ok(store.snapshot().bookmarks.includes("2609.12345"));
  assert.ok(client.queries.every(q => q.filters.user_id === A.id || q.row?.user_id === A.id));
  store.dispose();
});
test("read/write failures stay unconfirmed and recover with sync", async () => {
  const client = fakeClient(), store = lib.createStore(client);
  client.failRead = true; client.emit(A); await tick();
  assert.equal(store.snapshot().phase, "error");
  await assert.rejects(store.setBookmark("2609.03115", true));
  client.failRead = false; await store.refresh();
  client.failWrite = true; await assert.rejects(store.setBookmark("2609.03115", true));
  assert.equal(store.snapshot().phase, "error"); assert.deepEqual(store.snapshot().bookmarks, []);
  client.failWrite = false; await store.refresh(); assert.equal(store.snapshot().phase, "ready"); store.dispose();
});
test("late account A data cannot appear in account B or after signout", async () => {
  const client = fakeClient(), store = lib.createStore(client), delayed = deferred();
  client.nextRead = delayed.promise; client.emit(A); await tick();
  client.emit(B); await tick();
  delayed.resolve({ data: [{ user_id: A.id, paper_id: "2609.03115" }] }); await tick();
  assert.equal(store.snapshot().user.id, B.id); assert.deepEqual(store.snapshot().bookmarks, []);
  await store.signOut(); assert.equal(store.snapshot().user, null); assert.deepEqual(store.snapshot().presets, []); store.dispose();
});
test("SIGNED_OUT invalidates pending initialization even when already signed out", async () => {
  const client = fakeClient(), pending = deferred(); client.sessionResult = pending.promise;
  const store = lib.createStore(client); const start = store.start();
  client.emit(null); pending.resolve({ data: { session: { user: A } } }); await start; await tick();
  assert.equal(store.snapshot().user, null); store.dispose();
});
test("token refresh cannot restore an account during an explicit logout", async () => {
  const client = fakeClient(), store = lib.createStore(client), delayed = deferred();
  client.emit(A); await tick(); client.logoutResult = delayed.promise;
  const logout = store.signOut(); client.emit(A, "TOKEN_REFRESHED"); await tick();
  assert.equal(store.snapshot().user, null);
  delayed.resolve({ error: new Error("offline") }); await assert.rejects(logout);
  assert.equal(store.snapshot().user, null); store.dispose();
});
test("malformed or cross-user server rows fail closed", async () => {
  const client = fakeClient(), store = lib.createStore(client);
  client.nextRead = Promise.resolve({ data: [{ user_id: B.id, paper_id: "2609.03115" }] });
  client.emit(A); await tick(); assert.equal(store.snapshot().phase, "error"); store.dispose();
});
