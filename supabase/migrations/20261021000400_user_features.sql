-- =============================================================================
-- 0004 User-owned data: portfolios, transactions, watchlists, alerts,
-- notifications, Telegram links.
--
-- Every row carries user_id and RLS restricts all access to the owner.
-- Child rows (transactions, watchlist items) additionally verify that the
-- parent belongs to the same user, so a user cannot attach rows to someone
-- else's portfolio by guessing its id. Tier limits are enforced by triggers.
-- =============================================================================

-- Raise a recognizable error the API layer maps to "limit_reached".
create or replace function public.enforce_tier_limit(uid uuid, limit_key text, current_count bigint)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  lim integer := public.tier_limit_for(uid, limit_key);
begin
  if current_count >= lim then
    raise exception 'limit_reached:%', limit_key using errcode = 'P0001',
      hint = format('Your plan allows %s.', lim);
  end if;
end;
$$;
revoke execute on function public.enforce_tier_limit(uuid, text, bigint) from public, anon, authenticated;

-- --------------------------------------------------------------- portfolios
create table public.portfolios (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  name text not null check (char_length(name) between 1 and 60),
  base_currency char(3) not null default 'USD' check (base_currency = 'USD'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, name)
);
create index portfolios_user_idx on public.portfolios (user_id);

create trigger portfolios_updated_at before update on public.portfolios
  for each row execute function public.set_updated_at();

create or replace function public.portfolios_limit_check()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform pg_advisory_xact_lock(hashtext('portfolios:' || new.user_id::text));
  perform public.enforce_tier_limit(new.user_id, 'portfolios',
    (select count(*) from public.portfolios where user_id = new.user_id));
  return new;
end;
$$;
create trigger portfolios_limit before insert on public.portfolios
  for each row execute function public.portfolios_limit_check();

alter table public.portfolios enable row level security;
revoke all on public.portfolios from anon;
create policy "portfolios: owner select" on public.portfolios
  for select to authenticated using (user_id = (select auth.uid()));
create policy "portfolios: owner insert" on public.portfolios
  for insert to authenticated with check (user_id = (select auth.uid()));
create policy "portfolios: owner update" on public.portfolios
  for update to authenticated using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
create policy "portfolios: owner delete" on public.portfolios
  for delete to authenticated using (user_id = (select auth.uid()));

-- ---------------------------------------------------- portfolio transactions
create type public.transaction_type as enum ('buy', 'sell', 'transfer_in', 'transfer_out');

create table public.portfolio_transactions (
  id uuid primary key default gen_random_uuid(),
  portfolio_id uuid not null references public.portfolios (id) on delete cascade,
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  coin_id text not null references public.cryptocurrencies (id),
  type public.transaction_type not null,
  quantity numeric(38, 18) not null check (quantity > 0),
  price_usd numeric(38, 18) check (price_usd >= 0),
  fee_usd numeric(38, 18) not null default 0 check (fee_usd >= 0),
  occurred_at timestamptz not null check (occurred_at >= '2009-01-03'::timestamptz),
  notes text check (notes is null or char_length(notes) <= 500),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (type = 'transfer_out' or price_usd is not null)
);
create index portfolio_transactions_portfolio_idx on public.portfolio_transactions (portfolio_id, occurred_at);
create index portfolio_transactions_user_idx on public.portfolio_transactions (user_id);

create trigger portfolio_transactions_updated_at before update on public.portfolio_transactions
  for each row execute function public.set_updated_at();

create or replace function public.portfolio_transactions_guard()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.occurred_at > now() + interval '5 minutes' then
    raise exception 'invalid_transaction:occurred_at in the future' using errcode = 'P0001';
  end if;
  if tg_op = 'INSERT' and public.membership_tier_for(new.user_id) = 'free' then
    raise exception 'limit_reached:portfolios' using errcode = 'P0001', hint = 'Portfolios require a Premium membership.';
  end if;
  return new;
