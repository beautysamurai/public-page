const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path");
const { PGlite } = require("@electric-sql/pglite");

test("actual PostgreSQL RLS isolates anonymous, A and B for both tables", async () => {
  const db = new PGlite();
  const A = "11111111-1111-4111-8111-111111111111", B = "22222222-2222-4222-8222-222222222222";
  try {
    // Only the auth schema/claims are simulated. Run the exact production
    // migration and enforce policies as non-superuser PostgreSQL roles.
    await db.exec(`create role anon; create role authenticated;
      create schema auth; create table auth.users(id uuid primary key);
      create function auth.uid() returns uuid language sql stable as
        $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
      grant usage on schema public, auth to anon, authenticated;
      grant execute on function auth.uid() to anon, authenticated;
      insert into auth.users values ('${A}'), ('${B}');`);
    await db.exec(fs.readFileSync(path.join(__dirname, "../supabase/migrations/202609050001_personal_library.sql"), "utf8"));
    const asUser = async (id) => {
      await db.exec("reset role; set role authenticated;");
      await db.query("select set_config('request.jwt.claim.sub', $1, false)", [id]);
    };
    for (const table of ["research_bookmarks", "research_presets"]) {
      const bookmark = table === "research_bookmarks";
      const insert = bookmark
        ? `insert into public.${table}(user_id,paper_id) values ($1,'2609.03115')`
        : `insert into public.${table}(user_id,name,filters) values ($1,'Rates','{"version":1}')`;
      await asUser(A); await db.query(insert, [A]);
      await assert.rejects(db.query(insert, [B]), /row-level security/);
      await assert.rejects(db.query(`update public.${table} set user_id=$1`, [B]), /row-level security/);
      const select = `select * from public.${table}`;
      assert.equal((await db.query(select)).rows.length, 1);
      await asUser(B); assert.equal((await db.query(select)).rows.length, 0);
      assert.equal((await db.query(`delete from public.${table} where user_id=$1 returning *`, [A])).rows.length, 0);
      const update = bookmark ? "paper_id='2609.99999'" : "name='foreign edit'";
      assert.equal((await db.query(`update public.${table} set ${update} where user_id=$1 returning *`, [A])).rows.length, 0);
      await db.query(insert, [B]); assert.equal((await db.query(select)).rows.length, 1);
      await asUser(A); const original = (await db.query(select)).rows[0];
      assert.equal(bookmark ? original.paper_id : original.name, bookmark ? "2609.03115" : "Rates");
      await db.exec(`update public.${table} set ${update};`);
      assert.equal((await db.query(select)).rows.length, 1);
      await db.exec("reset role; set role anon;");
      await assert.rejects(db.query(select), /permission denied/);
      await assert.rejects(db.query(insert, [A]), /permission denied/);
      await asUser(A); await db.exec(`delete from public.${table};`);
      assert.equal((await db.query(select)).rows.length, 0);
    }
    await asUser(A);
    await assert.rejects(db.query("insert into public.research_bookmarks(user_id,paper_id) values ($1,'2609.03115v2')", [A]), /check constraint/);
    await assert.rejects(db.query("insert into public.research_presets(user_id,name,filters) values ($1,'n','{\"version\":1,\"private_unknown\":true}')", [A]), /check constraint/);
    await db.exec("reset role;");
    await db.query("delete from auth.users where id=$1", [B]);
    for (const table of ["research_bookmarks", "research_presets"]) assert.equal((await db.query(`select * from public.${table}`)).rows.length, 0);
  } finally { await db.close(); }
});
