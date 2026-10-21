-- =============================================================================
-- 0002 Membership: plans, subscriptions, billing events, entitlement functions.
--
-- Users can READ their own subscription and payment history but can never
-- write them. All writes come from the verified billing webhook running with
-- the service role.
-- =============================================================================

create type public.subscription_status as enum
  ('incomplete', 'trialing', 'active', 'past_due', 'canceled', 'expired', 'refunded');

create or replace function public.tier_rank(t public.membership_tier)
returns integer
language sql
immutable
set search_path = ''
as $$
  select case t when 'free' then 0 when 'premium' then 1 when 'founding' then 2 end;
$$;

-- ---------------------------------------------------------- membership plans
-- Mirrors src/config/plans.ts (source of truth). Synced by `npm run job -- sync-plans`.
create table public.membership_plans (
  id text primary key check (id ~ '^[a-z][a-z0-9_]{1,59}$'),
  tier public.membership_tier not null,
  name text not null check (char_length(name) <= 80),
  billing_interval text check (billing_interval in ('month', 'year')),
  price_cents integer not null default 0 check (price_cents >= 0),
  currency char(3) not null default 'USD',
  is_active boolean not null default true,
  limits jsonb not null default '{}'::jsonb,
  provider_price_ids jsonb not null default '{}'::jsonb,
  sort_order integer not null default 0,
  updated_at timestamptz not null default now(),
  check ((tier = 'free') = (billing_interval is null))
);

create trigger membership_plans_updated_at before update on public.membership_plans
  for each row execute function public.set_updated_at();

alter table public.membership_plans enable row level security;
create policy "plans: public read active" on public.membership_plans
  for select to anon, authenticated using (is_active);
revoke insert, update, delete on public.membership_plans from anon, authenticated;

-- Illustrative values; keep identical to src/config/plans.ts.
insert into public.membership_plans (id, tier, name, billing_interval, price_cents, limits, sort_order) values
  ('free', 'free', 'Free', null, 0,
   '{"watchlists":1,"watchlistItems":10,"portfolios":0,"alerts":0,"notificationsPerHour":0,"aiRequestsPerDay":3}', 0),
  ('premium_monthly', 'premium', 'Premium — Monthly', 'month', 1900,
   '{"watchlists":10,"watchlistItems":100,"portfolios":5,"alerts":50,"notificationsPerHour":20,"aiRequestsPerDay":50}', 1),
  ('premium_yearly', 'premium', 'Premium — Yearly', 'year', 16000,
   '{"watchlists":10,"watchlistItems":100,"portfolios":5,"alerts":50,"notificationsPerHour":20,"aiRequestsPerDay":50}', 2),
  ('founding_yearly', 'founding', 'Founding Member — Yearly', 'year', 30000,
   '{"watchlists":25,"watchlistItems":250,"portfolios":20,"alerts":200,"notificationsPerHour":60,"aiRequestsPerDay":150}', 3);

-- ------------------------------------------------------------ subscriptions
create table public.subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  plan_id text not null references public.membership_plans (id),
  tier public.membership_tier not null check (tier <> 'free'),
  status public.subscription_status not null,
  provider text not null check (char_length(provider) <= 40),
  provider_subscription_id text not null check (char_length(provider_subscription_id) <= 200),
  provider_customer_id text check (provider_customer_id is null or char_length(provider_customer_id) <= 200),
  livemode boolean not null,
  current_period_start timestamptz,
  current_period_end timestamptz,
  cancel_at_period_end boolean not null default false,
  canceled_at timestamptz,
  ended_at timestamptz,
  last_event_at timestamptz not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (provider, provider_subscription_id)
);
create index subscriptions_user_idx on public.subscriptions (user_id);
-- At most one non-terminal subscription per user (prevents duplicate checkouts).
create unique index subscriptions_one_open_per_user
  on public.subscriptions (user_id)
  where status in ('incomplete', 'trialing', 'active', 'past_due');

create trigger subscriptions_updated_at before update on public.subscriptions
  for each row execute function public.set_updated_at();

alter table public.subscriptions enable row level security;
revoke all on public.subscriptions from anon;
revoke insert, update, delete, truncate on public.subscriptions from authenticated;
create policy "subscriptions: owner reads" on public.subscriptions
  for select to authenticated using (user_id = (select auth.uid()));
create policy "subscriptions: admins read" on public.subscriptions
  for select to authenticated using ((select public.is_admin()));