end;
$$;
create trigger portfolio_transactions_guard before insert or update on public.portfolio_transactions
  for each row execute function public.portfolio_transactions_guard();

alter table public.portfolio_transactions enable row level security;
revoke all on public.portfolio_transactions from anon;
create policy "transactions: owner select" on public.portfolio_transactions
  for select to authenticated using (user_id = (select auth.uid()));
create policy "transactions: owner insert into own portfolio" on public.portfolio_transactions
  for insert to authenticated with check (
    user_id = (select auth.uid())
    and exists (select 1 from public.portfolios p where p.id = portfolio_id and p.user_id = (select auth.uid()))
  );
create policy "transactions: owner update" on public.portfolio_transactions
  for update to authenticated
  using (user_id = (select auth.uid()))
  with check (
    user_id = (select auth.uid())
    and exists (select 1 from public.portfolios p where p.id = portfolio_id and p.user_id = (select auth.uid()))
  );
create policy "transactions: owner delete" on public.portfolio_transactions
  for delete to authenticated using (user_id = (select auth.uid()));

-- --------------------------------------------------------------- watchlists
create table public.watchlists (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  name text not null check (char_length(name) between 1 and 60),
  created_at timestamptz not null default now(),
  unique (user_id, name)
);
create index watchlists_user_idx on public.watchlists (user_id);

create or replace function public.watchlists_limit_check()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform pg_advisory_xact_lock(hashtext('watchlists:' || new.user_id::text));
  perform public.enforce_tier_limit(new.user_id, 'watchlists',
    (select count(*) from public.watchlists where user_id = new.user_id));
  return new;
end;
$$;
create trigger watchlists_limit before insert on public.watchlists
  for each row execute function public.watchlists_limit_check();

alter table public.watchlists enable row level security;
revoke all on public.watchlists from anon;
create policy "watchlists: owner select" on public.watchlists
  for select to authenticated using (user_id = (select auth.uid()));
create policy "watchlists: owner insert" on public.watchlists
  for insert to authenticated with check (user_id = (select auth.uid()));
create policy "watchlists: owner update" on public.watchlists
  for update to authenticated using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
create policy "watchlists: owner delete" on public.watchlists
  for delete to authenticated using (user_id = (select auth.uid()));

create table public.watchlist_items (
  watchlist_id uuid not null references public.watchlists (id) on delete cascade,
  coin_id text not null references public.cryptocurrencies (id) on delete cascade,
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  added_at timestamptz not null default now(),
  primary key (watchlist_id, coin_id)
);
create index watchlist_items_user_idx on public.watchlist_items (user_id);

create or replace function public.watchlist_items_limit_check()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform pg_advisory_xact_lock(hashtext('watchlist_items:' || new.user_id::text));
  perform public.enforce_tier_limit(new.user_id, 'watchlistItems',
    (select count(*) from public.watchlist_items where watchlist_id = new.watchlist_id));
  return new;
end;
$$;
create trigger watchlist_items_limit before insert on public.watchlist_items
  for each row execute function public.watchlist_items_limit_check();

alter table public.watchlist_items enable row level security;
revoke all on public.watchlist_items from anon;
revoke update on public.watchlist_items from authenticated;
create policy "watchlist_items: owner select" on public.watchlist_items
  for select to authenticated using (user_id = (select auth.uid()));
create policy "watchlist_items: owner insert into own list" on public.watchlist_items
  for insert to authenticated with check (
    user_id = (select auth.uid())
    and exists (select 1 from public.watchlists w where w.id = watchlist_id and w.user_id = (select auth.uid()))
  );
create policy "watchlist_items: owner delete" on public.watchlist_items
  for delete to authenticated using (user_id = (select auth.uid()));

-- ------------------------------------------------------------------ alerts
create type public.alert_type as enum (
  'price_above', 'price_below', 'pct_change_24h_above', 'pct_change_24h_below',
  'volume_change_24h_above', 'score_change', 'unlock_upcoming',
  'portfolio_value_above', 'portfolio_value_below'
);

