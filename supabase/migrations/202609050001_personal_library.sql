-- Apply once in the project's SQL Editor (or via supabase db push).
begin;

create table public.research_bookmarks (
  user_id uuid not null references auth.users(id) on delete cascade,
  paper_id text not null check (paper_id ~ '^([0-9]{4}\.[0-9]{4,5}|[a-z][a-z0-9.-]*/[0-9]{7})$'),
  created_at timestamptz not null default now(),
  primary key (user_id, paper_id)
);

create table public.research_presets (
  user_id uuid not null references auth.users(id) on delete cascade,
  id uuid not null default gen_random_uuid(),
  name text not null check (char_length(btrim(name)) between 1 and 80),
  filters jsonb not null check (
    jsonb_typeof(filters) = 'object' and octet_length(filters::text) <= 2048
    and filters @> '{"version":1}'::jsonb
    and filters - array['version','view','kind','from','to','minRating','tag','sort','query','savedOnly'] = '{}'::jsonb
  ),
  created_at timestamptz not null default now(),
  primary key (user_id, id)
);

alter table public.research_bookmarks enable row level security;
alter table public.research_presets enable row level security;
revoke all on public.research_bookmarks, public.research_presets from public, anon, authenticated;
grant select, insert, update, delete on public.research_bookmarks, public.research_presets to authenticated;

create policy bookmarks_select on public.research_bookmarks for select to authenticated
  using ((select auth.uid()) = user_id);
create policy bookmarks_insert on public.research_bookmarks for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy bookmarks_update on public.research_bookmarks for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy bookmarks_delete on public.research_bookmarks for delete to authenticated
  using ((select auth.uid()) = user_id);

create policy presets_select on public.research_presets for select to authenticated
  using ((select auth.uid()) = user_id);
create policy presets_insert on public.research_presets for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy presets_update on public.research_presets for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy presets_delete on public.research_presets for delete to authenticated
  using ((select auth.uid()) = user_id);

commit;
