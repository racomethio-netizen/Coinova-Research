-- =============================================================================
-- 0013 NEXUS signal ledger (docs/nexus/SIGNAL_ENGINE.md §3, ADR-005).
--
-- Published signals, their lifecycle events, final outcomes and annotations
-- are INSERT-ONLY for every role, including the service role: losing signals
-- can never be deleted and predictions can never be edited after publication.
-- Signals form a hash chain (prev_hash -> content_hash) so that any rewrite
-- outside the database's own rules is detectable by recomputing the chain.
--
-- Visibility (gate G-1 + decision D3):
--   staff  : MFA-verified staff only (shadow mode; the default)
--   beta   : staff + invited beta testers
--   public : Pro and above see everything; Free and anon see signals that
--            are closed (have an outcome) and were published > 7 days ago.
-- =============================================================================

-- ------------------------------------------------------------ immutability helper
create or replace function public.reject_ledger_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception '% is insert-only', tg_table_name;
end;
$$;

-- ------------------------------------------------------------ beta testers
create table public.beta_testers (
  user_id uuid primary key references auth.users (id) on delete cascade,
  added_by uuid references auth.users (id) on delete set null,
  added_at timestamptz not null default now()
);
alter table public.beta_testers enable row level security;
revoke all on public.beta_testers from anon;
revoke update, truncate on public.beta_testers from authenticated;
create policy "beta_testers: read own" on public.beta_testers for select to authenticated using (user_id = (select auth.uid()));
create policy "beta_testers: staff read" on public.beta_testers for select to authenticated using ((select public.is_staff()));
create policy "beta_testers: admins add" on public.beta_testers for insert to authenticated
  with check ((select public.is_admin()) and added_by = (select auth.uid()));
create policy "beta_testers: admins remove" on public.beta_testers for delete to authenticated using ((select public.is_admin()));

-- ------------------------------------------------------------ strategies (immutable versions)
create table public.strategies (
  id smallint generated always as identity primary key,
  key text not null check (key ~ '^[a-z][a-z0-9_]{1,59}$'),
  version text not null check (version ~ '^\d+\.\d+\.\d+$'),
  name text not null check (char_length(name) between 1 and 120),
  description text not null check (char_length(description) <= 4000),
  params jsonb not null,
  created_at timestamptz not null default now(),
  unique (key, version)
);
create trigger strategies_immutable before update or delete on public.strategies
  for each row execute function public.reject_ledger_mutation();
alter table public.strategies enable row level security;
revoke insert, update, delete, truncate on public.strategies from anon, authenticated;
-- Methodology is public: anyone can read the exact parameters of every strategy version.
create policy "strategies: public read" on public.strategies for select to anon, authenticated using (true);

-- ------------------------------------------------------------ model versions
create table public.model_versions (
  id uuid primary key default gen_random_uuid(),
  strategy_id smallint not null references public.strategies (id),
  version text not null check (version ~ '^\d+\.\d+\.\d+$'),
  model_type text not null check (model_type in ('logistic', 'xgboost')),
  artifact_uri text check (artifact_uri is null or char_length(artifact_uri) <= 500),
  artifact_sha256 text check (artifact_sha256 is null or artifact_sha256 ~ '^[0-9a-f]{64}$'),
  feature_set jsonb not null default '[]'::jsonb,
  calibration_method text check (calibration_method is null or calibration_method in ('isotonic', 'sigmoid')),
  git_sha text check (git_sha is null or git_sha ~ '^[0-9a-f]{7,40}$'),
  status text not null default 'draft' check (status in ('draft', 'shadow', 'approved', 'retired')),
  approved_by uuid references auth.users (id) on delete set null,
  approved_at timestamptz,
  retired_at timestamptz,
  created_at timestamptz not null default now(),
  unique (strategy_id, version),
  check ((status = 'approved') = (approved_at is not null) or status = 'retired')
);

-- Lifecycle: draft -> shadow -> approved -> retired (or retire earlier). Everything
-- except the lifecycle columns is frozen once written; nothing is ever deleted.
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
      new.calibration_method, new.git_sha, new.created_at)
     is distinct from
     (old.strategy_id, old.version, old.model_type, old.artifact_uri, old.artifact_sha256, old.feature_set,
      old.calibration_method, old.git_sha, old.created_at) then
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
  if new.status = 'approved' and new.artifact_sha256 is null then
    raise exception 'an approved model needs an artifact checksum';
  end if;
  return new;
end;
$$;
create trigger model_versions_lifecycle before update or delete on public.model_versions
  for each row execute function public.model_versions_lifecycle();

alter table public.model_versions enable row level security;
revoke insert, update, delete, truncate on public.model_versions from anon, authenticated;
grant update (status, approved_by, approved_at, retired_at) on public.model_versions to authenticated;
create policy "model_versions: public read approved" on public.model_versions for select to anon, authenticated
  using (status in ('approved', 'retired') or (select public.is_staff()));
create policy "model_versions: admins manage lifecycle" on public.model_versions for update to authenticated
  using ((select public.is_admin()))
  with check ((select public.is_admin()) and (approved_by is null or approved_by = (select auth.uid())));

