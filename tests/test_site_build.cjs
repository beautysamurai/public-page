const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path");
const { spawnSync } = require("node:child_process");
const root = path.join(__dirname, ".."), output = path.join(root, ".local/site");
const publicKey = "sb_publishable_" + "x".repeat(25);
function build(env) {
  return spawnSync(process.execPath, ["scripts/build_site.mjs"], {
    cwd: root, encoding: "utf8", env: { ...process.env, SUPABASE_URL: "", SUPABASE_PUBLISHABLE_KEY: "", ...env }
  });
}
test("Pages build publishes only public settings, narrows CSP, rejects secrets, and removes obsolete artifacts", () => {
  const committed = fs.readFileSync(path.join(root, "site/personal-config.js"), "utf8");
  try {
    let result = build({ SUPABASE_URL: "https://abcdefgh.supabase.co", SUPABASE_PUBLISHABLE_KEY: publicKey, OPENAI_API_KEY: "private-test-sentinel-not-a-real-key" });
    assert.equal(result.status, 0, result.stderr);
    const config = fs.readFileSync(path.join(output, "personal-config.js"), "utf8");
    assert.ok(config.includes(publicKey)); assert.ok(!config.includes("private-test-sentinel"));
    const html = fs.readFileSync(path.join(output, "index.html"), "utf8");
    assert.ok(html.includes("connect-src 'self' https://abcdefgh.supabase.co;"));
    assert.ok(html.includes("script-src 'self';")); assert.ok(!html.includes("https://*.supabase.co"));
    assert.ok(fs.existsSync(path.join(output, "vendor/supabase.js")));
    for (const file of ["classics/index.html", "classics.js", "classics.css", "data/classics.json"]) {
      assert.equal(fs.readFileSync(path.join(output, file), "utf8"), fs.readFileSync(path.join(root, "site", file), "utf8"), file);
    }
    assert.ok(fs.readFileSync(path.join(output, "classics/index.html"), "utf8").includes("connect-src 'self';"));
    assert.ok(!fs.existsSync(path.join(output, ".env")));
    result = build({ SUPABASE_URL: "https://abcdefgh.supabase.co", SUPABASE_PUBLISHABLE_KEY: "sb_secret_" + "x".repeat(25) });
    assert.notEqual(result.status, 0); assert.ok(!result.stderr.includes("sb_secret_" + "x".repeat(25)));
    assert.equal(fs.readFileSync(path.join(output, "personal-config.js"), "utf8"), config, "failed build preserves previous output");
    result = build({ SUPABASE_URL: "https://abcdefgh.supabase.co" }); assert.notEqual(result.status, 0);
    fs.writeFileSync(path.join(output, "obsolete-generated-test.txt"), "stale");
    result = build({}); assert.equal(result.status, 0, result.stderr);
    assert.ok(!fs.existsSync(path.join(output, "obsolete-generated-test.txt")));
    assert.ok(fs.readFileSync(path.join(output, "personal-config.js"), "utf8").includes("= null;"));
    assert.ok(fs.readFileSync(path.join(output, "index.html"), "utf8").includes("connect-src 'self';"));
    assert.equal(fs.readFileSync(path.join(root, "site/personal-config.js"), "utf8"), committed);
  } finally { build({}); }
});
