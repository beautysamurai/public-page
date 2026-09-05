(function () {
  "use strict";
  window.RatesPersonalUi = { create: function (hooks) {
    var lib = window.RatesPersonalLibrary, t = hooks.t;
    var byId = function (id) { return document.getElementById(id); };
    var panel = byId("personal-library"), login = byId("personal-login"), account = byId("personal-account");
    var email = byId("personal-email"), token = byId("personal-token");
    var message = byId("personal-message"), status = byId("personal-status"), presets = byId("personal-presets");
    var config = null, configInvalid = false, store = null, client = null, initPromise = null;
    var authBusy = false, authGeneration = 0, pendingEmail = "", messageKey = "", resendAt = 0;
    var snapshot = { user: null, phase: "signed-out", busy: false, bookmarks: [], presets: [] };
    var storageKey = "";
    try {
      if (window.RatesPersonalConfig) {
        config = lib.validateConfig(window.RatesPersonalConfig);
        storageKey = "rates-personal:" + new URL(config.url).hostname + ":" + window.location.pathname;
      }
    } catch (_error) { configInvalid = true; }

    function node(tag, text, className) {
      var result = document.createElement(tag);
      if (text !== undefined) result.textContent = text;
      if (className) result.className = className;
      return result;
    }
    function ready() { return snapshot.phase === "ready" && !!snapshot.user; }
    function saved(id) { return snapshot.bookmarks.includes(lib.paperId(id)); }
    function openPanel() {
      panel.open = true;
      panel.scrollIntoView({ block: "center", behavior: "smooth" });
      (snapshot.user ? byId("personal-refresh") : email).focus({ preventScroll: true });
    }
    function updateButton(button) {
      var isSaved = ready() && saved(button.dataset.bookmarkId);
      button.textContent = t(snapshot.user && !ready() ? "personal.bookmarkPending" : isSaved ? "personal.saved" : "personal.save");
      button.setAttribute("aria-pressed", String(isSaved));
      button.disabled = !!snapshot.user && (!ready() || snapshot.busy);
      button.title = t(snapshot.user ? "personal.bookmarkHelp" : "personal.loginHelp");
    }
    function render() {
      login.hidden = !!snapshot.user || !config;
      byId("personal-code-form").hidden = !!snapshot.user || !config || !pendingEmail;
      account.hidden = !snapshot.user;
      byId("personal-not-configured").hidden = !!config;
      byId("personal-not-configured").textContent = t(configInvalid ? "personal.configInvalid" : "personal.notConfigured");
      byId("personal-identity").textContent = snapshot.user ? snapshot.user.email : "";
      status.textContent = t("personal.phase." + snapshot.phase);
      message.textContent = messageKey ? t(messageKey) : "";
      byId("personal-send").disabled = authBusy || Date.now() < resendAt;
      byId("personal-verify").disabled = authBusy || !pendingEmail;
      email.disabled = authBusy;
      token.disabled = authBusy || !pendingEmail;
      byId("personal-refresh").disabled = snapshot.busy || snapshot.phase === "loading";
      byId("personal-signout").disabled = authBusy;
      byId("personal-preset-save").disabled = !ready() || snapshot.busy;
      byId("personal-show-saved").disabled = !ready();
      byId("personal-export").disabled = !ready() || snapshot.busy;
      document.querySelectorAll("[data-bookmark-id]").forEach(updateButton);
      // Clearing this list on every account transition prevents personal names
      // from lingering after logout, including when network requests fail.
      presets.replaceChildren();
      if (ready()) {
        if (!snapshot.presets.length) presets.appendChild(node("li", t("personal.noPresets")));
        snapshot.presets.forEach(function (preset) {
          var row = node("li"), apply = node("button", preset.name), remove = node("button", t("personal.delete"));
          apply.type = remove.type = "button";
          apply.className = "personal-preset-apply";
          remove.className = "personal-preset-delete";
          remove.setAttribute("aria-label", t("personal.deletePreset", { name: preset.name }));
          remove.disabled = snapshot.busy;
          apply.addEventListener("click", function () { hooks.applyFilters(preset.filters); });
          remove.addEventListener("click", function () { runMutation(function () { return store.deletePreset(preset.id); }); });
          row.append(apply, remove); presets.appendChild(row);
        });
      }
    }
    function onChange(next) {
      var previousUser = snapshot.user && snapshot.user.id;
      snapshot = next;
      if (previousUser !== (next.user && next.user.id)) {
        authGeneration += 1; pendingEmail = ""; token.value = ""; email.value = "";
        byId("personal-preset-name").value = ""; messageKey = "";
      }
      render(); hooks.onChange();
    }
    async function ensureClient() {
      if (store) return store;
      if (!config) throw new Error("Sync is not configured.");
      if (initPromise) return initPromise;
      initPromise = (async function () {
        if (!window.RatesCreateSupabaseClient) await new Promise(function (resolve, reject) {
          var script = document.createElement("script");
          var timer = setTimeout(function () { script.remove(); reject(new Error("Login client timed out.")); }, 20000);
          script.src = new URL("./vendor/supabase.js", document.baseURI).href;
          script.onload = function () { clearTimeout(timer); resolve(); };
          script.onerror = function () { clearTimeout(timer); script.remove(); reject(new Error("Could not load the login client.")); };
          document.head.appendChild(script);
        });
        client = window.RatesCreateSupabaseClient(config.url, config.publishableKey, {
          auth: { storageKey: storageKey, persistSession: true, autoRefreshToken: true, detectSessionInUrl: false },
          global: { fetch: function (input, options) {
            var request = Object.assign({}, options);
            var timeout = AbortSignal.timeout(20000);
            request.signal = request.signal ? AbortSignal.any([request.signal, timeout]) : timeout;
            return window.fetch(input, request);
          } }
        });
        store = lib.createStore(client, onChange);
        await store.start();
        return store;
      })();
      try { return await initPromise; } finally { initPromise = null; }
    }
    async function runMutation(operation) {
      var user = snapshot.user && snapshot.user.id;
      var generation = authGeneration;
      messageKey = "";
      try { await operation(); }
      catch (_error) { if (generation === authGeneration && snapshot.user && snapshot.user.id === user) messageKey = "personal.saveFailed"; }
      render();
    }
    login.addEventListener("submit", async function (event) {
      event.preventDefault();
      if (authBusy || Date.now() < resendAt || !login.reportValidity()) return;
      var requestedEmail = email.value.trim();
      var generation = authGeneration;
      authBusy = true; messageKey = ""; render();
      try {
        await ensureClient();
        if (snapshot.user || generation !== authGeneration) return;
        var result = await client.auth.signInWithOtp({ email: requestedEmail, options: { shouldCreateUser: true } });
        if (result.error) throw result.error;
        if (snapshot.user || generation !== authGeneration) return;
        pendingEmail = requestedEmail;
        messageKey = "personal.codeSent";
        resendAt = Date.now() + 60000;
        setTimeout(render, 60100);
        token.value = "";
      } catch (_error) { if (generation === authGeneration) messageKey = "personal.authFailed"; }
      finally { authBusy = false; render(); if (generation === authGeneration && pendingEmail) token.focus(); }
    });
    byId("personal-code-form").addEventListener("submit", async function (event) {
      event.preventDefault();
      if (authBusy || !pendingEmail || !event.currentTarget.reportValidity()) return;
      var generation = authGeneration;
      authBusy = true; messageKey = ""; render();
      try {
        var result = await client.auth.verifyOtp({ email: pendingEmail, token: token.value.trim(), type: "email" });
        if (result.error) throw result.error;
      } catch (_error) { if (generation === authGeneration) messageKey = "personal.codeFailed"; }
      finally { authBusy = false; token.value = ""; render(); }
    });
    email.addEventListener("input", function () {
      if (pendingEmail && email.value.trim() !== pendingEmail) { pendingEmail = ""; token.value = ""; messageKey = ""; render(); }
    });
    byId("personal-refresh").addEventListener("click", function () { messageKey = ""; if (store) store.refresh(); });
    byId("personal-signout").addEventListener("click", async function () {
      authGeneration += 1; authBusy = true;
      try { await store.signOut(); }
      catch (_error) { messageKey = "personal.signoutLocal"; }
      finally {
        // If the remote logout fails, never restore this browser's old session.
        try { window.localStorage.removeItem(storageKey); } catch (_error) { /* storage can be unavailable */ }
        authBusy = false; render();
      }
    });
    byId("personal-preset-form").addEventListener("submit", async function (event) {
      event.preventDefault();
      if (!ready() || snapshot.busy || !event.currentTarget.reportValidity()) return;
      var generation = authGeneration;
      await runMutation(function () { return store.savePreset(byId("personal-preset-name").value, hooks.getFilters()); });
      if (generation === authGeneration && ready()) byId("personal-preset-name").value = "";
    });
    byId("personal-show-saved").addEventListener("click", function () {
      // Explicitly start across all dates/types, not the currently selected issue.
      hooks.applyFilters(lib.presetFilters({ savedOnly: true }));
    });
    byId("personal-export").addEventListener("click", function () {
      if (!ready()) return;
      var data = { version: 1, bookmarks: snapshot.bookmarks, presets: snapshot.presets };
      var url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      var link = node("a"); link.href = url; link.download = "research-personal-library.json";
      link.click(); setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    });
    // No external request for anonymous readers. Restore only a previously
    // chosen login; focus refreshes pick up edits made on a different device.
    try { if (config && window.localStorage.getItem(storageKey)) ensureClient().catch(function () { messageKey = "personal.authFailed"; render(); }); }
    catch (_error) { /* Login can still work without persistent browser storage. */ }
    var lastRefresh = 0;
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible" && store && snapshot.user && !snapshot.busy && Date.now() - lastRefresh > 30000) {
        lastRefresh = Date.now(); store.refresh();
      }
    });
    render();
    return {
      ready: ready, saved: saved, open: openPanel,
      render: render,
      bookmarkButton: function (rawId) {
        var id;
        try { id = lib.paperId(rawId); } catch (_error) { return null; }
        var button = node("button", "", "bookmark-button");
        button.type = "button"; button.dataset.bookmarkId = id;
        updateButton(button);
        button.addEventListener("click", function () {
          if (!ready()) { openPanel(); return; }
          runMutation(function () { return store.setBookmark(id, !saved(id)); });
        });
        return button;
      }
    };
  } };
})();
