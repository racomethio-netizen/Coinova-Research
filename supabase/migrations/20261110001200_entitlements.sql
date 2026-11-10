-- =============================================================================
-- 0018 Entitlements (Phase 9): every feature a plan lists is enforced in SQL.
--
--   analytics.advanced  market_scanner hides advanced indicators below Pro
--   signals.history     can_view_signal (0013)
--   signals.alerts      opt-in notifications when a public signal is published
--   performance.reports CSV export route (API) + this file's notes
--   models.analytics    validation reports readable only with the feature
--   backtesting         request_backtest (0014), quota now per tier
--   strategies.custom   custom backtest parameters require the feature
-- Removed (not real entitlements / not built): charts.basic, research.advanced_tools.
-- =============================================================================

delete from public.plan_features where feature_key in ('charts.basic', 'research.advanced_tools');

-- ------------------------------------------------------------ per-tier backtest quota
update public.membership_plans
   set limits = limits || jsonb_build_object('backtestsPerDay', case when tier = 'premium' or tier = 'founding' then 10 else 0 end);

create or replace function public.request_backtest(p_pair_id bigint, p_timeframe text, p_start timestamptz, p_end timestamptz, p_params jsonb default '{}'::jsonb)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  uid uuid := auth.uid();
  lim integer;
  rid uuid;
begin
  if uid is null then
    raise exception 'unauthorized' using errcode = '42501';
  end if;
  if not public.has_feature('backtesting') then
    raise exception 'feature_required:backtesting' using errcode = '42501';
  end if;
  if coalesce(p_params, '{}'::jsonb) <> '{}'::jsonb and not public.has_feature('strategies.custom') then
    raise exception 'feature_required:strategies.custom' using errcode = '42501';
  end if;
  if not exists (select 1 from public.market_pairs where id = p_pair_id and enabled and p_timeframe = any (timeframes)) then
    raise exception 'unknown pair or timeframe' using errcode = '22023';
  end if;
  if p_end > now() then
    raise exception 'period must end in the past' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(hashtext('backtest:' || uid::text));
  lim := public.tier_limit_for(uid, 'backtestsPerDay');
  if (select count(*) from public.backtest_runs where requested_by = uid and created_at > now() - interval '1 day') >= lim then
    raise exception 'limit_reached:backtestsPerDay' using errcode = 'P0001', hint = format('Daily limit is %s.', lim);
  end if;
  if exists (select 1 from public.backtest_runs where requested_by = uid and status in ('queued', 'running')) then
    raise exception 'a backtest is already queued or running' using errcode = 'P0001';
  end if;
  insert into public.backtest_runs (requested_by, pair_id, timeframe, params, period_start, period_end)
  values (uid, p_pair_id, p_timeframe, coalesce(p_params, '{}'::jsonb), p_start, p_end)
  returning id into rid;
  return rid;
end;
$$;

-- ------------------------------------------------------------ models.analytics
drop policy "validation_reports: public read for approved models" on public.validation_reports;
create policy "validation_reports: model analytics for approved models" on public.validation_reports for select to authenticated
  using (
    (select public.has_feature('models.analytics'))
    and exists (select 1 from public.model_versions m where m.validation_report_id = validation_reports.id and m.status in ('approved', 'retired'))
  );

-- ------------------------------------------------------------ analytics.advanced (scanner)
-- Free: price, trend EMAs and RSI. Pro+: MACD, ATR, volume and higher-timeframe trend.
create or replace view public.market_scanner as
select distinct on (e.pair_id, e.timeframe)
  e.pair_id,
  p.symbol,
  e.timeframe,
  e.as_of,
  nullif(e.features ->> 'close', '')::numeric as close,
  nullif(e.features ->> 'ema20', '')::numeric as ema20,
  nullif(e.features ->> 'ema50', '')::numeric as ema50,
  nullif(e.features ->> 'ema200', '')::numeric as ema200,
  nullif(e.features ->> 'rsi14', '')::numeric as rsi14,
  case when public.has_feature('analytics.advanced') then nullif(e.features ->> 'macd_hist', '')::numeric end as macd_hist,
  case when public.has_feature('analytics.advanced') then nullif(e.features ->> 'atr14', '')::numeric end as atr14,
  case when public.has_feature('analytics.advanced') then nullif(e.features ->> 'volume_ratio', '')::numeric end as volume_ratio,
  case when public.has_feature('analytics.advanced') then e.features ->> 'htf' end as htf,
  case when public.has_feature('analytics.advanced') then nullif(e.features ->> 'htf_ema50', '')::numeric end as htf_ema50,
  case when public.has_feature('analytics.advanced') then nullif(e.features ->> 'htf_ema200', '')::numeric end as htf_ema200
from public.signal_evaluations e
join public.market_pairs p on p.id = e.pair_id and p.enabled
where e.features ? 'ema200' and (e.features ->> 'ema200') <> 'nan' and (e.features ->> 'rsi14') <> 'nan'
order by e.pair_id, e.timeframe, e.as_of desc;

-- ------------------------------------------------------------ signals.alerts
alter table public.profiles add column signal_notifications boolean not null default false;
grant update (signal_notifications) on public.profiles to authenticated;

do $$
declare c text;
begin
  select conname into c from pg_constraint where conrelid = 'public.notifications'::regclass and contype = 'c' and pg_get_constraintdef(oid) like '%kind%';
  execute format('alter table public.notifications drop constraint %I', c);
end;
$$;
alter table public.notifications add constraint notifications_kind_check check (kind in ('alert', 'system', 'billing', 'signal'));

-- When a PUBLIC signal is published, notify users who opted in and whose plan includes
-- signals.alerts: one in-app notification (deduplicated per user and signal), plus a push
-- delivery when the user has a registered device. Shadow/beta signals never notify.
create or replace function public.notify_signal_subscribers()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  sym text;
  r record;
  nid uuid;
begin
  if new.visibility <> 'public' then
    return new;
  end if;
  select symbol into sym from public.market_pairs where id = new.pair_id;
  for r in
    select p.id as user_id from public.profiles p
    where p.signal_notifications and public.has_feature_for(p.id, 'signals.alerts')
  loop
    insert into public.notifications (user_id, kind, title, body, data, dedupe_key)
    values (
      r.user_id, 'signal',
      format('%s %s · %s', new.direction, sym, new.timeframe),
      format('New research signal: entry %s–%s, stop %s, target %s. Not financial advice.',
             trim(to_char(new.entry_low, 'FM999999999990.99999999')), trim(to_char(new.entry_high, 'FM999999999990.99999999')),
             trim(to_char(new.stop_loss, 'FM999999999990.99999999')), trim(to_char(new.take_profits[1], 'FM999999999990.99999999'))),
      jsonb_build_object('signalId', new.id),
      format('signal:%s:%s', new.id, r.user_id)
    )
    on conflict (dedupe_key) do nothing
    returning id into nid;
    if nid is not null then
      insert into public.notification_deliveries (notification_id, user_id, channel, status, sent_at, attempts)
      values (nid, r.user_id, 'in_app', 'sent', now(), 1);
      if exists (select 1 from public.device_push_tokens d where d.user_id = r.user_id and d.revoked_at is null) then
        insert into public.notification_deliveries (notification_id, user_id, channel) values (nid, r.user_id, 'push');
      end if;
    end if;
  end loop;
  return new;
end;
$$;
revoke execute on function public.notify_signal_subscribers() from public, anon, authenticated;

create trigger signals_notify_subscribers after insert on public.signals
  for each row execute function public.notify_signal_subscribers();
