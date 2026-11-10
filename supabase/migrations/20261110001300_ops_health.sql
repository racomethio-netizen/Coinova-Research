-- =============================================================================
-- 0019 Operations health (Phase 10): one SQL function lists every problem the
-- on-call admin must know about, and a second one turns them into admin alerts.
--
--   ops_health()        read-only checks; callable by the service role and staff
--   raise_ops_alerts()  service role only; at most one alert per check, admin and hour
--
-- Called every 5 minutes by the watchdog (`check-health` job), which runs on its
-- own Render cron so that a dead engine is still detected. Thresholds are listed
-- in docs/nexus/RUNBOOKS.md together with what to do for each check.
-- Each check returns at most one row, so alerts deduplicate per check.
-- =============================================================================

create or replace function public.ops_health(p_now timestamptz default now())
returns table (check_key text, severity text, detail text)
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  hb record;
  r record;
  failed integer;
  total integer;
  stale text[] := '{}';
begin
  if coalesce(auth.role(), '') <> 'service_role' and not public.is_staff() then
    raise exception 'forbidden' using errcode = '42501';
  end if;

  -- Engine liveness (the worker writes a heartbeat every tick, ~5 s).
  select * into hb from public.engine_heartbeats where component = 'marketdata.worker';
  if not found then
    return query select 'engine.heartbeat', 'critical', 'No engine heartbeat has ever been recorded.';
  else
    if hb.last_seen < p_now - interval '3 minutes' then
      return query select 'engine.heartbeat', 'critical',
        format('Engine heartbeat is %s minutes old.', floor(extract(epoch from p_now - hb.last_seen) / 60));
    end if;
    if nullif(hb.details ->> 'blocked', '') is not null then
      return query select 'marketdata.blocked', 'critical', 'The market-data source blocked the engine (HTTP 418/451). Ingestion is stopped.';
    end if;
  end if;

  -- Web cron jobs driven by the engine.
  select * into hb from public.engine_heartbeats where component = 'web.cron';
  if not found then
    return query select 'webcron.heartbeat', 'warning', 'The engine has never reported web cron runs (WEB_CRON_URL/CRON_SECRET not set?).';
  elsif hb.last_seen < p_now - interval '3 minutes' then
    return query select 'webcron.heartbeat', 'critical',
      format('Web cron status is %s minutes old.', floor(extract(epoch from p_now - hb.last_seen) / 60));
  else
    for r in select e.key, e.value from jsonb_each(coalesce(hb.details -> 'entries', '{}'::jsonb)) e
             where coalesce((e.value ->> 'consecutive_failures')::integer, 0) >= 3 loop
      return query select 'webcron.failing', 'warning',
        format('Web cron "%s" failed %s times in a row (%s).', r.key, r.value ->> 'consecutive_failures', r.value ->> 'last_status');
    end loop;
  end if;

  -- Ingestion freshness per enabled pair and timeframe.
  for r in
    select p.symbol, tf, (select max(c.close_time) from public.market_candles c
                          where c.pair_id = p.id and c.timeframe = tf and c.is_final) as last_close
    from public.market_pairs p
    join public.market_sources s on s.id = p.source_id and s.enabled
    cross join lateral unnest(p.timeframes) tf
    where p.enabled
  loop
    if r.last_close is null then
      stale := stale || format('%s %s (no candles)', r.symbol, r.tf);
    elsif r.last_close < p_now - make_interval(secs => 2 * public.timeframe_seconds(r.tf) + 300) then
      stale := stale || format('%s %s (%s ago)', r.symbol, r.tf, date_trunc('minute', p_now - r.last_close));
    end if;
  end loop;
  if cardinality(stale) > 0 then
    return query select 'marketdata.stale', 'warning',
      left(format('%s pair/timeframe(s) are not up to date: %s.', cardinality(stale), array_to_string(stale, ', ')), 1900);
  end if;

  select count(*) into failed from public.candle_gaps where status = 'open' and detected_at < p_now - interval '1 hour';
  if failed > 0 then
    return query select 'marketdata.gaps', 'warning', format('%s candle gap(s) open for more than an hour.', failed);
  end if;

  -- Web job error rate over the last hour, and stuck runs.
  select count(*) filter (where status = 'failed'), count(*) into failed, total
  from public.background_jobs where started_at > p_now - interval '1 hour';
  if failed >= 3 or (total >= 5 and failed::numeric / total > 0.2) then
    return query select 'jobs.failing', 'warning', format('%s of %s background jobs failed in the last hour.', failed, total);
  end if;
  select count(*) into failed from public.background_jobs where status = 'running' and locked_until < p_now - interval '5 minutes';
  if failed > 0 then
    return query select 'jobs.stuck', 'warning', format('%s background job(s) still marked running after their lock expired.', failed);
  end if;

  -- Notification delivery failures.
  select count(*) into failed from public.notification_deliveries where status = 'failed' and updated_at > p_now - interval '1 hour';
  if failed >= 10 then
    return query select 'notifications.failing', 'warning', format('%s notification deliveries failed in the last hour.', failed);
  end if;

  -- Engine queue failures (backfills, gap repair) in the last day.
  select count(*) into failed from public.engine_jobs where status = 'failed' and coalesce(finished_at, created_at) > p_now - interval '1 day';
  if failed > 0 then
    return query select 'engine.jobs_failed', 'warning', format('%s engine job(s) failed in the last 24 hours.', failed);
  end if;

  -- Nightly ledger export (R-02, R-17).
  select * into hb from public.engine_heartbeats where component = 'ledger.export';
  if not found then
    return query select 'ledger.export', 'warning', 'No ledger export has been recorded (LEDGER_EXPORT_DIR not set?).';
  else
    if hb.last_seen < p_now - interval '26 hours' then
      return query select 'ledger.export', 'warning',
        format('Last ledger export was %s hours ago.', floor(extract(epoch from p_now - hb.last_seen) / 3600));
    end if;
    if (hb.details ->> 'intact') is distinct from 'true' then
      return query select 'ledger.integrity', 'critical', 'The last ledger export found a broken hash chain or a continuity problem. Freeze publishing and investigate.';
    end if;
  end if;
  return;