-- ------------------------------------------------------------ evaluations (every decision, incl. HOLD)
create table public.signal_evaluations (
  id uuid primary key default gen_random_uuid(),
  pair_id bigint not null references public.market_pairs (id),
  timeframe text not null check (public.timeframe_seconds(timeframe) is not null),
  strategy_id smallint not null references public.strategies (id),
  model_version_id uuid references public.model_versions (id),
  as_of timestamptz not null,
  decision text not null check (decision in ('BUY', 'SELL', 'HOLD')),
  probability numeric(6, 4) check (probability is null or probability between 0 and 1),
  reason_codes text[] not null default '{}',
  features jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (pair_id, timeframe, strategy_id, as_of)
);
create index signal_evaluations_recent_idx on public.signal_evaluations (pair_id, timeframe, as_of desc);

-- Insert-only, except that HOLD evaluations older than a year may be removed by the
-- retention job after aggregation (decision D8). BUY/SELL evaluations are kept forever.
create or replace function public.signal_evaluations_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'DELETE' and old.decision = 'HOLD' and old.created_at < now() - interval '365 days' then
    return old;
  end if;
  raise exception 'signal_evaluations is insert-only';
end;
$$;
create trigger signal_evaluations_guard before update or delete on public.signal_evaluations
  for each row execute function public.signal_evaluations_guard();

-- ------------------------------------------------------------ published signals (hash-chained)
create table public.signals (
  seq bigint generated always as identity unique,
  id uuid primary key,
  evaluation_id uuid not null unique references public.signal_evaluations (id),
  pair_id bigint not null references public.market_pairs (id),
  timeframe text not null check (public.timeframe_seconds(timeframe) is not null),
  strategy_id smallint not null references public.strategies (id),
  model_version_id uuid references public.model_versions (id),
  direction text not null check (direction in ('BUY', 'SELL')),
  as_of timestamptz not null,
  published_at timestamptz not null,
  entry_low numeric(30, 8) not null check (entry_low > 0),
  entry_high numeric(30, 8) not null check (entry_high >= entry_low),
  stop_loss numeric(30, 8) not null check (stop_loss > 0),
  take_profits numeric(30, 8)[] not null check (cardinality(take_profits) between 1 and 3),
  risk_reward numeric(10, 4) not null check (risk_reward > 0),
  confidence numeric(6, 4) check (confidence is null or confidence between 0 and 1),
  confidence_band text not null check (confidence_band in ('not_calibrated', 'low', 'medium', 'high')),
  entry_expires_at timestamptz not null,
  horizon_bars integer not null check (horizon_bars between 1 and 1000),
  assumptions jsonb not null,
  explanation jsonb not null,
  visibility text not null check (visibility in ('staff', 'beta', 'public')),
  prev_hash text check (prev_hash is null or prev_hash ~ '^[0-9a-f]{64}$'),
  content_hash text not null unique check (content_hash ~ '^[0-9a-f]{64}$'),
  check (published_at >= as_of),
  check (entry_expires_at > published_at),
  check ((confidence is null) = (confidence_band = 'not_calibrated')),
  check (
    (direction = 'BUY' and stop_loss < entry_low and take_profits[1] > entry_high)
    or (direction = 'SELL' and stop_loss > entry_high and take_profits[1] < entry_low)
  )
);
create index signals_pair_idx on public.signals (pair_id, timeframe, published_at desc);
create index signals_published_idx on public.signals (published_at desc);

-- On insert: published_at is the database's own transaction time (no backdating),
-- and prev_hash must link to the latest signal (one chain, serialized by a lock).
create or replace function public.signals_chain_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  last_hash text;
begin
  if tg_op <> 'INSERT' then
    raise exception 'signals is insert-only';
  end if;
  if new.published_at <> now() then
    raise exception 'published_at must equal the database transaction time';
  end if;
  perform pg_advisory_xact_lock(hashtext('signals_chain'));
  select content_hash into last_hash from public.signals order by seq desc limit 1;
  if new.prev_hash is distinct from last_hash then
    raise exception 'hash chain mismatch: prev_hash does not match the latest signal';
  end if;
  return new;
end;
$$;
create trigger signals_chain_guard before insert or update or delete on public.signals
  for each row execute function public.signals_chain_guard();

-- ------------------------------------------------------------ lifecycle events & outcomes
create table public.signal_events (
  id bigint generated always as identity primary key,
  signal_id uuid not null references public.signals (id),
  event_type text not null check (event_type in ('entered', 'tp1_hit', 'sl_hit', 'expired_no_entry', 'invalidated', 'horizon_reached')),
  occurred_at timestamptz not null,
  price numeric(30, 8) check (price is null or price > 0),
  created_at timestamptz not null default now(),
  unique (signal_id, event_type)
);
create trigger signal_events_immutable before update or delete on public.signal_events
  for each row execute function public.reject_ledger_mutation();

