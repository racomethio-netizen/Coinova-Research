-- =============================================================================
-- 0006 Fundamentals cache on cryptocurrencies.
-- Refreshed in rotating batches by the `refresh-fundamentals` job so scoring
-- does not need one provider call per coin per run.
-- =============================================================================

alter table public.cryptocurrencies
  add column dev_commits_4w integer check (dev_commits_4w >= 0),
  add column dev_stars integer check (dev_stars >= 0),
  add column dev_contributors integer check (dev_contributors >= 0),
  add column genesis_date date,
  add column fundamentals_updated_at timestamptz,
  add column history_updated_at timestamptz;

create index cryptocurrencies_fundamentals_stale_idx on public.cryptocurrencies (fundamentals_updated_at nulls first) where is_active;
create index cryptocurrencies_history_stale_idx on public.cryptocurrencies (history_updated_at nulls first) where is_active;
