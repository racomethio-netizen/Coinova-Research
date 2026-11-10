-- =============================================================================
-- 0014 NEXUS validation & backtesting (docs/nexus/SIGNAL_ENGINE.md §6-§8).
--
-- experiments        every run of the evaluation pipeline, including failures
--                    (so the number of configurations tried is always visible)
-- validation_reports insert-only results; a model can only be approved with one
-- backtest_*         Premium on-demand backtests, queued and run by the engine
-- signal_performance aggregate view over the ledger (security invoker: viewers
--                    only aggregate signals they are allowed to see)
-- =============================================================================

-- ------------------------------------------------------------ experiments
create table public.experiments (
  id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('walk_forward', 'backtest', 'baseline')),
  strategy_id smallint references public.strategies (id),
  config jsonb not null,
  dataset_hash text not null check (dataset_hash ~ '^[0-9a-f]{64}$'),
  git_sha text check (git_sha is null or git_sha ~ '^[0-9a-f]{7,40}$'),
  lockfile_hash text check (lockfile_hash is null or lockfile_hash ~ '^[0-9a-f]{64}$'),
  engine_version text not null,
  seed integer not null,
  status text not null default 'running' check (status in ('running', 'succeeded', 'failed')),
  metrics jsonb,
  error text check (error is null or char_length(error) <= 2000),
  started_at timestamptz not null default now(),
  finished_at timestamptz
);
create index experiments_started_idx on public.experiments (started_at desc);

-- Written once when running, finished exactly once; never deleted.
create or replace function public.experiments_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'experiments are never deleted';
  end if;
  if old.status <> 'running' then
    raise exception 'finished experiments are immutable';
  end if;
  if (new.kind, new.strategy_id, new.config, new.dataset_hash, new.git_sha, new.lockfile_hash, new.engine_version, new.seed, new.started_at)
     is distinct from (old.kind, old.strategy_id, old.config, old.dataset_hash, old.git_sha, old.lockfile_hash, old.engine_version, old.seed, old.started_at) then
    raise exception 'experiment inputs are immutable';
  end if;
  return new;
end;
$$;
create trigger experiments_guard before update or delete on public.experiments
  for each row execute function public.experiments_guard();

-- ------------------------------------------------------------ validation reports
create table public.validation_reports (
  id uuid primary key default gen_random_uuid(),
  experiment_id uuid not null unique references public.experiments (id),
  strategy_id smallint not null references public.strategies (id),
  period_start timestamptz not null,
  period_end timestamptz not null,
  n_trades integer not null check (n_trades >= 0),
  folds jsonb not null,
  metrics jsonb not null,
  calibration jsonb,
  baselines jsonb not null,
  assumptions jsonb not null,
  lockbox jsonb,
  created_at timestamptz not null default now(),
  check (period_end > period_start)
);
create trigger validation_reports_immutable before update or delete on public.validation_reports
  for each row execute function public.reject_ledger_mutation();

-- Models reference the report they were validated with; approval requires it and a lockbox result.
alter table public.model_versions add column validation_report_id uuid references public.validation_reports (id);

create or replace function public.model_versions_lifecycle()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'model_versions are never deleted';
  end if;
  if (new.strategy_id, new.version, new.model_type, new.artifact_uri, new.artifact_sha256, new.feature_set,
      new.calibration_method, new.git_sha, new.created_at, new.validation_report_id)
     is distinct from
     (old.strategy_id, old.version, old.model_type, old.artifact_uri, old.artifact_sha256, old.feature_set,
      old.calibration_method, old.git_sha, old.created_at, old.validation_report_id) then
    raise exception 'model version contents are immutable';
  end if;
  if not ((old.status, new.status) in (('draft', 'shadow'), ('shadow', 'approved'), ('draft', 'retired'),
                                       ('shadow', 'retired'), ('approved', 'retired'))
          or old.status = new.status) then
    raise exception 'invalid model status transition % -> %', old.status, new.status;
  end if;
  if old.status = 'approved' and (new.approved_by, new.approved_at) is distinct from (old.approved_by, old.approved_at) then
    raise exception 'approval records are immutable';
  end if;
  if new.status = 'approved' and old.status <> 'approved' then
    if new.artifact_sha256 is null then
      raise exception 'an approved model needs an artifact checksum';
    end if;
    if new.validation_report_id is null
       or not exists (select 1 from public.validation_reports r where r.id = new.validation_report_id and r.lockbox is not null) then
      raise exception 'an approved model needs a validation report with a lockbox result';
    end if;
  end if;
  return new;