create table public.alerts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  type public.alert_type not null,
  coin_id text references public.cryptocurrencies (id) on delete cascade,
  portfolio_id uuid references public.portfolios (id) on delete cascade,
  threshold double precision not null,
  channels text[] not null check (
    cardinality(channels) between 1 and 3 and channels <@ array['in_app', 'email', 'telegram']::text[]
  ),
  trigger_mode text not null default 'recurring' check (trigger_mode in ('once', 'recurring')),
  cooldown_minutes integer not null default 60 check (cooldown_minutes between 5 and 10080),
  is_active boolean not null default true,
  last_condition_met boolean,
  last_triggered_at timestamptz,
  last_evaluated_at timestamptz,
  last_observed_value double precision,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (
    (type in ('portfolio_value_above', 'portfolio_value_below') and portfolio_id is not null and coin_id is null)
    or (type not in ('portfolio_value_above', 'portfolio_value_below') and coin_id is not null and portfolio_id is null)
  )
);
create index alerts_user_idx on public.alerts (user_id);
create index alerts_active_idx on public.alerts (type, coin_id) where is_active;

create trigger alerts_updated_at before update on public.alerts
  for each row execute function public.set_updated_at();

create or replace function public.alerts_limit_check()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform pg_advisory_xact_lock(hashtext('alerts:' || new.user_id::text));
  perform public.enforce_tier_limit(new.user_id, 'alerts',
    (select count(*) from public.alerts where user_id = new.user_id));
  return new;
end;
$$;
create trigger alerts_limit before insert on public.alerts
  for each row execute function public.alerts_limit_check();

alter table public.alerts enable row level security;
revoke all on public.alerts from anon;
-- Users may edit configuration columns only; evaluation state is job-owned.
revoke update on public.alerts from authenticated;
grant update (threshold, channels, trigger_mode, cooldown_minutes, is_active) on public.alerts to authenticated;
create policy "alerts: owner select" on public.alerts
  for select to authenticated using (user_id = (select auth.uid()));
create policy "alerts: owner insert" on public.alerts
  for insert to authenticated with check (
    user_id = (select auth.uid())
    and (portfolio_id is null or exists (select 1 from public.portfolios p where p.id = portfolio_id and p.user_id = (select auth.uid())))
  );
create policy "alerts: owner update" on public.alerts
  for update to authenticated using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
create policy "alerts: owner delete" on public.alerts
  for delete to authenticated using (user_id = (select auth.uid()));

