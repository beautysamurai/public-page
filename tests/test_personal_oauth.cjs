const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const { webcrypto, createHash } = require("node:crypto");
const lib = require("../site/personal-library.js");
const source = fs.readFileSync(path.join(__dirname, "../site/personal-oauth.js"), "utf8");
const base = "https://example.test/public-page/";
const storageKey = "rates-personal:abcdefgh.supabase.co:/public-page/";
const pendingKey = storageKey + ":github-pending";
function environment(url = base, entries = []) {
  const values = new Map(entries);
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
  const w = { location: { href: url }, crypto: webcrypto, sessionStorage: storage,
    RatesPersonalLibrary: lib, RatesPersonalConfig: { url: "https://abcdefgh.supabase.co", publishableKey: "sb_publishable_" + "x".repeat(25) } };
  w.history = { replaceState: (_state, _title, value) => { w.location.href = String(value); } };
  vm.runInNewContext(source, { window: w, URL, URLSearchParams, Date });
  return { w, storage, values, api: w.RatesPersonalOAuth };
}
function fake(result) {
  return { auth: { signInWithOAuth: async () => result || { data: { flowId: "test-flow-12345",
    url: "https://abcdefgh.supabase.co/auth/v1/authorize?provider=github&code_challenge=test&code_challenge_method=s256" } } } };
}
test("OAuth stores a short-lived same-tab return target without leaking it to the provider", async () => {
  const e = environment(base + "?edition=2026-09-01-daily-openai-01&lang=en&next=https://evil.test#review-2608.29423");
  const url = await e.api.begin(fake());
  const pending = JSON.parse(e.values.get(pendingKey));
  assert.equal(pending.flowId, "test-flow-12345");
  assert.equal(pending.returnTo, base + "?edition=2026-09-01-daily-openai-01&lang=en#review-2608.29423");
  assert.ok(!url.includes("edition="));
  const callback = environment(base + "?code=valid-code-123", [...e.values]);
  assert.equal(callback.w.location.href, pending.returnTo);
  assert.equal(callback.api.takeCallback().code, "valid-code-123");
  assert.equal(callback.api.takeCallback(), null);
  assert.equal(callback.values.has(pendingKey), false);
});
test("unsafe provider URLs and unavailable storage cannot start OAuth", async () => {
  for (const url of ["https://evil.test/auth/v1/authorize?provider=github", "javascript:alert(1)",
    "https://abcdefgh.supabase.co/auth/v1/authorize?provider=github&code_challenge=x&code_challenge_method=plain"]) {
    const e = environment();
    await assert.rejects(e.api.begin(fake({ data: { url, flowId: "test-flow-12345" } })));
    assert.equal(e.values.has(pendingKey), false);
  }
  const e = environment(); let called = false;
  e.w.sessionStorage.setItem = () => { throw new Error("disabled"); };
  await assert.rejects(e.api.begin({ auth: { signInWithOAuth: () => { called = true; } } }));
  assert.equal(called, false);
});
test("callback return paths cannot leave this site or carry credentials", () => {
  for (const returnTo of ["https://evil.test/", "//evil.test/", "/other-project/", "javascript:alert(1)"]) {
    const e = environment(base + "?code=valid-code-123", [[pendingKey, JSON.stringify({ flowId: "test-flow-12345", createdAt: Date.now(), returnTo })]]);
    assert.equal(e.w.location.href, base);
  }
  const e = environment(base + "?code=valid-code-123", [[pendingKey, JSON.stringify({ flowId: "test-flow-12345", createdAt: Date.now(),
    returnTo: base + "?access_token=secret&refresh_token=secret&lang=en#access_token=secret" })]]);
  assert.equal(e.w.location.href, base + "?lang=en");
});
test("installed Supabase SDK performs S256 PKCE and exchanges the selected flow without real requests", async () => {
  const { createClient } = require("@supabase/supabase-js");
  const e = environment(), session = new Map(), requests = [];
  const client = createClient(e.w.RatesPersonalConfig.url, e.w.RatesPersonalConfig.publishableKey, {
    auth: { storageKey, flowType: "pkce", detectSessionInUrl: false, autoRefreshToken: false,
      storage: { getItem: key => session.get(key) ?? null, setItem: (key, value) => session.set(key, value), removeItem: key => session.delete(key) } },
    global: { fetch: async (input, options) => {
      const url = new URL(input); requests.push({ url, body: JSON.parse(options.body) });
      assert.equal(url.origin, e.w.RatesPersonalConfig.url);
      assert.equal(url.pathname, "/auth/v1/token");
      return new Response(JSON.stringify({ access_token: "test-access-token", refresh_token: "test-refresh-token", token_type: "bearer", expires_in: 3600,
        user: { id: "00000000-0000-4000-8000-000000000001", email: "test@example.test" } }), { status: 200, headers: { "Content-Type": "application/json" } });
    } }
  });
  const destination = new URL(await e.api.begin(client));
  assert.equal(destination.searchParams.get("scopes"), "user:email");
  assert.equal(destination.searchParams.get("redirect_to"), base);
  assert.equal(destination.searchParams.get("code_challenge_method"), "s256");
  assert.equal(requests.length, 0, "start does not send personal data or read repositories");
  const pending = JSON.parse(e.values.get(pendingKey));
  const result = await client.auth.exchangeCodeForSession("fixture-code", { flowId: pending.flowId });
  assert.equal(result.error, null);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url.searchParams.get("grant_type"), "pkce");
  assert.equal(requests[0].body.auth_code, "fixture-code");
  assert.equal(createHash("sha256").update(requests[0].body.code_verifier).digest("base64url"), destination.searchParams.get("code_challenge"));
  assert.equal(result.data.session.user.email, "test@example.test");
  assert.equal(session.has(storageKey + "-flow-" + pending.flowId + "-code-verifier"), false);
});
