-- =============================================================================
-- 0001 Core: profiles, roles, configuration, feature flags, audit log.
--
-- Conventions:
--   * Every table has RLS enabled. A table with RLS and no policy for a role
--     is inaccessible to that role (anon/authenticated). The service role
--     bypasses RLS and is only used server-side for jobs and webhooks.
--   * SECURITY DEFINER functions pin search_path = '' and fully qualify names.
--   * Privileges are revoked explicitly where a role must never write, as
--     defense in depth on top of RLS.
-- =============================================================================

create extension if not exists pgcrypto with schema extensions;

-- ------------------------------------------------------------------- helpers
create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- -------------------------------------------------------------------- types
create type public.app_role as enum ('admin', 'editor');
create type public.membership_tier as enum ('free', 'premium', 'founding');

-- ----------------------------------------------------------------- profiles
create table public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  display_name text check (display_name is null or char_length(display_name) between 1 and 80),
  timezone text not null default 'UTC' check (char_length(timezone) <= 64),
  theme text not null default 'dark' check (theme in ('dark', 'light', 'system')),
  email_notifications boolean not null default true,
  marketing_emails boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger profiles_updated_at before update on public.profiles
  for each row execute function public.set_updated_at();

alter table public.profiles enable row level security;

create policy "profiles: owner can read" on public.profiles
  for select to authenticated using (id = (select auth.uid()));

create policy "profiles: owner can update" on public.profiles
  for update to authenticated using (id = (select auth.uid())) with check (id = (select auth.uid()));

-- Only specific columns are user-editable; id/created_at are not.
revoke all on public.profiles from anon;
revoke insert, update, delete on public.profiles from authenticated;
grant select on public.profiles to authenticated;
grant update (display_name, timezone, theme, email_notifications, marketing_emails) on public.profiles to authenticated;

-- Create a profile automatically for each new auth user.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, display_name)
  values (
    new.id,
    nullif(left(coalesce(new.raw_user_meta_data ->> 'display_name', ''), 80), '')
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- --------------------------------------------------------------- user roles
-- Roles are assigned ONLY with the service role (scripts/grant-admin.ts or
-- the SQL editor). No policy permits anon/authenticated to write this table.
create table public.user_roles (
  user_id uuid not null references auth.users (id) on delete cascade,
  role public.app_role not null,
  granted_by uuid references auth.users (id) on delete set null,
  granted_at timestamptz not null default now(),
  note text check (note is null or char_length(note) <= 200),
  primary key (user_id, role)
);

alter table public.user_roles enable row level security;
revoke all on public.user_roles from anon;
revoke insert, update, delete, truncate on public.user_roles from authenticated;
grant select on public.user_roles to authenticated;

create or replace function public.has_role(check_role public.app_role)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.user_roles
    where user_id = (select auth.uid()) and role = check_role
  );
$$;

create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select public.has_role('admin'::public.app_role);
$$;

-- Editors manage research content; admins can do everything editors can.
create or replace function public.is_staff()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select public.has_role('admin'::public.app_role) or public.has_role('editor'::public.app_role);
$$;

revoke execute on function public.has_role(public.app_role) from public, anon;
grant execute on function public.has_role(public.app_role) to authenticated, service_role;
grant execute on function public.is_admin() to anon, authenticated, service_role;
grant execute on function public.is_staff() to anon, authenticated, service_role;

create policy "user_roles: user reads own roles" on public.user_roles
  for select to authenticated using (user_id = (select auth.uid()));
create policy "user_roles: admins read all" on public.user_roles
  for select to authenticated using ((select public.is_admin()));

create policy "profiles: admins read all" on public.profiles
  for select to authenticated using ((select public.is_admin()));

-- --------------------------------------------------------------- app config
-- Server-side business settings read by SQL functions (e.g. entitlement rules).
create table public.app_config (
  key text primary key check (key ~ '^[a-z][a-z0-9_]{1,62}$'),
  value jsonb not null,
  description text,
  updated_at timestamptz not null default now(),
  updated_by uuid references auth.users (id) on delete set null
);

create trigger app_config_updated_at before update on public.app_config
  for each row execute function public.set_updated_at();

alter table public.app_config enable row level security;
revoke all on public.app_config from anon;
create policy "app_config: admins read" on public.app_config
  for select to authenticated using ((select public.is_admin()));
create policy "app_config: admins update" on public.app_config
  for update to authenticated using ((select public.is_admin())) with check ((select public.is_admin()));

insert into public.app_config (key, value, description) values
  ('require_livemode_subscriptions', 'true'::jsonb,
   'When true, only live-mode (real provider) subscriptions grant entitlements. Set false ONLY in development databases using the billing simulator.'),
  ('billing_grace_period_days', '3'::jsonb,
   'Days a past_due subscription keeps access after its period end.');

create or replace function public.config_value(config_key text)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select value from public.app_config where key = config_key;
$$;
revoke execute on function public.config_value(text) from public, anon, authenticated;
grant execute on function public.config_value(text) to service_role;

-- ------------------------------------------------------------ feature flags
create table public.feature_flags (
  key text primary key check (key ~ '^[a-z][a-z0-9_]{1,62}$'),
  enabled boolean not null default false,
  description text check (description is null or char_length(description) <= 300),
  updated_at timestamptz not null default now(),
  updated_by uuid references auth.users (id) on delete set null
);

create trigger feature_flags_updated_at before update on public.feature_flags
  for each row execute function public.set_updated_at();

alter table public.feature_flags enable row level security;
create policy "feature_flags: public read" on public.feature_flags
  for select to anon, authenticated using (true);
create policy "feature_flags: admins insert" on public.feature_flags
  for insert to authenticated with check ((select public.is_admin()));
create policy "feature_flags: admins update" on public.feature_flags
  for update to authenticated using ((select public.is_admin())) with check ((select public.is_admin()));
revoke insert, update, delete on public.feature_flags from anon;

insert into public.feature_flags (key, enabled, description) values
  ('ai_assistant', true, 'Show the AI research assistant (requires AI provider configuration).'),
  ('telegram_notifications', true, 'Allow linking Telegram accounts (requires bot configuration).'),
  ('public_signups', true, 'Allow new account registration.'),
  ('maintenance_banner', false, 'Display a maintenance notice site-wide.');

-- --------------------------------------------------------------- audit log
create table public.admin_audit_logs (
  id bigint generated always as identity primary key,
  actor_id uuid references auth.users (id) on delete set null,
  action text not null check (char_length(action) between 1 and 80),
  target_type text check (target_type is null or char_length(target_type) <= 60),
  target_id text check (target_id is null or char_length(target_id) <= 200),
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index admin_audit_logs_created_at_idx on public.admin_audit_logs (created_at desc);
create index admin_audit_logs_actor_idx on public.admin_audit_logs (actor_id, created_at desc);

alter table public.admin_audit_logs enable row level security;
revoke all on public.admin_audit_logs from anon;
revoke update, delete, truncate on public.admin_audit_logs from authenticated;
create policy "audit: admins read" on public.admin_audit_logs
  for select to authenticated using ((select public.is_staff()));
create policy "audit: staff append own entries" on public.admin_audit_logs
  for insert to authenticated with check ((select public.is_staff()) and actor_id = (select auth.uid()));

-- Append-only, even for the service role.
create or replace function public.prevent_audit_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'admin_audit_logs is append-only';
end;
$$;
create trigger admin_audit_logs_immutable
  before update or delete on public.admin_audit_logs
  for each row execute function public.prevent_audit_mutation();
