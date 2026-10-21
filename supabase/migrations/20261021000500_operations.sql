-- =============================================================================
-- 0005 Operations: AI usage quotas, background jobs, retention cleanup.
-- =============================================================================

-- ------------------------------------------------------------- AI usage
create table public.ai_usage_records (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  feature text not null check (feature in ('summary', 'compare', 'explain_score', 'tokenomics', 'article_qa', 'market_brief')),
  status text not null default 'reserved' check (status in ('reserved', 'success', 'error')),
  provider text check (provider is null or char_length(provider) <= 40),
  model text check (model is null or char_length(model) <= 100),
  input_tokens integer check (input_tokens >= 0),
  output_tokens integer check (output_tokens >= 0),
  latency_ms integer check (latency_ms >= 0),
  error_code text check (error_code is null or char_length(error_code) <= 80),
  -- Prompts and responses are intentionally NOT stored.
  created_at timestamptz not null default now(),
  completed_at timestamptz
);
create index ai_usage_user_day_idx on public.ai_usage_records (user_id, created_at desc);

alter table public.ai_usage_records enable row level security;
revoke all on public.ai_usage_records from anon;
revoke insert, update, delete, truncate on public.ai_usage_records from authenticated;
create policy "ai_usage: owner select" on public.ai_usage_records
  for select to authenticated using (user_id = (select auth.uid()));
create policy "ai_usage: admins select" on public.ai_usage_records
  for select to authenticated using ((select public.is_admin()));

-- Atomically reserve one AI request against the caller's daily quota (UTC day).
-- Exact across all app instances. Raises limit_reached when exhausted.
create or replace function public.reserve_ai_request(p_feature text)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  uid uuid := auth.uid();
  used integer;
  lim integer;
  rid uuid;
begin
  if uid is null then
    raise exception 'unauthorized' using errcode = '42501';
  end if;
  perform pg_advisory_xact_lock(hashtext('ai:' || uid::text));
  lim := public.tier_limit_for(uid, 'aiRequestsPerDay');
  select count(*) into used from public.ai_usage_records
   where user_id = uid
     and created_at >= date_trunc('day', now() at time zone 'utc') at time zone 'utc'
     and status <> 'error';
  if used >= lim then
    raise exception 'limit_reached:aiRequestsPerDay' using errcode = 'P0001', hint = format('Daily limit is %s.', lim);
  end if;
  insert into public.ai_usage_records (user_id, feature) values (uid, p_feature) returning id into rid;
  return rid;
end;
$$;
revoke execute on function public.reserve_ai_request(text) from public, anon;
grant execute on function public.reserve_ai_request(text) to authenticated;

-- ---------------------------------------------------------- background jobs
create table public.background_jobs (
  id uuid primary key default gen_random_uuid(),
  job_name text not null check (job_name ~ '^[a-z][a-z0-9-]{1,59}$'),
  -- e.g. "refresh-markets:2026-10-21T10:05" — one successful run per key.
  idempotency_key text not null unique check (char_length(idempotency_key) <= 200),
  status text not null check (status in ('running', 'succeeded', 'failed')),
  attempts integer not null default 1,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  locked_until timestamptz not null,
  summary jsonb not null default '{}'::jsonb,
  error text check (error is null or char_length(error) <= 2000)
);
create index background_jobs_name_time_idx on public.background_jobs (job_name, started_at desc);

alter table public.background_jobs enable row level security;
revoke all on public.background_jobs from anon;
revoke insert, update, delete, truncate on public.background_jobs from authenticated;
create policy "jobs: admins select" on public.background_jobs
  for select to authenticated using ((select public.is_admin()));

-- Acquire a run for an idempotency key. Returns the job id, or NULL when the
-- key already succeeded or is held by a live lock (another worker).
create or replace function public.acquire_job(p_job_name text, p_key text, p_lock_seconds integer)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  jid uuid;
begin
  insert into public.background_jobs (job_name, idempotency_key, status, locked_until)
  values (p_job_name, p_key, 'running', now() + make_interval(secs => p_lock_seconds))
  on conflict (idempotency_key) do update
     set status = 'running',
         attempts = public.background_jobs.attempts + 1,
         started_at = now(),
         finished_at = null,
         error = null,
         locked_until = now() + make_interval(secs => p_lock_seconds)
   where public.background_jobs.status = 'failed'
      or (public.background_jobs.status = 'running' and public.background_jobs.locked_until < now())
  returning id into jid;
  return jid;
end;
$$;
revoke execute on function public.acquire_job(text, text, integer) from public, anon, authenticated;
grant execute on function public.acquire_job(text, text, integer) to service_role;

-- ---------------------------------------------------------------- retention
-- See docs/DATABASE.md#retention. Run daily by the `cleanup` job.
create or replace function public.run_retention_cleanup()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  result jsonb := '{}'::jsonb;
  n bigint;
begin
  delete from public.market_snapshots where captured_at < now() - interval '90 days';
  get diagnostics n = row_count; result := result || jsonb_build_object('market_snapshots', n);

  delete from public.alert_evaluations where evaluated_at < now() - interval '30 days';
  get diagnostics n = row_count; result := result || jsonb_build_object('alert_evaluations', n);

  delete from public.notification_deliveries where status in ('sent', 'failed', 'skipped') and updated_at < now() - interval '90 days';
  get diagnostics n = row_count; result := result || jsonb_build_object('notification_deliveries', n);

  delete from public.notifications where created_at < now() - interval '180 days';
  get diagnostics n = row_count; result := result || jsonb_build_object('notifications', n);

  delete from public.telegram_link_tokens where expires_at < now() - interval '1 day';
  get diagnostics n = row_count; result := result || jsonb_build_object('telegram_link_tokens', n);

  delete from public.checkout_sessions where created_at < now() - interval '30 days' and status <> 'completed';
  get diagnostics n = row_count; result := result || jsonb_build_object('checkout_sessions', n);

  delete from public.background_jobs where started_at < now() - interval '90 days';
  get diagnostics n = row_count; result := result || jsonb_build_object('background_jobs', n);

  delete from public.ai_usage_records where created_at < now() - interval '400 days';
  get diagnostics n = row_count; result := result || jsonb_build_object('ai_usage_records', n);

  return result;
end;
$$;
revoke execute on function public.run_retention_cleanup() from public, anon, authenticated;
grant execute on function public.run_retention_cleanup() to service_role;
