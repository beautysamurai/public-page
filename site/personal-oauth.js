(function () {
  "use strict";
  // Runs before app.js reads the URL. Only a same-tab, user-started PKCE flow
  // may exchange a code; bearer tokens from URLs are never accepted.
  var base = new URL("./", window.location.href), config = null;
  try {
    if (window.RatesPersonalConfig) config = window.RatesPersonalLibrary.validateConfig(window.RatesPersonalConfig);
  } catch (_error) { /* The existing UI reports invalid public configuration. */ }
  var storageKey = config ? "rates-personal:" + new URL(config.url).hostname + ":" + base.pathname : "";
  var pendingKey = storageKey + ":github-pending";
  var authFields = ["code", "error", "error_code", "error_description", "sb_flow_id", "access_token",
    "refresh_token", "provider_token", "provider_refresh_token", "expires_in", "expires_at", "token_type"];
  var viewFields = ["edition", "lang", "archive-from", "archive-to", "archive-kind", "archive-view",
    "archive-rating", "archive-tag", "archive-sort", "archive-query", "archive-saved"];
  function returnUrl(value) {
    var url;
    try { url = new URL(value, base); } catch (_error) { return base.href; }
    if (url.origin !== base.origin || (url.pathname !== base.pathname && url.pathname !== base.pathname + "index.html")) return base.href;
    var safe = new URL(base);
    viewFields.forEach(function (key) {
      if (url.searchParams.has(key)) safe.searchParams.set(key, url.searchParams.get(key).slice(0, 180));
    });
    if (/^#(?:archive|digest|method|page-title|paper-list|research-tools|research-filters|personal-library|review-[a-z0-9.-]+)$/.test(url.hash)) safe.hash = url.hash;
    return safe.href;
  }
  function clearPending() {
    try { window.sessionStorage.removeItem(pendingKey); } catch (_error) { /* No persistent intent to clear. */ }
  }
  function validFlowId(value) { return typeof value === "string" && /^[a-zA-Z0-9_-]{8,64}$/.test(value); }
  var callback = null;
  var incoming = new URL(window.location.href), fragment = new URLSearchParams(incoming.hash.slice(1));
  if (authFields.some(function (key) { return incoming.searchParams.has(key) || fragment.has(key); })) {
    var pending = null;
    try { if (config) pending = JSON.parse(window.sessionStorage.getItem(pendingKey)); } catch (_error) { /* Fail closed. */ }
    clearPending();
    var validPending = pending && validFlowId(pending.flowId) && Number.isFinite(pending.createdAt) &&
      Date.now() >= pending.createdAt && Date.now() - pending.createdAt < 10 * 60 * 1000;
    var code = incoming.searchParams.get("code");
    var failed = incoming.searchParams.has("error") || incoming.searchParams.has("error_code") ||
      authFields.some(function (key) { return fragment.has(key); });
    callback = validPending && !failed && typeof code === "string" && /^[a-zA-Z0-9._~-]{8,2048}$/.test(code)
      ? { code: code, flowId: pending.flowId } : { error: true };
    // Remove credentials/errors before any archive, language or share link is
    // constructed. Restore only our own allowlisted view, never a remote URL.
    window.history.replaceState(null, "", returnUrl(validPending ? pending.returnTo : incoming.href));
  }
  window.RatesPersonalOAuth = {
    storageKey: storageKey,
    clearPending: clearPending,
    takeCallback: function () { var result = callback; callback = null; return result; },
    begin: async function (client) {
      if (!config || !window.crypto || !window.crypto.subtle) throw new Error("Secure GitHub sign-in is unavailable.");
      var destination = returnUrl(window.location.href);
      // Do not navigate if this browser cannot retain the one-time intent.
      window.sessionStorage.setItem(pendingKey, "{}");
      try {
        var result = await client.auth.signInWithOAuth({ provider: "github", options: {
          redirectTo: base.href, scopes: "user:email", skipBrowserRedirect: true
        } });
        if (result.error || !result.data || !validFlowId(result.data.flowId)) throw new Error("GitHub sign-in could not start.");
        var authorize = new URL(result.data.url);
        if (authorize.origin !== config.url || authorize.pathname !== "/auth/v1/authorize" ||
            authorize.username || authorize.password || authorize.searchParams.get("provider") !== "github" ||
            authorize.searchParams.get("code_challenge_method") !== "s256" || !authorize.searchParams.get("code_challenge")) {
          throw new Error("Unexpected sign-in destination.");
        }
        window.sessionStorage.setItem(pendingKey, JSON.stringify({ createdAt: Date.now(),
          returnTo: destination, flowId: result.data.flowId }));
        return authorize.href;
      } catch (error) { clearPending(); throw error; }
    }
  };
})();
