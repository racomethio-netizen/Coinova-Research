# Binance rate-limit and reconnect loop: investigation and fix

Date: 2026-11-22. Status: fixed in code and tested; 24-hour production stability test
started at deployment (results appended below when complete).

## Symptom

Telegram showed "binance unreachable: RateLimited" followed by "binance reconnected", over
and over (for example 15:05, 15:11, 15:18, 15:25, 15:27, 15:36 UTC on 22 Nov).

## Evidence (production logs, Render service nexus-ai-fra, 22 Nov 12:00-16:10 UTC)

| Time (UTC) | What Binance answered | NEXUS caller |
|---|---|---|
| 12:05:30 | 418, `-1003 Way too much request weight used; IP banned until 12:05:58` | candle poll (5m) |
| 13:00:15 | 418, banned until 13:01:57 | candle poll |
| 15:05:01 | 418, banned until 15:09:57 | candle poll |
| 15:17:22 | 418, banned until 15:24:58 | order-book snapshot |
| 15:27:23 | 418, banned until 15:35:59 | order-book snapshot |

Each "unreachable" message followed one of these bans; each "reconnected" followed its end.

## Root cause

1. **The IP, not NEXUS's own volume, was over Binance's limit.** From its polling
   schedule NEXUS used roughly 10 request weight per minute (an estimate: Binance's usage
   header was not logged before this fix; it is now). Binance allows 6,000 per IP. The bans arrived on the FIRST
   request after quiet periods, with no 429 warnings first. On Render's free plan the
   outgoing IP is shared with other customers; their traffic counts against the same
   per-IP limit. NEXUS cannot see or control that traffic.
2. **NEXUS made bans worse and noisier:**
   * The health monitor pinged Binance (`/api/v3/time`) every 60 s **without checking the
     shared back-off**, so it kept calling during bans. Binance escalates bans for clients
     that keep calling.
   * The WebSocket order books (ccxt.pro diff-depth streams) download a REST order-book
     snapshot on every (re)subscription, outside the back-off.
   * The trader and the 5-minute book snapshots used REST order books although a live
     stream existed.
   * Request-time API calls (account pages, connection test) and the futures transport
     were not covered by the shared back-off.
   * A single refusal produced an alert and the next success a "reconnected" alert, with no
     debounce, hence the flood.
   * The back-off had no circuit breaker: when a ban ended, every waiting caller resumed at
     once.

## Audit: every Binance request

| Caller | Before | After |
|---|---|---|
| Live prices (books) | ccxt.pro diff-depth WS + REST snapshot per (re)subscribe | one combined WS, `@depth20` partial book: no REST at all |
| Candles 1m-1d | REST poll every 30 s (only when a bar was due) | WS `@kline_<tf>` closed bars; REST only for bars still missing 90 s after closing (gaps) |
| Trader books (valuation, exits, paper fills) | REST depth 50 per symbol | stream book from Redis (20 levels, <= 5 s old); REST via the gate only if none |
| Book snapshots to Postgres (5 min) | REST depth 50 | stream book; REST only if none |
| Health check | `/time` ping every 60 s, even during bans | passive: stream freshness + gate state; ping only when both are unknown, through the gate |
| Derivatives (funding, mark/index, OI, ratios) every 5 min | httpx with back-off | gate + 60 s shared cache |
| Carry / taker forward tests (hourly) | httpx with back-off | gate + shared cache (5 s quotes, 120 s klines) |
| Dashboard fallbacks, AI intelligence page | httpx with back-off | gate + 10 s / 30 s shared cache |
| Account pages, connection test (signed) | raw ccxt, no back-off | gated client proxy (production keys) |
| Futures account transport | no back-off | gate (scope futures) |
| Exchange metadata (start-up) | ccxt `load_markets` | through the gate (weight 20) |

## Fix: architecture

* **Central gate** (`packages/common/nexus_common/binance_guard.py`): every REST request
  calls `acquire` (circuit check, half-open probe lock, per-minute weight budget) and
  `report` (status, Binance's `X-MBX-USED-WEIGHT-1M`, statistics, circuit transitions).
  State is in Redis, so all workers and the API share it. Unreadable state = do not call.
* **Circuit breaker**: CLOSED -> OPEN on 429/418 until max(Binance's stated time + 1-5 s
  jitter, exponential back-off 30 s x 2^(n-1) +-20% jitter, capped at 1 h) -> HALF-OPEN:
  exactly one probe request (Redis lock); success closes and resets, failure re-opens for
  longer. Refusals of requests that were already in flight when the circuit opened do not
  escalate the back-off (found by the storm simulation).
* **Budget**: NEXUS uses at most 1,200 of the IP's 6,000 spot weight per minute (480 of
  2,400 futures); at 80% IP usage reported by Binance, everyone pauses until the next
  minute.