end;
$$;

-- ------------------------------------------------------------ backtests
create table public.backtest_runs (
  id uuid primary key default gen_random_uuid(),
  requested_by uuid references auth.users (id) on delete cascade,
  pair_id bigint not null references public.market_pairs (id),
  timeframe text not null check (public.timeframe_seconds(timeframe) is not null),
  strategy_key text not null default 'trend_pullback' check (strategy_key ~ '^[a-z][a-z0-9_]{1,59}$'),
  params jsonb not null default '{}'::jsonb check (jsonb_typeof(params) = 'object' and octet_length(params::text) <= 4096),
  period_start timestamptz not null,
  period_end timestamptz not null,
  status text not null default 'queued' check (status in ('queued', 'running', 'succeeded', 'failed')),
  error text check (error is null or char_length(error) <= 1000),
  experiment_id uuid references public.experiments (id),
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz,
  check (period_end > period_start and period_end - period_start <= interval '5 years 2 days')
);
create index backtest_runs_queue_idx on public.backtest_runs (created_at) where status = 'queued';
create index backtest_runs_user_idx on public.backtest_runs (requested_by, created_at desc);

create table public.backtest_results (
  run_id uuid primary key references public.backtest_runs (id) on delete cascade,
  metrics jsonb not null,
  equity_curve jsonb not null,
  baselines jsonb not null,
  assumptions jsonb not null,
  created_at timestamptz not null default now()
);

create table public.backtest_trades (
  run_id uuid not null references public.backtest_runs (id) on delete cascade,
  seq integer not null,
  direction text not null check (direction in ('BUY', 'SELL')),
  decided_at timestamptz not null,
  closed_at timestamptz not null,
  outcome text not null,
  entry_price numeric(30, 8),
  exit_price numeric(30, 8),
  quantity numeric(30, 12),
  net_return numeric(14, 8),
  r_multiple numeric(14, 4),
  pnl numeric(30, 8),
  primary key (run_id, seq)
);

-- Premium users queue backtests through this function only (feature + quota + bounds).
create or replace function public.request_backtest(p_pair_id bigint, p_timeframe text, p_start timestamptz, p_end timestamptz, p_params jsonb default '{}'::jsonb)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  uid uuid := auth.uid();
  rid uuid;
begin
  if uid is null then
    raise exception 'unauthorized' using errcode = '42501';
  end if;
  if not public.has_feature('backtesting') then
    raise exception 'feature_required:backtesting' using errcode = '42501';
  end if;
  if not exists (select 1 from public.market_pairs where id = p_pair_id and enabled and p_timeframe = any (timeframes)) then
    raise exception 'unknown pair or timeframe' using errcode = '22023';
  end if;
  if p_end > now() then
    raise exception 'period must end in the past' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(hashtext('backtest:' || uid::text));
  if (select count(*) from public.backtest_runs where requested_by = uid and created_at > now() - interval '1 day') >= 10 then
    raise exception 'limit_reached:backtestsPerDay' using errcode = 'P0001';
  end if;
  if exists (select 1 from public.backtest_runs where requested_by = uid and status in ('queued', 'running')) then
    raise exception 'a backtest is already queued or running' using errcode = 'P0001';
  end if;
  insert into public.backtest_runs (requested_by, pair_id, timeframe, params, period_start, period_end)
  values (uid, p_pair_id, p_timeframe, coalesce(p_params, '{}'::jsonb), p_start, p_end)
  returning id into rid;
  return rid;