create table public.alert_evaluations (
  id bigint generated always as identity primary key,
  alert_id uuid not null references public.alerts (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  evaluated_at timestamptz not null,
  outcome text not null check (outcome in ('triggered', 'not_met', 'suppressed', 'skipped')),
  observed_value double precision,
  reason text check (reason is null or char_length(reason) <= 300),
  data_as_of timestamptz,
  job_run_id uuid
);
create index alert_evaluations_alert_idx on public.alert_evaluations (alert_id, evaluated_at desc);
create index alert_evaluations_time_idx on public.alert_evaluations (evaluated_at);

alter table public.alert_evaluations enable row level security;
revoke all on public.alert_evaluations from anon;
revoke insert, update, delete, truncate on public.alert_evaluations from authenticated;
create policy "alert_evaluations: owner select" on public.alert_evaluations
  for select to authenticated using (user_id = (select auth.uid()));

-- ------------------------------------------------------------ notifications
create table public.notifications (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  alert_id uuid references public.alerts (id) on delete set null,
  kind text not null check (kind in ('alert', 'system', 'billing')),
  title text not null check (char_length(title) <= 200),
  body text not null check (char_length(body) <= 2000),
  data jsonb not null default '{}'::jsonb,
  -- Unique: the same alert window can never produce two notifications.
  dedupe_key text not null unique check (char_length(dedupe_key) <= 200),
  read_at timestamptz,
  created_at timestamptz not null default now()
);
create index notifications_user_idx on public.notifications (user_id, created_at desc);

alter table public.notifications enable row level security;
revoke all on public.notifications from anon;
revoke insert, update, delete, truncate on public.notifications from authenticated;
grant update (read_at) on public.notifications to authenticated;
create policy "notifications: owner select" on public.notifications
  for select to authenticated using (user_id = (select auth.uid()));
create policy "notifications: owner marks read" on public.notifications
  for update to authenticated using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

create table public.notification_deliveries (
  id uuid primary key default gen_random_uuid(),
  notification_id uuid not null references public.notifications (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  channel text not null check (channel in ('in_app', 'email', 'telegram')),
  status text not null default 'pending' check (status in ('pending', 'sending', 'sent', 'failed', 'skipped')),
  attempts integer not null default 0 check (attempts >= 0),
  next_attempt_at timestamptz not null default now(),
  locked_until timestamptz,
  last_error text check (last_error is null or char_length(last_error) <= 500),
  provider_message_id text,
  sent_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (notification_id, channel)
);
create index notification_deliveries_due_idx on public.notification_deliveries (next_attempt_at) where status = 'pending';
create index notification_deliveries_user_sent_idx on public.notification_deliveries (user_id, sent_at) where status = 'sent';

create trigger notification_deliveries_updated_at before update on public.notification_deliveries
  for each row execute function public.set_updated_at();

alter table public.notification_deliveries enable row level security;
revoke all on public.notification_deliveries from anon;
revoke insert, update, delete, truncate on public.notification_deliveries from authenticated;
create policy "deliveries: owner select" on public.notification_deliveries
  for select to authenticated using (user_id = (select auth.uid()));
create policy "deliveries: admins select" on public.notification_deliveries
  for select to authenticated using ((select public.is_admin()));

-- Claim due deliveries atomically (safe with concurrent workers).
create or replace function public.claim_due_deliveries(batch_size integer, lock_seconds integer)
returns setof public.notification_deliveries
language sql
security definer
set search_path = ''
as $$
  update public.notification_deliveries d
     set status = 'sending', locked_until = now() + make_interval(secs => lock_seconds)
   where d.id in (
     select id from public.notification_deliveries
      where (status = 'pending' and next_attempt_at <= now())
         or (status = 'sending' and locked_until < now())
      order by next_attempt_at
      limit batch_size
      for update skip locked
   )
  returning d.*;
$$;
revoke execute on function public.claim_due_deliveries(integer, integer) from public, anon, authenticated;
grant execute on function public.claim_due_deliveries(integer, integer) to service_role;

-- ---------------------------------------------------------------- telegram
create table public.telegram_accounts (
  user_id uuid primary key references auth.users (id) on delete cascade,
  chat_id bigint not null unique,
  telegram_user_id bigint not null,
  username text check (username is null or char_length(username) <= 64),
  linked_at timestamptz not null default now()
);

alter table public.telegram_accounts enable row level security;
revoke all on public.telegram_accounts from anon;
revoke insert, update, truncate on public.telegram_accounts from authenticated;
create policy "telegram: owner select" on public.telegram_accounts
  for select to authenticated using (user_id = (select auth.uid()));
create policy "telegram: owner unlink" on public.telegram_accounts
  for delete to authenticated using (user_id = (select auth.uid()));

-- Single-use, expiring link tokens. Only the SHA-256 hash is stored.
create table public.telegram_link_tokens (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  token_hash text not null unique check (token_hash ~ '^[a-f0-9]{64}$'),
  expires_at timestamptz not null,
  used_at timestamptz,
  created_at timestamptz not null default now()
);
create index telegram_link_tokens_user_idx on public.telegram_link_tokens (user_id, created_at desc);

alter table public.telegram_link_tokens enable row level security;
revoke all on public.telegram_link_tokens from anon, authenticated;