* **WebSocket first** (`services/market-data/nexus_market/binance_ws.py`): one combined
  connection for books and klines; heartbeat via Binance's pings (auto-pong); silence >30 s
  = reconnect; reconnect back-off 2 s doubling to 5 min with jitter; at most 20 connection
  attempts per 5 minutes; no connecting while Binance has the IP paused. Verified against
  the real Binance stream (data-stream.binance.vision): books every second and closed 1m
  bars at the minute.
* **Deduplication**: identical REST requests from any worker share one response (Redis
  cache with a short TTL, one in-flight request per process).
* **Health and alerts** (`nexus_market/health.py`): passive health; the trading health
  flag still changes immediately (entries stop at once), but Telegram gets one "down"
  message after 2 minutes down (at most one per 30 minutes) and one "recovered" message
  after 5 stable minutes, with the number of interruptions and total downtime. Every
  transition is still recorded in System events.
* **Visibility**: System page card "Binance connection and request budget" (circuit state,
  pause end, last-hour requests and weight by endpoint and status, IP weight used, stream
  state, recent pauses); every request logged as `binance_request` (endpoint, weight,
  status, ms, IP used weight, failures, circuit, probe).

## Safety

* A request refused by the gate is never sent, so an order can never be left in an unknown
  state by the gate: it is a definitive pre-submission failure.
* No prices = no trading: with no fresh stream book and REST paused, the trader has no book
  and rejects entries ("market data stale or missing").
* Strategies, risk management, database records and paper trading are unchanged. Live
  trading stays off.

## Tests

| Test | What it proves |
|---|---|
| `unit/test_binance_gate.py` (11) | jittered exponential back-off and cap; Binance weights; open -> half-open -> single probe -> closed; failed probe re-opens longer; in-flight success does not close; "banned until" honoured; budget; unreadable Redis = no call; statistics; concurrent identical requests -> one call; gated raw client |
| `unit/test_binance_ws.py` (5) | streams/URL; books and only closed klines dispatched; reconnect after server close, refused connect and silence, with jittered growing back-off that resets after success; no connecting during a ban; connection-attempt budget; a bad message does not drop the connection |
| `integration/test_rate_limit_soak.py` | 6 services hammering a fake Binance that answers 429 for 3 s: at most one in-flight request per service hits the refusal, then only spaced single probes (<= 5 in 3 s instead of ~600 requests), automatic recovery, no burst |
| `integration/test_market_ingest.py` (3 new) | a flapping venue (10 interruptions in an hour) sends exactly one alert and one recovery summary while the trading health flag follows every change; with a live stream no REST call for just-closed bars; passive health reasons; books from the stream, stale stream books ignored |
| `integration/test_paper_trader.py` (1 new) | during a pause nothing is traded; after recovery a repeated signal produces exactly one order |

## 24-hour production stability test

Started at the deployment of this fix (paper trading, live trading off). Checked at about
+1 h, +6 h, +12 h and +24 h from the production logs and the System page:

* count of `binance_circuit_open` events and their causes;
* NEXUS request weight per hour by endpoint vs the IP weight Binance reports;
* WebSocket reconnects and their reasons;
* Telegram alerts sent;
* orders created (must be 0 while no strategy is enabled; never duplicated);
* memory and restarts of the service.

Results: see the section appended below.

## Deployment check (+6 min, 22 Nov 16:37-16:49 UTC)

* Deploy `dep-dc1hj7h42hec73fv1phg` live at 16:37 UTC; migrations 0010 and 0011 applied.
* **Root cause confirmed by Binance's own numbers.** Binance's `X-MBX-USED-WEIGHT-1M`
  header for this server's IP read **3,697 to 5,869 of 6,000** in the first minutes, while
  NEXUS itself sent a handful of requests of weight 2. Other users of the shared IP use most
  of the limit. At 16:37:55 the IP reached 5,869 (98%); the gate paused NEXUS until the next
  minute (16:38:01) by itself, before Binance had to refuse anything.
* WebSocket: connection deferred 3 s because of that pause, then connected at 16:38:02
  (14 streams: books and closed candles for BTC and ETH).
* Start-up: the trader's first attempt hit the pause and restarted 5 s later (by design:
  nothing sent); one notifier restart on a Redis connection timeout.
* No circuit opened (no 429/418) and no Telegram alert in the first 12 minutes.
* Futures data stays paused until the earlier ban ends (~08:54 UTC 23 Nov).
* Memory: ~434 MB steady (limit 512 MB), up from ~290 MB before; a one-time 480 MB peak at
  start-up. Measured locally, the 1-minute candle pipeline adds ~34 MB; the rest is most
  likely the one-time derivation of 120 days of history plus memory the allocator kept.
  Fix committed (bounded derivation windows, memory returned to the OS after large jobs);
  to be deployed if the +1 h checkpoint shows memory rising.

## +1 h checkpoint (22 Nov 17:46 UTC, 70 minutes after the deploy)

* Pauses (`binance_circuit_open`): **0**. WebSocket: connected since 16:38:02, **no
  disconnects**. Telegram alerts: **none**. Crashes/loop errors: none after start-up.
