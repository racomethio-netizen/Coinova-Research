-- =============================================================================
-- 0017 Mobile push: device tokens and the `push` notification channel (Phase 8).
-- Tokens are Expo push tokens (not secrets, but personal data): users manage
-- only their own; the service role reads them for delivery and revokes tokens
-- that Expo reports as no longer registered.
-- =============================================================================

create table public.device_push_tokens (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  token text not null unique check (token ~ '^Expo(nent)?PushToken\[[A-Za-z0-9_-]{10,200}\]$'),
  platform text not null check (platform in ('ios', 'android')),
  created_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  revoked_at timestamptz
);
create index device_push_tokens_user_idx on public.device_push_tokens (user_id) where revoked_at is null;

alter table public.device_push_tokens enable row level security;
revoke all on public.device_push_tokens from anon;
revoke update, truncate on public.device_push_tokens from authenticated;
create policy "devices: own read" on public.device_push_tokens for select to authenticated using (user_id = (select auth.uid()));
create policy "devices: own insert" on public.device_push_tokens for insert to authenticated with check (user_id = (select auth.uid()));
create policy "devices: own delete" on public.device_push_tokens for delete to authenticated using (user_id = (select auth.uid()));

-- Allow `push` wherever channels are listed (constraint names looked up, not assumed).
do $$
declare c text;
begin
  select conname into c from pg_constraint where conrelid = 'public.alerts'::regclass and contype = 'c' and pg_get_constraintdef(oid) like '%channels%';
  execute format('alter table public.alerts drop constraint %I', c);
  select conname into c from pg_constraint where conrelid = 'public.notification_deliveries'::regclass and contype = 'c' and pg_get_constraintdef(oid) like '%channel%';
  execute format('alter table public.notification_deliveries drop constraint %I', c);
end;
$$;
alter table public.alerts add constraint alerts_channels_check
  check (cardinality(channels) between 1 and 4 and channels <@ array['in_app', 'email', 'telegram', 'push']::text[]);
alter table public.notification_deliveries add constraint notification_deliveries_channel_check
  check (channel in ('in_app', 'email', 'telegram', 'push'));