end;
$$;
revoke execute on function public.ops_health(timestamptz) from public, anon;
grant execute on function public.ops_health(timestamptz) to authenticated, service_role;

-- One in-app + one email notification per issue, admin and hour. Returns a summary.
create or replace function public.raise_ops_alerts(p_now timestamptz default now())
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  i record;
  a record;
  nid uuid;
  issues integer := 0;
  critical integer := 0;
  notified integer := 0;
  hour_key text := to_char(date_trunc('hour', p_now) at time zone 'UTC', 'YYYY-MM-DD"T"HH24');
begin
  if coalesce(auth.role(), '') <> 'service_role' then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  for i in select * from public.ops_health(p_now) loop
    issues := issues + 1;
    if i.severity = 'critical' then critical := critical + 1; end if;
    for a in select distinct ur.user_id from public.user_roles ur where ur.role = 'admin' loop
      insert into public.notifications (user_id, kind, title, body, data, dedupe_key)
      values (a.user_id, 'system', left(format('[%s] Operations: %s', upper(i.severity), i.check_key), 200), left(i.detail, 2000),
              jsonb_build_object('check', i.check_key, 'severity', i.severity, 'runbook', 'docs/nexus/RUNBOOKS.md'),
              format('ops:%s:%s:%s', i.check_key, hour_key, a.user_id))
      on conflict (dedupe_key) do nothing
      returning id into nid;
      if nid is not null then
        notified := notified + 1;
        insert into public.notification_deliveries (notification_id, user_id, channel, status, sent_at, attempts)
        values (nid, a.user_id, 'in_app', 'sent', p_now, 1);
        insert into public.notification_deliveries (notification_id, user_id, channel) values (nid, a.user_id, 'email');
      end if;
    end loop;
  end loop;
  return jsonb_build_object('issues', issues, 'critical', critical, 'notified', notified);
end;
$$;
revoke execute on function public.raise_ops_alerts(timestamptz) from public, anon, authenticated;
grant execute on function public.raise_ops_alerts(timestamptz) to service_role;