* Binance REST requests since 16:48: a handful per hour (2 `bookTicker` and 6 `klines` at
  17:02-17:03 for the hourly forward tests), all HTTP 200. No candle polling was needed:
  176 closed bars arrived over the WebSocket in the last 70 minutes and were stored.
* IP weight reported by Binance in this hour: 47 to 780 of 6,000 (other users of the IP were
  quieter than at 16:37, when it was 3,700-5,900).
* Memory: flat at 434 MB (no growth). Orders: 0, signals: 0, duplicate client order IDs: 0.
* Found: the 1-minute history is not being filled (4,076 bars instead of ~86,000). The
  stream writes the newest bar first and the REST filler only continues from the newest
  stored bar, so older holes are never revisited. Trading does not use 1m bars; a gap-repair
  fix is committed and will ship with the next deploy (after this test).

## +6 h checkpoint (22 Nov 22:40 UTC; data 18:06-22:40)

The owner asked to deploy the Phase C-E work, so a new release (`9b1fc5d`,
`dep-dc1islfb9u1s738bo4ig`) went live at **18:06 UTC**. The stability test restarted from that
time. At **19:43** the owner changed the Render plan, which restarted the service once more. The
memory limit is still 512 MB and the CPU limit 0.5.

* **One pause, caused by Binance, handled as designed.** At 18:06:14 the first `bookTicker`
  after start-up got **HTTP 418** ("banned until 18:19:59"). The IP's used weight was only 858 of
  6,000 at that moment. A second request that was already in flight also got 418. The circuit
  opened once, until 18:20:01 (Binance's time plus jitter); after that NEXUS sent nothing.
  * At 18:20:03 the WebSocket connected.
  * At 18:20:30 the probe request succeeded and the circuit closed.
  * There were no 429s and no further 418s in the period.
* **Telegram: 2 alerts in total.**
  * 18:08 "down for 121 s" (sent after the 2-minute debounce).
  * 18:25 "stable for 304 s after 1 interruption, 841 s down in total".
  * There was no flapping.
* **WebSocket:** connected at 18:20:03 and again at 19:44:08 (after the plan-change restart),
  with **no disconnects** in between or since.
* **Gap repair works:** between 19:26 and 19:43 it filled the old 1-minute holes in steps of
  up to 3,000 bars. After the 19:43 restart it filled the 4 missing minutes at 19:49.
* **Start-up after the 19:43 restart:** Redis timed out for a moment.
  * The notifier restarted once.
  * The gate *failed closed*: it treated the unreadable state as a 2-minute pause, so pair sync
    used the pairs stored in the database. The trader restarted once.
  * Requests flowed normally from 19:43:54, with IP weight 670-700.
  * Note: the log text for this case says "rate limit / IP ban" although the cause was Redis.
    The behaviour is correct; the wording should be fixed in a later change.
* **Trader restarts during the 18:06-18:20 ban:** the restart delay grew 5 s → 300 s. No request
  was sent while paused. The trader started normally after the ban.
* **Futures:** still paused until 08:54:50 UTC on 23 Nov, from the earlier ban.
  * `derivatives_fetch_failed` is logged every 5 minutes per symbol without calling Binance.
  * The hourly forward-test gaps (`carry_paper_gap`, `forward_taker_gap`) are recorded as gaps,
    not filled with estimates.
* **Memory** (lower than the 434 MB before this release; the bounded derivation works):
  * 18:36-19:36: 385-400 MB.
  * After the 19:43 restart: 395-398 MB, flat through 22:36.
  * CPU: about 1-2% of the 0.5 limit.
* **Errors:** only the start-up items above. No `cycle_failed`, no `loop_error`, no tracebacks.
* **Orders: 0. Duplicate client order IDs: 0.** No strategy is enabled. The 7 strategies A-G
  now carry their real (failed) validation records in production.

## +12 h checkpoint (23 Nov 04:40 UTC; data 22:40-04:40)

The six hours were fully quiet:

* **Binance pauses (circuit opens): 0.** No 429 or 418 responses.
* **WebSocket:** no disconnects, no reconnects and no stale-stream events.
* **Telegram alerts: 0.**
* **Service:** no restarts and no Render events.
* **Errors:** no error-level log lines, no worker crashes, no `cycle_failed` or `loop_error`.
* **Data:** 720 one-minute bars stored (360 per coin = every minute for 6 hours, all from the
  WebSocket). The newest 1m and 5m bars are current.
* **Memory:** 397.7 → 406.5 MB over 6 hours (limit 512 MB). That is +0.6 MB/h until 03:36,
  then +7.7 MB in the last hour. This is worth watching, but it is not a leak pattern yet.
* **CPU:** about 1.1% of the 0.5 CPU limit.
* **Futures:** still in the earlier back-off until 08:54:50 UTC, with no requests sent.
* **Orders: 0. Duplicate client order IDs: 0.**
