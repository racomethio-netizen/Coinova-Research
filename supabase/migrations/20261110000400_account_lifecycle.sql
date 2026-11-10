-- =============================================================================
-- 0010 Account lifecycle: data export, deletion with retention, deletion audit.
--
-- Deletion policy (docs/nexus/SECURITY_RISKS.md R-16):
--   * personal data and user content are deleted (FK cascades, unchanged);
--   * payment records are financial records and are RETAINED, pseudonymized
--     (user_id set to NULL; they hold no name, email, or card data);
--   * audit entries the user wrote as staff are kept, with actor_id nulled;
--   * every deletion is recorded in the audit log without identifying data.
-- =============================================================================

-- --------------------------------------------------- audit log pseudonymization
-- The log stays append-only, with one exception: the FK `on delete set null`
-- for a deleted actor. Previously that update was rejected, which made it
-- impossible to delete any account that had ever written an audit entry.
create or replace function public.prevent_audit_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'UPDATE'
     and old.actor_id is not null and new.actor_id is null
     and (new.id, new.action, new.target_type, new.target_id, new.details, new.created_at)
         is not distinct from (old.id, old.action, old.target_type, old.target_id, old.details, old.created_at) then
    return new;
  end if;
  raise exception 'admin_audit_logs is append-only';
end;
$$;

-- --------------------------------------------------- retain payment records
alter table public.billing_payments alter column user_id drop not null;
alter table public.billing_payments drop constraint billing_payments_user_id_fkey;
alter table public.billing_payments
  add constraint billing_payments_user_id_fkey foreign key (user_id) references auth.users (id) on delete set null;

-- --------------------------------------------------- deletion audit
create or replace function public.audit_account_deletion()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.admin_audit_logs (actor_id, action, target_type, details)
  values (null, 'account.deleted', 'user', jsonb_build_object(
    'retained_payment_records', (select count(*) from public.billing_payments where user_id = old.id)
  ));
  return old;
end;
$$;
revoke execute on function public.audit_account_deletion() from public, anon, authenticated;

create trigger on_auth_user_deleted
  before delete on auth.users
  for each row execute function public.audit_account_deletion();

-- --------------------------------------------------- data export
-- SECURITY INVOKER: every read goes through the caller's RLS policies, so the
-- export can only ever contain rows the caller may already read.
create or replace function public.export_my_data()
returns jsonb
language plpgsql
stable
security invoker
set search_path = ''
as $$
declare
  uid uuid := auth.uid();
begin
  if uid is null then
    raise exception 'unauthorized' using errcode = '42501';
  end if;
  return jsonb_build_object(
    'format', 'nexus-account-export/1',
    'generated_at', now(),
    'user_id', uid,
    'profile', (select to_jsonb(p) from public.profiles p where p.id = uid),
    'roles', (select coalesce(jsonb_agg(to_jsonb(r)), '[]') from public.user_roles r where r.user_id = uid),
    'subscriptions', (select coalesce(jsonb_agg(to_jsonb(s) order by s.created_at), '[]') from public.subscriptions s where s.user_id = uid),
    'payments', (select coalesce(jsonb_agg(to_jsonb(b) order by b.occurred_at), '[]') from public.billing_payments b where b.user_id = uid),
    'checkout_sessions', (select coalesce(jsonb_agg(to_jsonb(c) order by c.created_at), '[]') from public.checkout_sessions c where c.user_id = uid),
    'portfolios', (select coalesce(jsonb_agg(to_jsonb(x) order by x.created_at), '[]') from public.portfolios x where x.user_id = uid),
    'portfolio_transactions', (select coalesce(jsonb_agg(to_jsonb(t) order by t.occurred_at), '[]') from public.portfolio_transactions t where t.user_id = uid),
    'watchlists', (select coalesce(jsonb_agg(to_jsonb(w) order by w.created_at), '[]') from public.watchlists w where w.user_id = uid),
    'watchlist_items', (select coalesce(jsonb_agg(to_jsonb(i)), '[]') from public.watchlist_items i where i.user_id = uid),
    'alerts', (select coalesce(jsonb_agg(to_jsonb(a) order by a.created_at), '[]') from public.alerts a where a.user_id = uid),
    'notifications', (select coalesce(jsonb_agg(to_jsonb(n) order by n.created_at), '[]') from public.notifications n where n.user_id = uid),
    'telegram', (select to_jsonb(g) from public.telegram_accounts g where g.user_id = uid),
    'ai_usage', (select coalesce(jsonb_agg(to_jsonb(u) order by u.created_at), '[]') from public.ai_usage_records u where u.user_id = uid)
  );
end;
$$;
revoke execute on function public.export_my_data() from public, anon;
grant execute on function public.export_my_data() to authenticated;
