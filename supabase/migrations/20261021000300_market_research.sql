-- =============================================================================
-- 0003 Market data and research.
--
-- Market tables are publicly readable (they hold provider data the site
-- displays anyway) and written only by scheduled jobs (service role).
-- Premium research is protected IN THE DATABASE: article bodies, full score
-- tables, and score history are only readable by entitled tiers, so the
-- public anon key cannot be used to bypass the paywall.
-- =============================================================================

create type public.data_provenance as enum ('provider', 'project_reported', 'estimated');

-- ----------------------------------------------------------- cryptocurrencies
create table public.cryptocurrencies (
  id text primary key check (id ~ '^[a-z0-9][a-z0-9-]{0,99}$'),
  symbol text not null check (char_length(symbol) between 1 and 20),
  name text not null check (char_length(name) between 1 and 120),
  image_url text check (image_url is null or image_url like 'https://%'),
  categories text[] not null default '{}',
  homepage_url text check (homepage_url is null or homepage_url like 'https://%'),
  description text check (description is null or char_length(description) <= 4000),
  is_active boolean not null default true,
  is_score_sample boolean not null default false,
  is_scored boolean not null default true,
  market_cap_rank integer,
  editorial_notes text check (editorial_notes is null or char_length(editorial_notes) <= 2000),
  metadata_source text not null default 'coingecko',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index cryptocurrencies_rank_idx on public.cryptocurrencies (market_cap_rank) where is_active;

create trigger cryptocurrencies_updated_at before update on public.cryptocurrencies
  for each row execute function public.set_updated_at();

alter table public.cryptocurrencies enable row level security;
create policy "coins: public read active" on public.cryptocurrencies
  for select to anon, authenticated using (is_active or (select public.is_staff()));
create policy "coins: staff update metadata" on public.cryptocurrencies
  for update to authenticated using ((select public.is_staff())) with check ((select public.is_staff()));
revoke insert, update, delete on public.cryptocurrencies from anon;
revoke insert, delete on public.cryptocurrencies from authenticated;
revoke update on public.cryptocurrencies from authenticated;
grant update (editorial_notes, is_active, is_score_sample, is_scored, categories, description) on public.cryptocurrencies to authenticated;

-- ---------------------------------------------------------- market snapshots
-- One row per coin per refresh bucket. The unique key makes refresh jobs idempotent.
create table public.market_snapshots (
  id bigint generated always as identity primary key,
  coin_id text not null references public.cryptocurrencies (id) on delete cascade,
  source text not null check (source in ('coingecko', 'mock')),
  captured_at timestamptz not null,
  provider_updated_at timestamptz,
  price_usd numeric(38, 18) check (price_usd >= 0),
  market_cap_usd numeric(38, 2) check (market_cap_usd >= 0),
  fdv_usd numeric(38, 2) check (fdv_usd >= 0),
  volume_24h_usd numeric(38, 2) check (volume_24h_usd >= 0),
  change_1h_pct double precision,
  change_24h_pct double precision,
  change_7d_pct double precision,
  change_30d_pct double precision,
  circulating_supply numeric(38, 8) check (circulating_supply >= 0),
  total_supply numeric(38, 8) check (total_supply >= 0),
  max_supply numeric(38, 8) check (max_supply >= 0),
  market_cap_rank integer,
  unique (coin_id, source, captured_at)
);
create index market_snapshots_coin_time_idx on public.market_snapshots (coin_id, captured_at desc);
create index market_snapshots_time_idx on public.market_snapshots (captured_at);

alter table public.market_snapshots enable row level security;
create policy "market_snapshots: public read" on public.market_snapshots
  for select to anon, authenticated using (true);
revoke insert, update, delete, truncate on public.market_snapshots from anon, authenticated;

create view public.latest_market_snapshots
with (security_invoker = true) as
select distinct on (coin_id) *
from public.market_snapshots
order by coin_id, captured_at desc;

-- --------------------------------------------------------- historical prices
create table public.historical_prices (
  coin_id text not null references public.cryptocurrencies (id) on delete cascade,
  source text not null check (source in ('coingecko', 'mock')),
  granularity text not null check (granularity in ('1d')),
  ts timestamptz not null,
  price_usd numeric(38, 18) not null check (price_usd >= 0),
  market_cap_usd numeric(38, 2) check (market_cap_usd >= 0),
  volume_usd numeric(38, 2) check (volume_usd >= 0),
  primary key (coin_id, source, granularity, ts)
);

alter table public.historical_prices enable row level security;
create policy "historical_prices: public read" on public.historical_prices
  for select to anon, authenticated using (true);
revoke insert, update, delete, truncate on public.historical_prices from anon, authenticated;

-- ------------------------------------------------------- tokenomics snapshots
-- One supply observation per coin per day, used to estimate inflation.
create table public.tokenomics_snapshots (
  coin_id text not null references public.cryptocurrencies (id) on delete cascade,
  source text not null check (source in ('coingecko', 'mock')),
  snapshot_date date not null,
  circulating_supply numeric(38, 8) check (circulating_supply >= 0),
  total_supply numeric(38, 8) check (total_supply >= 0),
  max_supply numeric(38, 8) check (max_supply >= 0),
  provenance public.data_provenance not null default 'provider',
  captured_at timestamptz not null default now(),
  primary key (coin_id, source, snapshot_date)
);

alter table public.tokenomics_snapshots enable row level security;
create policy "tokenomics_snapshots: public read" on public.tokenomics_snapshots
  for select to anon, authenticated using (true);
revoke insert, update, delete, truncate on public.tokenomics_snapshots from anon, authenticated;

-- --------------------------------------------------------- token unlocks
-- Never fabricated: rows come from a configured provider or are entered by
-- staff from a cited project source, with provenance and confidence.
create table public.token_unlock_events (
  id uuid primary key default gen_random_uuid(),
  coin_id text not null references public.cryptocurrencies (id) on delete cascade,
  unlock_at timestamptz not null,
  amount numeric(38, 8) not null check (amount > 0),
  unlock_type text not null default 'cliff' check (unlock_type in ('cliff', 'linear', 'other')),
  allocation text check (allocation is null or char_length(allocation) <= 80),
  source_type public.data_provenance not null,
  source_name text check (source_name is null or char_length(source_name) <= 120),
  source_url text check (source_url is null or source_url like 'https://%'),
  confidence text not null default 'medium' check (confidence in ('high', 'medium', 'low')),
  notes text check (notes is null or char_length(notes) <= 1000),
  status text not null default 'scheduled' check (status in ('scheduled', 'completed', 'cancelled')),
  created_by uuid references auth.users (id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (source_type = 'provider' or source_url is not null)
);
create index token_unlock_events_coin_time_idx on public.token_unlock_events (coin_id, unlock_at);
create index token_unlock_events_upcoming_idx on public.token_unlock_events (unlock_at) where status = 'scheduled';

create trigger token_unlock_events_updated_at before update on public.token_unlock_events
  for each row execute function public.set_updated_at();

alter table public.token_unlock_events enable row level security;
create policy "unlocks: public read" on public.token_unlock_events
  for select to anon, authenticated using (true);
create policy "unlocks: staff insert" on public.token_unlock_events
  for insert to authenticated with check ((select public.is_staff()) and created_by = (select auth.uid()));
create policy "unlocks: staff update" on public.token_unlock_events
  for update to authenticated using ((select public.is_staff())) with check ((select public.is_staff()));
create policy "unlocks: staff delete" on public.token_unlock_events
  for delete to authenticated using ((select public.is_staff()));
revoke insert, update, delete on public.token_unlock_events from anon;

-- ----------------------------------------------------------- scoring configs
create table public.scoring_configs (
  version integer generated always as identity primary key,
  methodology_version text not null check (methodology_version ~ '^\d+\.\d+\.\d+$'),
  config jsonb not null,
  is_active boolean not null default false,
  notes text check (notes is null or char_length(notes) <= 1000),
  created_by uuid references auth.users (id) on delete set null,
  created_at timestamptz not null default now()
);
create unique index scoring_configs_one_active on public.scoring_configs (is_active) where is_active;

alter table public.scoring_configs enable row level security;
-- Methodology is public for transparency.
create policy "scoring_configs: public read" on public.scoring_configs
  for select to anon, authenticated using (true);
revoke insert, update, delete, truncate on public.scoring_configs from anon, authenticated;

-- Publishing a config is a single atomic admin operation.
create or replace function public.publish_scoring_config(p_methodology_version text, p_config jsonb, p_notes text)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  new_version integer;
begin
  if not public.is_admin() then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  update public.scoring_configs set is_active = false where is_active;
  insert into public.scoring_configs (methodology_version, config, is_active, notes, created_by)
  values (p_methodology_version, p_config, true, p_notes, auth.uid())
  returning version into new_version;
  insert into public.admin_audit_logs (actor_id, action, target_type, target_id, details)
  values (auth.uid(), 'scoring_config.publish', 'scoring_config', new_version::text,
          jsonb_build_object('methodology_version', p_methodology_version));
  return new_version;
end;
$$;
revoke execute on function public.publish_scoring_config(text, jsonb, text) from public, anon;
grant execute on function public.publish_scoring_config(text, jsonb, text) to authenticated;

-- ------------------------------------------------------------ research scores
-- Latest score per coin. Free users can read sample coins only.
create table public.research_scores (
  coin_id text primary key references public.cryptocurrencies (id) on delete cascade,
  methodology_version text not null,
  config_version integer references public.scoring_configs (version),
  status text not null check (status in ('scored', 'insufficient_data')),
  overall_score numeric(5, 1) check (overall_score between 0 and 100),
  coverage numeric(5, 4) not null check (coverage between 0 and 1),
  subscores jsonb not null,
  flags text[] not null default '{}',
  inputs_hash text not null,
  data_as_of timestamptz,
  calculated_at timestamptz not null,
  previous_overall_score numeric(5, 1),
  check ((status = 'scored') = (overall_score is not null))
);

alter table public.research_scores enable row level security;
create policy "scores: sample coins public, all coins for premium" on public.research_scores
  for select to anon, authenticated using (
    exists (select 1 from public.cryptocurrencies c where c.id = coin_id and c.is_score_sample)
    or public.tier_rank((select public.current_membership_tier())) >= 1
    or (select public.is_staff())
  );
revoke insert, update, delete, truncate on public.research_scores from anon, authenticated;

create table public.research_score_snapshots (
  id bigint generated always as identity primary key,
  coin_id text not null references public.cryptocurrencies (id) on delete cascade,
  methodology_version text not null,
  config_version integer references public.scoring_configs (version),
  status text not null check (status in ('scored', 'insufficient_data')),
  overall_score numeric(5, 1) check (overall_score between 0 and 100),
  coverage numeric(5, 4) not null,
  subscores jsonb not null,
  flags text[] not null default '{}',
  inputs jsonb not null,
  inputs_hash text not null,
  data_as_of timestamptz,
  calculated_at timestamptz not null,
  -- Re-running the job for the same inputs/methodology does not create duplicates.
  unique (coin_id, methodology_version, inputs_hash)
);
create index research_score_snapshots_coin_time_idx on public.research_score_snapshots (coin_id, calculated_at desc);

alter table public.research_score_snapshots enable row level security;
create policy "score history: premium" on public.research_score_snapshots
  for select to authenticated using (
    public.tier_rank((select public.current_membership_tier())) >= 1 or (select public.is_staff())
  );
revoke all on public.research_score_snapshots from anon;
revoke insert, update, delete, truncate on public.research_score_snapshots from authenticated;

-- ---------------------------------------------------------- research articles
create table public.research_articles (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique check (slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$' and char_length(slug) <= 120),
  title text not null check (char_length(title) between 3 and 200),
  summary text not null check (char_length(summary) between 10 and 600),
  category text not null check (category in ('market', 'tokenomics', 'fundamentals', 'risk', 'methodology', 'education')),
  tags text[] not null default '{}',
  access_level public.membership_tier not null default 'free',
  status text not null default 'draft' check (status in ('draft', 'published', 'archived')),
  author_id uuid references auth.users (id) on delete set null,
  author_name text not null check (char_length(author_name) between 1 and 80),
  related_coin_ids text[] not null default '{}',
  data_sources jsonb not null default '[]'::jsonb check (jsonb_typeof(data_sources) = 'array'),
  is_ai_assisted boolean not null default false,
  published_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (status <> 'published' or published_at is not null)
);
create index research_articles_published_idx on public.research_articles (published_at desc) where status = 'published';
create index research_articles_category_idx on public.research_articles (category, published_at desc) where status = 'published';
create index research_articles_coins_idx on public.research_articles using gin (related_coin_ids);
create index research_articles_search_idx on public.research_articles
  using gin (to_tsvector('english', title || ' ' || summary));

create trigger research_articles_updated_at before update on public.research_articles
  for each row execute function public.set_updated_at();

alter table public.research_articles enable row level security;
-- Metadata (title/summary) of published articles is public, including premium ones (teasers).
create policy "articles: public read published" on public.research_articles
  for select to anon, authenticated using ((status = 'published' and published_at <= now()) or (select public.is_staff()));
create policy "articles: staff insert" on public.research_articles
  for insert to authenticated with check ((select public.is_staff()));
create policy "articles: staff update" on public.research_articles
  for update to authenticated using ((select public.is_staff())) with check ((select public.is_staff()));
create policy "articles: admins delete" on public.research_articles
  for delete to authenticated using ((select public.is_admin()));
revoke insert, update, delete on public.research_articles from anon;

-- Article bodies are separated so premium content is enforced by RLS.
create table public.research_article_bodies (
  article_id uuid primary key references public.research_articles (id) on delete cascade,
  body_markdown text not null check (char_length(body_markdown) <= 200000),
  updated_at timestamptz not null default now()
);

create trigger research_article_bodies_updated_at before update on public.research_article_bodies
  for each row execute function public.set_updated_at();

alter table public.research_article_bodies enable row level security;
create policy "article bodies: entitled readers" on public.research_article_bodies
  for select to anon, authenticated using (
    exists (
      select 1 from public.research_articles a
      where a.id = article_id
        and a.status = 'published'
        and a.published_at <= now()
        and public.tier_rank((select public.current_membership_tier())) >= public.tier_rank(a.access_level)
    )
    or (select public.is_staff())
  );
create policy "article bodies: staff insert" on public.research_article_bodies
  for insert to authenticated with check ((select public.is_staff()));
create policy "article bodies: staff update" on public.research_article_bodies
  for update to authenticated using ((select public.is_staff())) with check ((select public.is_staff()));
revoke insert, update, delete on public.research_article_bodies from anon;