-- ---------------------------------------------------------- entitlement logic
-- Mirrors src/lib/billing/state.ts `subscriptionGrantsAccess`.
create or replace function public.subscription_grants_access(
  s_status public.subscription_status,
  s_period_end timestamptz,
  s_livemode boolean
)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select
    case
      when coalesce((select (value)::text::boolean from public.app_config where key = 'require_livemode_subscriptions'), true)
           and not s_livemode then false
      when s_period_end is null then false
      when s_status in ('active', 'trialing') then now() < s_period_end
      when s_status = 'past_due' then now() < s_period_end + make_interval(days =>
        coalesce((select (value)::text::int from public.app_config where key = 'billing_grace_period_days'), 0))
      else false
    end;
$$;

-- Internal: tier for an arbitrary user. NOT callable by clients (would leak other users' tiers).
create or replace function public.membership_tier_for(uid uuid)
returns public.membership_tier
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(
    (select s.tier from public.subscriptions s
      where s.user_id = uid
        and public.subscription_grants_access(s.status, s.current_period_end, s.livemode)
      order by public.tier_rank(s.tier) desc
      limit 1),
    'free'::public.membership_tier
  );
$$;
revoke execute on function public.membership_tier_for(uuid) from public, anon, authenticated;
grant execute on function public.membership_tier_for(uuid) to service_role;

-- The caller's own tier (anon ⇒ free).
create or replace function public.current_membership_tier()
returns public.membership_tier
language sql
stable
security definer
set search_path = ''
as $$
  select case when (select auth.uid()) is null then 'free'::public.membership_tier
              else public.membership_tier_for((select auth.uid())) end;
$$;
grant execute on function public.current_membership_tier() to anon, authenticated, service_role;

-- Numeric limit for a user's current tier (largest across that tier's plans).
create or replace function public.tier_limit_for(uid uuid, limit_key text)
returns integer
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(max((p.limits ->> limit_key)::int), 0)
  from public.membership_plans p
  where p.tier = public.membership_tier_for(uid);
$$;
revoke execute on function public.tier_limit_for(uuid, text) from public, anon, authenticated;
grant execute on function public.tier_limit_for(uuid, text) to service_role;

-- --------------------------------------------------------- billing events
create table public.billing_events (
  id uuid primary key default gen_random_uuid(),
  provider text not null check (char_length(provider) <= 40),
  provider_event_id text not null check (char_length(provider_event_id) <= 200),
  event_type text not null check (char_length(event_type) <= 80),
  occurred_at timestamptz not null,
  received_at timestamptz not null default now(),
  livemode boolean not null,
  user_id uuid references auth.users (id) on delete set null,
  subscription_id uuid references public.subscriptions (id) on delete set null,
  payload jsonb not null,
  status text not null default 'received' check (status in ('received', 'processed', 'ignored', 'failed')),
  processing_note text check (processing_note is null or char_length(processing_note) <= 500),
  processed_at timestamptz,
  unique (provider, provider_event_id)
);
create index billing_events_received_idx on public.billing_events (received_at desc);
create index billing_events_status_idx on public.billing_events (status) where status in ('received', 'failed');

alter table public.billing_events enable row level security;
revoke all on public.billing_events from anon;
revoke insert, update, delete, truncate on public.billing_events from authenticated;
create policy "billing_events: admins read" on public.billing_events
  for select to authenticated using ((select public.is_admin()));

-- User-visible payment history (no raw provider payloads).
create table public.billing_payments (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  subscription_id uuid references public.subscriptions (id) on delete set null,
  billing_event_id uuid not null unique references public.billing_events (id) on delete cascade,
  plan_id text references public.membership_plans (id),
  amount_cents integer not null check (amount_cents >= 0),
  currency char(3) not null,
  kind text not null check (kind in ('payment', 'refund', 'failed_payment')),
  livemode boolean not null,
  occurred_at timestamptz not null,
  created_at timestamptz not null default now()
);
create index billing_payments_user_idx on public.billing_payments (user_id, occurred_at desc);

alter table public.billing_payments enable row level security;
revoke all on public.billing_payments from anon;
revoke insert, update, delete, truncate on public.billing_payments from authenticated;
create policy "billing_payments: owner reads" on public.billing_payments
  for select to authenticated using (user_id = (select auth.uid()));
create policy "billing_payments: admins read" on public.billing_payments
  for select to authenticated using ((select public.is_admin()));

-- ---------------------------------------------------------- checkout sessions
create table public.checkout_sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  plan_id text not null references public.membership_plans (id),
  provider text not null,
  provider_session_id text not null,
  status text not null default 'open' check (status in ('open', 'completed', 'expired', 'canceled')),
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  unique (provider, provider_session_id)
);
create index checkout_sessions_user_idx on public.checkout_sessions (user_id, created_at desc);

alter table public.checkout_sessions enable row level security;
revoke all on public.checkout_sessions from anon;
revoke insert, update, delete, truncate on public.checkout_sessions from authenticated;
create policy "checkout_sessions: owner reads" on public.checkout_sessions
  for select to authenticated using (user_id = (select auth.uid()));
