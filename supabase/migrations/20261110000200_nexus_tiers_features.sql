-- =============================================================================
-- 0008 NEXUS tiers (part 2) and feature entitlements.
--
-- Tiers are now free < pro < premium (src/config/plans.ts). The legacy
-- `founding` tier is retired: no new founding subscriptions or articles, and
-- any legacy founding subscription is treated as premium. Enum values cannot
-- be dropped safely, so `founding` stays in the type but is unusable.
-- =============================================================================

-- ------------------------------------------------------------ tier ranking
create or replace function public.tier_rank(t public.membership_tier)
returns integer
language sql
immutable
set search_path = ''
as $$
  select case t when 'free' then 0 when 'pro' then 1 when 'premium' then 2 when 'founding' then 2 end;
$$;

-- The caller-facing tier never reports `founding`.
create or replace function public.membership_tier_for(uid uuid)
returns public.membership_tier
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(
    (select case when s.tier = 'founding' then 'premium'::public.membership_tier else s.tier end
      from public.subscriptions s
      where s.user_id = uid
        and public.subscription_grants_access(s.status, s.current_period_end, s.livemode)
      order by public.tier_rank(s.tier) desc
      limit 1),
    'free'::public.membership_tier
  );
$$;
revoke execute on function public.membership_tier_for(uuid) from public, anon, authenticated;
grant execute on function public.membership_tier_for(uuid) to service_role;

-- ------------------------------------------------------------ retire founding
create or replace function public.reject_retired_tier()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.tier = 'founding' then
    raise exception 'The founding tier is retired' using errcode = '23514';
  end if;
  return new;
end;
$$;
-- Insert only: legacy rows can still receive lifecycle updates (cancel, expire).
create trigger subscriptions_reject_retired_tier before insert on public.subscriptions
  for each row execute function public.reject_retired_tier();
create trigger membership_plans_reject_retired_tier before insert on public.membership_plans
  for each row execute function public.reject_retired_tier();

update public.research_articles set access_level = 'premium' where access_level = 'founding';
alter table public.research_articles
  add constraint research_articles_access_level_current check (access_level <> 'founding');

-- ------------------------------------------------------------ plans
-- Illustrative values; keep identical to src/config/plans.ts (sync-plans job).
insert into public.membership_plans (id, tier, name, billing_interval, price_cents, limits, sort_order) values
  ('pro_monthly', 'pro', 'Pro — Monthly', 'month', 900,
   '{"watchlists":5,"watchlistItems":50,"portfolios":2,"alerts":25,"notificationsPerHour":10,"aiRequestsPerDay":20}', 1),
  ('pro_yearly', 'pro', 'Pro — Yearly', 'year', 7900,
   '{"watchlists":5,"watchlistItems":50,"portfolios":2,"alerts":25,"notificationsPerHour":10,"aiRequestsPerDay":20}', 2)
on conflict (id) do nothing;
update public.membership_plans set sort_order = 3 where id = 'premium_monthly';
update public.membership_plans set sort_order = 4 where id = 'premium_yearly';
update public.membership_plans set is_active = false, sort_order = 99 where id = 'founding_yearly';

-- ------------------------------------------------------------ feature entitlements
-- Mirrors PLAN_FEATURES in src/config/plans.ts (synced by `sync-plans`).
create table public.plan_features (
  tier public.membership_tier not null check (tier <> 'founding'),
  feature_key text not null check (feature_key ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$' and char_length(feature_key) <= 64),
  enabled boolean not null,
  updated_at timestamptz not null default now(),
  primary key (tier, feature_key)
);

create trigger plan_features_updated_at before update on public.plan_features
  for each row execute function public.set_updated_at();

alter table public.plan_features enable row level security;
create policy "plan_features: public read" on public.plan_features
  for select to anon, authenticated using (true);
revoke insert, update, delete, truncate on public.plan_features from anon, authenticated;

insert into public.plan_features (tier, feature_key, enabled)
select t.tier::public.membership_tier, f.key, public.tier_rank(t.tier::public.membership_tier) >= public.tier_rank(f.min_tier::public.membership_tier)
from (values ('free'), ('pro'), ('premium')) as t(tier)
cross join (values
  ('charts.basic', 'free'),
  ('analytics.advanced', 'pro'),
  ('signals.history', 'pro'),
  ('signals.alerts', 'pro'),
  ('performance.reports', 'pro'),
  ('models.analytics', 'premium'),
  ('backtesting', 'premium'),
  ('strategies.custom', 'premium'),
  ('research.advanced_tools', 'premium')
) as f(key, min_tier);

-- Whether the caller's current tier includes a feature (anon ⇒ free tier).
create or replace function public.has_feature(feature text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(
    (select pf.enabled from public.plan_features pf
      where pf.tier = public.current_membership_tier() and pf.feature_key = feature),
    false
  );
$$;
revoke execute on function public.has_feature(text) from public;
grant execute on function public.has_feature(text) to anon, authenticated, service_role;

-- Internal: feature check for an arbitrary user (jobs, engine). Not for clients.
create or replace function public.has_feature_for(uid uuid, feature text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(
    (select pf.enabled from public.plan_features pf
      where pf.tier = public.membership_tier_for(uid) and pf.feature_key = feature),
    false
  );
$$;
revoke execute on function public.has_feature_for(uuid, text) from public, anon, authenticated;
grant execute on function public.has_feature_for(uuid, text) to service_role;