create table public.signal_outcomes (
  signal_id uuid primary key references public.signals (id),
  outcome text not null check (outcome in ('win', 'loss', 'expired_closed', 'expired_no_entry', 'invalidated')),
  entry_price numeric(30, 8) check (entry_price is null or entry_price > 0),
  exit_price numeric(30, 8) check (exit_price is null or exit_price > 0),
  net_return numeric(14, 8),
  r_multiple numeric(14, 4),
  closed_at timestamptz not null,
  resolver_version text not null check (resolver_version ~ '^\d+\.\d+\.\d+$'),
  resolved_at timestamptz not null default now(),
  check ((outcome in ('expired_no_entry', 'invalidated')) = (entry_price is null))
);
create trigger signal_outcomes_immutable before update or delete on public.signal_outcomes
  for each row execute function public.reject_ledger_mutation();

create table public.signal_annotations (
  id bigint generated always as identity primary key,
  signal_id uuid not null references public.signals (id),
  kind text not null check (kind in ('note', 'correction', 'retraction')),
  body text not null check (char_length(body) between 1 and 4000),
  author_id uuid default auth.uid() references auth.users (id) on delete set null,
  created_at timestamptz not null default now()
);
create index signal_annotations_signal_idx on public.signal_annotations (signal_id, created_at);
create trigger signal_annotations_immutable before update or delete on public.signal_annotations
  for each row execute function public.reject_ledger_mutation();

-- ------------------------------------------------------------ visibility
-- SECURITY DEFINER so signal policies can consult outcomes without policy recursion.
create or replace function public.signal_is_closed(sid uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (select 1 from public.signal_outcomes o where o.signal_id = sid);
$$;
revoke execute on function public.signal_is_closed(uuid) from public;
grant execute on function public.signal_is_closed(uuid) to anon, authenticated, service_role;

create or replace function public.can_view_signal(s_visibility text, s_published_at timestamptz, sid uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select public.is_staff()
    or (s_visibility = 'beta' and exists (select 1 from public.beta_testers b where b.user_id = (select auth.uid())))
    or (s_visibility = 'public' and (
          public.has_feature('signals.history')
          or (s_published_at < now() - interval '7 days' and public.signal_is_closed(sid))
        ));
$$;
revoke execute on function public.can_view_signal(text, timestamptz, uuid) from public;
grant execute on function public.can_view_signal(text, timestamptz, uuid) to anon, authenticated, service_role;

alter table public.signal_evaluations enable row level security;
alter table public.signals enable row level security;
alter table public.signal_events enable row level security;
alter table public.signal_outcomes enable row level security;
alter table public.signal_annotations enable row level security;

revoke insert, update, delete, truncate on
  public.signal_evaluations, public.signals, public.signal_events, public.signal_outcomes, public.signal_annotations
  from anon, authenticated;
revoke select on public.signal_evaluations from anon;

create policy "signal_evaluations: staff read" on public.signal_evaluations for select to authenticated using ((select public.is_staff()));
create policy "signals: visible per gate and tier" on public.signals for select to anon, authenticated
  using (public.can_view_signal(visibility, published_at, id));
create policy "signal_events: follow signal" on public.signal_events for select to anon, authenticated
  using (exists (select 1 from public.signals s where s.id = signal_id));
create policy "signal_outcomes: follow signal" on public.signal_outcomes for select to anon, authenticated
  using (exists (select 1 from public.signals s where s.id = signal_id));
create policy "signal_annotations: follow signal" on public.signal_annotations for select to anon, authenticated
  using (exists (select 1 from public.signals s where s.id = signal_id));
grant insert (signal_id, kind, body) on public.signal_annotations to authenticated;
create policy "signal_annotations: MFA admins annotate as themselves" on public.signal_annotations for insert to authenticated
  with check ((select public.is_admin()) and author_id = (select auth.uid()));

-- ------------------------------------------------------------ engine grants
grant select, insert on public.strategies, public.model_versions, public.signal_evaluations,
  public.signals, public.signal_events, public.signal_outcomes to nexus_engine;
grant select on public.signal_annotations to nexus_engine;
grant usage on sequence public.strategies_id_seq, public.signals_seq_seq, public.signal_events_id_seq to nexus_engine;
grant execute on function public.reject_ledger_mutation() to nexus_engine;

create policy "strategies: engine" on public.strategies for all to nexus_engine using (true) with check (true);
create policy "model_versions: engine read" on public.model_versions for select to nexus_engine using (true);
create policy "model_versions: engine registers drafts" on public.model_versions for insert to nexus_engine
  with check (status in ('draft', 'shadow') and approved_by is null and approved_at is null);
create policy "signal_evaluations: engine" on public.signal_evaluations for all to nexus_engine using (true) with check (true);
create policy "signals: engine" on public.signals for all to nexus_engine using (true) with check (true);
create policy "signal_events: engine" on public.signal_events for all to nexus_engine using (true) with check (true);
create policy "signal_outcomes: engine" on public.signal_outcomes for all to nexus_engine using (true) with check (true);
create policy "signal_annotations: engine read" on public.signal_annotations for select to nexus_engine using (true);
