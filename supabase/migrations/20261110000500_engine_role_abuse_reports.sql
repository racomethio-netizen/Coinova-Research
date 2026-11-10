-- =============================================================================
-- 0011 NEXUS engine database role, and abuse reports.
-- =============================================================================

-- --------------------------------------------------------------- engine role
-- The Python engine (docs/nexus/ARCHITECTURE.md, ADR-002) connects as this
-- least-privilege role instead of using the service-role key. It is created
-- NOLOGIN with no table privileges; later migrations grant exactly the tables
-- the engine writes. The owner enables login out of band, never in the repo:
--   alter role nexus_engine with login password '<generated secret>';
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'nexus_engine') then
    create role nexus_engine nologin noinherit;
  end if;
end;
$$;
grant usage on schema public to nexus_engine;
-- Guard against default privileges ever extending to it.
revoke all on all tables in schema public from nexus_engine;
revoke all on all sequences in schema public from nexus_engine;

-- --------------------------------------------------------------- abuse reports
create table public.abuse_reports (
  id uuid primary key default gen_random_uuid(),
  reporter_id uuid default auth.uid() references auth.users (id) on delete set null,
  target_type text not null check (target_type in ('article', 'signal', 'user', 'other')),
  target_id text check (target_id is null or char_length(target_id) between 1 and 200),
  reason text not null check (reason in ('spam', 'misleading', 'abusive', 'copyright', 'security', 'other')),
  details text check (details is null or char_length(details) <= 2000),
  status text not null default 'open' check (status in ('open', 'reviewing', 'resolved', 'dismissed')),
  handled_by uuid references auth.users (id) on delete set null,
  handled_at timestamptz,
  resolution_note text check (resolution_note is null or char_length(resolution_note) <= 2000),
  created_at timestamptz not null default now(),
  check ((status in ('open', 'reviewing')) or handled_at is not null)
);
create index abuse_reports_status_idx on public.abuse_reports (status, created_at desc);
create index abuse_reports_reporter_idx on public.abuse_reports (reporter_id, created_at desc);

alter table public.abuse_reports enable row level security;
revoke all on public.abuse_reports from anon;
revoke update, delete, truncate on public.abuse_reports from authenticated;
-- Users may only supply the report content; status and handling fields keep their defaults.
revoke insert on public.abuse_reports from authenticated;
grant insert (target_type, target_id, reason, details) on public.abuse_reports to authenticated;
grant update (status, handled_by, handled_at, resolution_note) on public.abuse_reports to authenticated;

create policy "abuse_reports: users file their own" on public.abuse_reports
  for insert to authenticated with check (reporter_id = (select auth.uid()));
create policy "abuse_reports: users read their own" on public.abuse_reports
  for select to authenticated using (reporter_id = (select auth.uid()));
create policy "abuse_reports: staff read all" on public.abuse_reports
  for select to authenticated using ((select public.is_staff()));
create policy "abuse_reports: admins handle" on public.abuse_reports
  for update to authenticated using ((select public.is_admin()))
  with check ((select public.is_admin()) and handled_by = (select auth.uid()));

-- Flood control: at most 20 reports per user per rolling day (exact, in SQL).
create or replace function public.limit_abuse_reports()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.reporter_id is null then
    return new;
  end if;
  perform pg_advisory_xact_lock(hashtext('abuse:' || new.reporter_id::text));
  if (select count(*) from public.abuse_reports
       where reporter_id = new.reporter_id and created_at > now() - interval '1 day') >= 20 then
    raise exception 'limit_reached:abuseReports' using errcode = 'P0001';
  end if;
  return new;
end;
$$;
revoke execute on function public.limit_abuse_reports() from public, anon, authenticated;
create trigger abuse_reports_limit before insert on public.abuse_reports
  for each row execute function public.limit_abuse_reports();

-- Reports are retained (not personal content of the reporter beyond the link,
-- which is nulled when the reporter deletes their account).
