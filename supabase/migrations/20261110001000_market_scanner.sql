-- =============================================================================
-- 0016 Market scanner view (Phase 7).
--
-- Latest indicator state per pair and timeframe, taken from the engine's own
-- evaluations so the web shows exactly the values the engine computed.
-- It deliberately exposes ONLY indicator fields: no decision, no reason
-- codes, no levels. Staff-only (shadow) signals therefore cannot be inferred
-- from it. Runs with the view owner's rights (evaluations are staff-only).
-- =============================================================================

create view public.market_scanner as
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
  nullif(e.features ->> 'macd_hist', '')::numeric as macd_hist,
  nullif(e.features ->> 'atr14', '')::numeric as atr14,
  nullif(e.features ->> 'volume_ratio', '')::numeric as volume_ratio,
  e.features ->> 'htf' as htf,
  nullif(e.features ->> 'htf_ema50', '')::numeric as htf_ema50,
  nullif(e.features ->> 'htf_ema200', '')::numeric as htf_ema200
from public.signal_evaluations e
join public.market_pairs p on p.id = e.pair_id and p.enabled
where e.features ? 'ema200' and (e.features ->> 'ema200') <> 'nan' and (e.features ->> 'rsi14') <> 'nan'
order by e.pair_id, e.timeframe, e.as_of desc;

revoke all on public.market_scanner from public;
grant select on public.market_scanner to anon, authenticated;
