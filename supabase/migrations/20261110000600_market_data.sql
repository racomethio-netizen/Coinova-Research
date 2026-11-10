-- =============================================================================
-- 0012 NEXUS market data: exchange OHLCV candles (docs/nexus/SIGNAL_ENGINE.md §1).
--
-- Written by the Python engine (role nexus_engine, least privilege). Users and
-- anon may READ sources, pairs, candles and latest prices; operational tables
-- are staff-only. Final candles are immutable: a differing re-fetch is stored
-- in candle_revisions, never applied in place.
-- =============================================================================

-- ------------------------------------------------------------ helpers
create or replace function public.timeframe_seconds(tf text)
returns integer
language sql
immutable
set search_path = ''
as $$
  select case tf when '15m' then 900 when '1h' then 3600 when '4h' then 14400 when '1d' then 86400 end;
$$;

-- ------------------------------------------------------------ sources
create table public.market_sources (
  id smallint generated always as identity primary key,
  key text not null unique check (key ~ '^[a-z][a-z0-9_]{1,39}$'),
  name text not null check (char_length(name) between 1 and 80),
  base_url text not null check (base_url ~ '^https://'),
  archive_url text check (archive_url is null or archive_url ~ '^https://'),
  terms_url text check (terms_url is null or terms_url ~ '^https://'),
  -- Gate G-4 (docs/nexus/COMPLIANCE.md): production ingestion requires a recorded review.
  terms_reviewed_at timestamptz,
  terms_reviewed_by uuid references auth.users (id) on delete set null,
  terms_notes text check (terms_notes is null or char_length(terms_notes) <= 4000),
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create trigger market_sources_updated_at before update on public.market_sources
  for each row execute function public.set_updated_at();

-- ------------------------------------------------------------ pairs
create table public.market_pairs (
  id bigint generated always as identity primary key,
  source_id smallint not null references public.market_sources (id),
  symbol text not null check (symbol ~ '^[A-Z0-9]{2,30}$'),
  base_asset text not null check (base_asset ~ '^[A-Z0-9]{1,20}$'),
  quote_asset text not null check (quote_asset ~ '^[A-Z0-9]{1,20}$'),
  -- Informational link to cryptocurrencies.id (CoinGecko id); not a FK because
  -- the universe table is populated by jobs and may not contain it yet.
  coin_id text check (coin_id is null or coin_id ~ '^[a-z0-9-]{1,100}$'),
  timeframes text[] not null default '{15m,1h,4h,1d}'
    check (cardinality(timeframes) between 1 and 4 and timeframes <@ array['15m', '1h', '4h', '1d']),
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (source_id, symbol)
);
create trigger market_pairs_updated_at before update on public.market_pairs
  for each row execute function public.set_updated_at();

-- ------------------------------------------------------------ ingest runs
create table public.ingest_runs (
  id bigint generated always as identity primary key,
  source_id smallint not null references public.market_sources (id),
  pair_id bigint references public.market_pairs (id),
  timeframe text check (timeframe is null or public.timeframe_seconds(timeframe) is not null),
  kind text not null check (kind in ('incremental', 'backfill', 'archive', 'ticker')),
  status text not null default 'running' check (status in ('running', 'succeeded', 'partial', 'failed')),
  candles_upserted integer not null default 0 check (candles_upserted >= 0),
  candles_rejected integer not null default 0 check (candles_rejected >= 0),
  revisions_detected integer not null default 0 check (revisions_detected >= 0),
  error jsonb,
  started_at timestamptz not null default now(),
  finished_at timestamptz
);
create index ingest_runs_started_idx on public.ingest_runs (started_at desc);
create index ingest_runs_pair_idx on public.ingest_runs (pair_id, timeframe, started_at desc);

-- ------------------------------------------------------------ candles
create table public.market_candles (
  pair_id bigint not null references public.market_pairs (id),
  timeframe text not null,
  open_time timestamptz not null,
  close_time timestamptz not null,
  open numeric not null check (open > 0),
  high numeric not null check (high > 0),
  low numeric not null check (low > 0),
  close numeric not null check (close > 0),
  volume numeric not null check (volume >= 0),
  quote_volume numeric check (quote_volume is null or quote_volume >= 0),
  trade_count integer check (trade_count is null or trade_count >= 0),
  is_final boolean not null,
  ingest_run_id bigint references public.ingest_runs (id) on delete set null,
  ingested_at timestamptz not null default now(),
  primary key (pair_id, timeframe, open_time),
  check (public.timeframe_seconds(timeframe) is not null),
  check (low <= least(open, close) and greatest(open, close) <= high),
  check (mod(extract(epoch from open_time)::numeric, public.timeframe_seconds(timeframe)) = 0),
  check (close_time = open_time + make_interval(secs => public.timeframe_seconds(timeframe)) - interval '1 millisecond')
);

-- Final candles are immutable (only a non-final candle may change, e.g. become final).
create or replace function public.protect_final_candles()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'market_candles rows cannot be deleted';
  end if;
  if old.is_final and (new.open, new.high, new.low, new.close, new.volume, new.quote_volume, new.trade_count, new.is_final, new.close_time)
       is distinct from (old.open, old.high, old.low, old.close, old.volume, old.quote_volume, old.trade_count, old.is_final, old.close_time) then
    raise exception 'final candles are immutable; record a candle_revisions row instead';
  end if;
  return new;
end;
$$;
create trigger market_candles_protect_final before update or delete on public.market_candles
  for each row execute function public.protect_final_candles();

create table public.candle_revisions (
  id bigint generated always as identity primary key,
  pair_id bigint not null references public.market_pairs (id),
  timeframe text not null,
  open_time timestamptz not null,
  original jsonb not null,
  revised jsonb not null,
  ingest_run_id bigint references public.ingest_runs (id) on delete set null,
  detected_at timestamptz not null default now()
);
create index candle_revisions_pair_idx on public.candle_revisions (pair_id, timeframe, open_time);

create table public.candle_gaps (
  id bigint generated always as identity primary key,
  pair_id bigint not null references public.market_pairs (id),
  timeframe text not null check (public.timeframe_seconds(timeframe) is not null),
  gap_start timestamptz not null,
  gap_end timestamptz not null,
  status text not null default 'open' check (status in ('open', 'backfilled', 'unrecoverable')),
  attempts integer not null default 0 check (attempts >= 0),
  last_error text check (last_error is null or char_length(last_error) <= 500),
  detected_at timestamptz not null default now(),
  resolved_at timestamptz,
  check (gap_end > gap_start),
  unique (pair_id, timeframe, gap_start)
);
create index candle_gaps_open_idx on public.candle_gaps (pair_id, timeframe) where status = 'open';

-- ------------------------------------------------------------ latest prices
create table public.latest_prices (
  pair_id bigint primary key references public.market_pairs (id),
  price numeric not null check (price > 0),
  source_time timestamptz not null,
  fetched_at timestamptz not null default now()
);

-- ------------------------------------------------------------ engine ops
create table public.engine_heartbeats (
  component text primary key check (component ~ '^[a-z][a-z0-9_.-]{1,59}$'),
  last_seen timestamptz not null default now(),
  details jsonb not null default '{}'::jsonb
);

create table public.engine_jobs (
  id bigint generated always as identity primary key,
  kind text not null check (kind in ('backfill', 'archive_backfill', 'resolve_gaps')),
  payload jsonb not null default '{}'::jsonb,
  status text not null default 'queued' check (status in ('queued', 'running', 'succeeded', 'failed')),
  run_after timestamptz not null default now(),
  attempts integer not null default 0 check (attempts >= 0),
  locked_by text,
  locked_until timestamptz,
  last_error text check (last_error is null or char_length(last_error) <= 1000),
  requested_by uuid references auth.users (id) on delete set null,
  created_at timestamptz not null default now(),
  finished_at timestamptz
);
create index engine_jobs_due_idx on public.engine_jobs (run_after) where status = 'queued';

-- ------------------------------------------------------------ privileges & RLS
alter table public.market_sources enable row level security;
alter table public.market_pairs enable row level security;
alter table public.ingest_runs enable row level security;
alter table public.market_candles enable row level security;
alter table public.candle_revisions enable row level security;
alter table public.candle_gaps enable row level security;
alter table public.latest_prices enable row level security;
alter table public.engine_heartbeats enable row level security;
alter table public.engine_jobs enable row level security;

revoke insert, update, delete, truncate on
  public.market_sources, public.market_pairs, public.ingest_runs, public.market_candles,
  public.candle_revisions, public.candle_gaps, public.latest_prices, public.engine_heartbeats, public.engine_jobs
  from anon, authenticated;
revoke select on public.ingest_runs, public.candle_revisions, public.candle_gaps, public.engine_heartbeats, public.engine_jobs from anon;

-- Public reads (attributed market data).
create policy "market_sources: public read" on public.market_sources for select to anon, authenticated using (enabled or (select public.is_staff()));
create policy "market_pairs: public read" on public.market_pairs for select to anon, authenticated using (enabled or (select public.is_staff()));
create policy "market_candles: public read" on public.market_candles for select to anon, authenticated using (true);
create policy "latest_prices: public read" on public.latest_prices for select to anon, authenticated using (true);

-- Staff-only operational reads.
create policy "ingest_runs: staff read" on public.ingest_runs for select to authenticated using ((select public.is_staff()));
create policy "candle_revisions: staff read" on public.candle_revisions for select to authenticated using ((select public.is_staff()));
create policy "candle_gaps: staff read" on public.candle_gaps for select to authenticated using ((select public.is_staff()));
create policy "engine_heartbeats: staff read" on public.engine_heartbeats for select to authenticated using ((select public.is_staff()));
create policy "engine_jobs: staff read" on public.engine_jobs for select to authenticated using ((select public.is_staff()));

-- Admin (MFA) management of pairs and source review status; audited by the app.
grant insert (source_id, symbol, base_asset, quote_asset, coin_id, timeframes, enabled) on public.market_pairs to authenticated;
grant update (coin_id, timeframes, enabled) on public.market_pairs to authenticated;
create policy "market_pairs: admins insert" on public.market_pairs for insert to authenticated with check ((select public.is_admin()));
create policy "market_pairs: admins update" on public.market_pairs for update to authenticated using ((select public.is_admin())) with check ((select public.is_admin()));
grant update (terms_reviewed_at, terms_reviewed_by, terms_notes, enabled) on public.market_sources to authenticated;
create policy "market_sources: admins record review" on public.market_sources for update to authenticated
  using ((select public.is_admin())) with check ((select public.is_admin()) and (terms_reviewed_by is null or terms_reviewed_by = (select auth.uid())));
grant insert (kind, payload, run_after) on public.engine_jobs to authenticated;
create policy "engine_jobs: admins enqueue" on public.engine_jobs for insert to authenticated with check ((select public.is_admin()));

-- Engine role: exactly what ingestion needs.
grant select on public.market_sources, public.market_pairs to nexus_engine;
grant select, insert, update on public.market_candles, public.latest_prices, public.engine_heartbeats to nexus_engine;
grant select, insert, update on public.ingest_runs, public.candle_gaps to nexus_engine;
grant select, insert on public.candle_revisions to nexus_engine;
grant select, update on public.engine_jobs to nexus_engine;
grant usage on sequence public.ingest_runs_id_seq, public.candle_revisions_id_seq, public.candle_gaps_id_seq to nexus_engine;
grant execute on function public.timeframe_seconds(text) to nexus_engine;

create policy "market_sources: engine read" on public.market_sources for select to nexus_engine using (true);
create policy "market_pairs: engine read" on public.market_pairs for select to nexus_engine using (true);
create policy "market_candles: engine all" on public.market_candles for all to nexus_engine using (true) with check (true);
create policy "latest_prices: engine all" on public.latest_prices for all to nexus_engine using (true) with check (true);
create policy "engine_heartbeats: engine all" on public.engine_heartbeats for all to nexus_engine using (true) with check (true);
create policy "ingest_runs: engine all" on public.ingest_runs for all to nexus_engine using (true) with check (true);
create policy "candle_gaps: engine all" on public.candle_gaps for all to nexus_engine using (true) with check (true);
create policy "candle_revisions: engine read" on public.candle_revisions for select to nexus_engine using (true);
create policy "candle_revisions: engine insert" on public.candle_revisions for insert to nexus_engine with check (true);
create policy "engine_jobs: engine read" on public.engine_jobs for select to nexus_engine using (true);
create policy "engine_jobs: engine update" on public.engine_jobs for update to nexus_engine using (true) with check (true);

-- ------------------------------------------------------------ seed (no data, only configuration)
insert into public.market_sources (key, name, base_url, archive_url, terms_url, enabled) values
  ('binance_spot', 'Binance (spot, public market data)', 'https://data-api.binance.vision', 'https://data.binance.vision',
   'https://www.binance.com/en/terms', true);

insert into public.market_pairs (source_id, symbol, base_asset, quote_asset, coin_id)
select s.id, p.symbol, p.base, 'USDT', p.coin
from public.market_sources s
cross join (values ('BTCUSDT', 'BTC', 'bitcoin'), ('ETHUSDT', 'ETH', 'ethereum')) as p(symbol, base, coin)
where s.key = 'binance_spot';
