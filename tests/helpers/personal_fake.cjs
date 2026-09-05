// A deterministic SDK-shaped transport for controller and DOM unit tests.
const tick = () => new Promise(resolve => setTimeout(resolve, 15));
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
function fakeClient() {
  let listener = () => {};
  const client = {
    user: null, tables: { research_bookmarks: [], research_presets: [] }, queries: [], writes: [],
    failRead: false, failWrite: false, nextRead: null, sessionResult: null, logoutResult: null,
    emit(user, event = user ? "SIGNED_IN" : "SIGNED_OUT") {
      client.user = user;
      listener(event, user ? { user } : null);
    },
    auth: {
      onAuthStateChange(fn) { listener = fn; return { data: { subscription: { unsubscribe() { listener = () => {}; } } } }; },
      async getSession() { return client.sessionResult || { data: { session: client.user ? { user: client.user } : null } }; },
      async signOut() { if (client.logoutResult) return client.logoutResult; client.emit(null); return { error: null }; },
      async signInWithOtp(args) { client.requestedEmail = args.email; return { error: null }; },
      async verifyOtp(args) { client.verified = args; client.emit({ id: "user-a", email: args.email }); return { data: {} }; }
    },
    from(table) {
      const q = { table, mode: "select", filters: {}, start: 0, end: Infinity };
      const chain = {
        select() { return chain; },
        eq(key, value) { q.filters[key] = value; return chain; },
        order(key) { q.order = key; return chain; },
        range(start, end) { q.start = start; q.end = end; return chain; },
        upsert(row, options) { q.mode = "upsert"; q.row = row; q.options = options; return chain; },
        insert(row) { q.mode = "insert"; q.row = row; return chain; },
        delete() { q.mode = "delete"; return chain; },
        then(resolve, reject) {
          const run = async () => {
            client.queries.push(q);
            const matches = row => Object.entries(q.filters).every(([k, v]) => row[k] === v);
            if (q.mode === "select") {
              if (client.nextRead) { const next = client.nextRead; client.nextRead = null; return await next; }
              if (client.failRead) return { error: new Error("read failed") };
              return { data: client.tables[table].filter(matches).sort((a, b) => String(a[q.order]).localeCompare(String(b[q.order]))).slice(q.start, q.end + 1) };
            }
            client.writes.push(q);
            if (client.failWrite) return { error: new Error("write failed") };
            if (q.mode === "delete") client.tables[table] = client.tables[table].filter(row => !matches(row));
            else if (q.mode === "insert") client.tables[table].push({ id: "00000000-0000-4000-8000-" + String(client.writes.length).padStart(12, "0"), ...q.row });
            else if (!client.tables[table].some(row => row.user_id === q.row.user_id && row.paper_id === q.row.paper_id)) client.tables[table].push(q.row);
            return { error: null };
          };
          return run().then(resolve, reject);
        }
      };
      return chain;
    }
  };
  return client;
}
module.exports = { tick, deferred, fakeClient };
