(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.RatesPersonalLibrary = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  function validateConfig(raw) {
    if (!raw || typeof raw.url !== "string" ||
        !/^https:\/\/[a-z0-9]{8,64}\.supabase\.co\/?$/.test(raw.url) ||
        typeof raw.publishableKey !== "string" ||
        !/^sb_publishable_[A-Za-z0-9_-]{12,200}$/.test(raw.publishableKey)) {
      throw new Error("Use the hosted Supabase HTTPS Project URL and a publishable key, never a secret/service-role key.");
    }
    return { url: raw.url.replace(/\/$/, ""), publishableKey: raw.publishableKey };
  }

  function paperId(value) {
    var id = typeof value === "string" ? value.trim().toLowerCase().replace(/v\d+$/, "") : "";
    if (!/^(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*\/\d{7})$/.test(id)) throw new Error("Invalid paper ID.");
    return id;
  }

  function presetFilters(raw) {
    var value = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
    function date(text) {
      if (typeof text !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(text)) return "";
      var parsed = new Date(text + "T00:00:00Z");
      return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === text ? text : "";
    }
    var from = date(value.from), to = date(value.to);
    if (from && to && from > to) { var swap = from; from = to; to = swap; }
    return {
      version: 1,
      view: value.view === "editions" ? "editions" : "papers",
      kind: ["daily", "weekly", "monthly"].includes(value.kind) ? value.kind : "all",
      from: from, to: to,
      minRating: /^(?:[0-9]|10)$/.test(String(value.minRating)) ? String(value.minRating) : "",
      tag: typeof value.tag === "string" ? value.tag.replace(/\s+/g, " ").trim().toLowerCase().slice(0, 120) : "",
      sort: value.sort === "date" ? "date" : "rating",
      query: typeof value.query === "string" ? value.query.trim().slice(0, 180) : "",
      savedOnly: value.savedOnly === true
    };
  }

  function presetName(raw) {
    var name = typeof raw === "string" ? raw.trim() : "";
    if (!name || name.length > 80 || /[\u0000-\u001f]/.test(name)) throw new Error("Invalid preset name.");
    return name;
  }

  // This controller never persists personal records in browser storage. Supabase
  // is authoritative; only its SDK persists the login session. Epoch checks keep
  // late responses from a previous account out of the current account's UI.
  function createStore(client, onChange) {
    var state = { user: null, phase: "signed-out", busy: false, bookmarks: [], presets: [] };
    var epoch = 0, readId = 0, authRevision = 0, loggingOut = false, disposed = false;
    function emit() { if (!disposed && onChange) onChange(snapshot()); }
    function snapshot() {
      return { user: state.user && { id: state.user.id, email: state.user.email },
        phase: state.phase, busy: state.busy, bookmarks: state.bookmarks.slice(),
        presets: state.presets.map(function (row) { return { id: row.id, name: row.name, filters: presetFilters(row.filters) }; }) };
    }
    function current(user, generation) { return !disposed && epoch === generation && state.user && state.user.id === user; }
    function fail(result) { if (result.error) throw new Error("Supabase request failed."); return result.data; }
    async function rows(table, fields, order, user) {
      var all = [];
      for (var start = 0; start < 10000; start += 200) {
        var data = fail(await client.from(table).select(fields).eq("user_id", user).order(order).range(start, start + 199));
        if (!Array.isArray(data) || data.some(function (row) { return row.user_id !== user; })) throw new Error("Invalid personal records.");
        all = all.concat(data);
        if (data.length < 200) return all;
      }
      throw new Error("Personal library is too large to load safely.");
    }
    async function refresh() {
      if (!state.user || disposed) return;
      var user = state.user.id, generation = epoch, request = ++readId;
      state.phase = "loading"; emit();
      try {
        var result = await Promise.all([
          rows("research_bookmarks", "user_id,paper_id", "paper_id", user),
          rows("research_presets", "user_id,id,name,filters", "id", user)
        ]);
        if (!current(user, generation) || request !== readId) return;
        state.bookmarks = result[0].map(function (row) { return paperId(row.paper_id); });
        state.presets = result[1].map(function (row) {
          return { id: row.id, name: presetName(row.name), filters: presetFilters(row.filters) };
        }).sort(function (a, b) { return a.name.localeCompare(b.name); });
        state.phase = "ready";
      } catch (_error) {
        if (!current(user, generation) || request !== readId) return;
        state.phase = "error";
      }
      emit();
    }
    function acceptSession(session) {
      if (disposed) return;
      var user = session && session.user;
      if ((state.user && state.user.id) === (user && user.id)) return;
      epoch += 1; readId += 1;
      state = { user: user ? { id: user.id, email: user.email || "" } : null,
        phase: user ? "loading" : "signed-out", busy: false, bookmarks: [], presets: [] };
      emit();
      // Never await another auth/DB call inside onAuthStateChange (SDK lock).
      if (user) setTimeout(function () { if (state.user && state.user.id === user.id) refresh(); }, 0);
    }
    var subscription = client.auth.onAuthStateChange(function (_event, session) {
      authRevision += 1;
      if (loggingOut && session) return;
      acceptSession(session);
    }).data.subscription;
    async function start() {
      var initialRevision = authRevision;
      try {
        var result = await client.auth.getSession();
        fail(result);
        if (initialRevision === authRevision && !loggingOut) acceptSession(result.data.session);
      } catch (_error) { if (initialRevision === authRevision && !loggingOut) { state.phase = "error"; emit(); } }
    }
    async function mutate(operation) {
      if (!state.user || state.phase !== "ready" || state.busy) throw new Error("Sign in and sync before saving.");
      var user = state.user.id, generation = epoch;
      state.busy = true; emit();
      try {
        fail(await operation(user));
        if (current(user, generation)) await refresh();
      } catch (_error) {
        if (current(user, generation)) { state.phase = "error"; emit(); }
        throw new Error("Could not confirm the save. Sync again before retrying.");
      } finally { if (current(user, generation)) { state.busy = false; emit(); } }
    }
    return {
      snapshot: snapshot, start: start, refresh: refresh,
      setBookmark: function (id, saved) {
        id = paperId(id);
        return mutate(function (user) {
          return saved
            ? client.from("research_bookmarks").upsert({ user_id: user, paper_id: id }, { onConflict: "user_id,paper_id" })
            : client.from("research_bookmarks").delete().eq("user_id", user).eq("paper_id", id);
        });
      },
      savePreset: function (name, filters) {
        name = presetName(name); filters = presetFilters(filters);
        return mutate(function (user) { return client.from("research_presets").insert({ user_id: user, name: name, filters: filters }); });
      },
      deletePreset: function (id) {
        if (typeof id !== "string" || !/^[0-9a-f-]{36}$/i.test(id)) throw new Error("Invalid preset ID.");
        return mutate(function (user) { return client.from("research_presets").delete().eq("user_id", user).eq("id", id); });
      },
      signOut: async function () {
        // Clear personal UI before waiting for the network, even when offline.
        loggingOut = true; authRevision += 1;
        acceptSession(null);
        try { fail(await client.auth.signOut({ scope: "local" })); }
        finally { acceptSession(null); loggingOut = false; }
      },
      dispose: function () { disposed = true; epoch += 1; subscription.unsubscribe(); }
    };
  }
  return Object.freeze({ validateConfig: validateConfig, paperId: paperId, presetFilters: presetFilters,
    presetName: presetName, createStore: createStore });
});