end;
$$;
revoke execute on function public.request_backtest(bigint, text, timestamptz, timestamptz, jsonb) from public, anon;
grant execute on function public.request_backtest(bigint, text, timestamptz, timestamptz, jsonb) to authenticated;

-- ------------------------------------------------------------ performance view
-- security_invoker: aggregates only the signals the viewer may see (RLS on the ledger).
create view public.signal_performance with (security_invoker = true) as
select
  s.pair_id,
  s.timeframe,
  s.strategy_id,
  count(*) as signals,
  count(o.signal_id) as closed,
  count(*) filter (where o.outcome = 'win') as wins,
  count(*) filter (where o.outcome = 'loss') as losses,
  count(*) filter (where o.outcome = 'expired_closed') as expired_closed,
  count(*) filter (where o.outcome = 'expired_no_entry') as expired_no_entry,
  count(*) filter (where o.outcome = 'invalidated') as invalidated,
  avg(o.r_multiple) filter (where o.entry_price is not null) as avg_r_multiple,
  avg(o.net_return) filter (where o.entry_price is not null) as avg_net_return,
  min(s.published_at) as first_published_at,
  max(s.published_at) as last_published_at
from public.signals s
left join public.signal_outcomes o on o.signal_id = s.id
group by s.pair_id, s.timeframe, s.strategy_id;
grant select on public.signal_performance to anon, authenticated;

-- ------------------------------------------------------------ RLS
alter table public.experiments enable row level security;
alter table public.validation_reports enable row level security;
alter table public.backtest_runs enable row level security;
alter table public.backtest_results enable row level security;
alter table public.backtest_trades enable row level security;

revoke all on public.experiments, public.validation_reports from anon;
revoke insert, update, delete, truncate on public.experiments, public.validation_reports,
  public.backtest_runs, public.backtest_results, public.backtest_trades from anon, authenticated;
revoke select on public.backtest_runs, public.backtest_results, public.backtest_trades from anon;

create policy "experiments: staff read" on public.experiments for select to authenticated using ((select public.is_staff()));
create policy "validation_reports: staff read" on public.validation_reports for select to authenticated using ((select public.is_staff()));
create policy "validation_reports: public read for approved models" on public.validation_reports for select to authenticated
  using (exists (select 1 from public.model_versions m where m.validation_report_id = validation_reports.id and m.status in ('approved', 'retired')));

create policy "backtest_runs: own" on public.backtest_runs for select to authenticated using (requested_by = (select auth.uid()));
create policy "backtest_runs: staff" on public.backtest_runs for select to authenticated using ((select public.is_staff()));
create policy "backtest_results: follow run" on public.backtest_results for select to authenticated
  using (exists (select 1 from public.backtest_runs r where r.id = run_id));
create policy "backtest_trades: follow run" on public.backtest_trades for select to authenticated
  using (exists (select 1 from public.backtest_runs r where r.id = run_id));

-- Engine grants.
grant select, insert, update on public.experiments to nexus_engine;
grant select, insert on public.validation_reports to nexus_engine;
grant select, update on public.backtest_runs to nexus_engine;
grant select, insert on public.backtest_results, public.backtest_trades to nexus_engine;
grant select on public.signal_performance to nexus_engine;
create policy "experiments: engine" on public.experiments for all to nexus_engine using (true) with check (true);
create policy "validation_reports: engine" on public.validation_reports for all to nexus_engine using (true) with check (true);
create policy "backtest_runs: engine" on public.backtest_runs for all to nexus_engine using (true) with check (true);
create policy "backtest_results: engine" on public.backtest_results for all to nexus_engine using (true) with check (true);
create policy "backtest_trades: engine" on public.backtest_trades for all to nexus_engine using (true) with check (true);
