-- =============================================================================
-- 0009 Admin MFA: staff and admin privileges require an MFA-verified session.
--
-- Supabase Auth issues `aal = 'aal2'` in the JWT after a TOTP challenge. From
-- this migration on, `is_admin()` and `is_staff()` are true only for aal2
-- sessions, so every policy and function that relies on them (admin reads,
-- staff writes, audit log, scoring publication) requires MFA without being
-- rewritten. Role *membership* is unchanged and still visible to its owner
-- through the `user_roles` "read own" policy, so the app can send privileged
-- users to MFA enrollment.
-- =============================================================================

create or replace function public.is_aal2()
returns boolean
language sql
stable
set search_path = ''
as $$
  select coalesce((select auth.jwt()) ->> 'aal', 'aal1') = 'aal2';
$$;
grant execute on function public.is_aal2() to anon, authenticated, service_role;

create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select public.has_role('admin'::public.app_role) and public.is_aal2();
$$;

create or replace function public.is_staff()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select (public.has_role('admin'::public.app_role) or public.has_role('editor'::public.app_role)) and public.is_aal2();
$$;
