# NEXUS test report

How to run: `scripts/test.sh` with `NEXUS_TEST_DATABASE_URL` and `NEXUS_TEST_REDIS_URL`
(Postgres and Redis test databases; integration tests are skipped without them). Browser
tests: `scripts/e2e.sh` (see the script header).

## 2026-11-22: Phase C (technical analysis, multi-timeframe)

| Suite | Result |
|---|---|
| Lint, types (136 files) | pass |
| Unit + integration | **693 passed**, 0 failed |
| Browser e2e | 13 passed, 2 failed (the same pre-existing "validation results" failure) |
| Real Binance candles (BTCUSDT 5m-1d, public data API) | snapshot and MTF states computed; values plausible (RSI, ADX, pivots, structure) |

New tests (38): every new indicator and the per-timeframe state are prefix-invariant (no
look-ahead, 22 indicators x 3 cut points); DMI, ROC, momentum, OBV, relative volume,
historical volatility, CCI, MFI, Stochastic RSI, Keltner and Ichimoku match independent
loop-based reference implementations; Supertrend flips on a reversal; candle shares,
pivots and wick rejection by hand; equal highs on the confirmation bar; RSI divergence;
breakout retest and failure; multi-timeframe alignment uses only closed bars of the other
timeframe (exact bar checked); the MTF frame is prefix-invariant and scores stay in [-1, 1];
conflict flag; JSON snapshots with missing history shown as missing; volatility
annualized by bar length; API routes on stored candles.

Bugs found and fixed: MFI produced its first value one bar early (counted the first bar,
which has no direction); historical volatility was annualized as if bars were days (found
on real BTC data: 15% shown instead of ~74% for 1h bars).

## 2026-11-22: Binance rate-limit fix (docs/BINANCE_RATE_LIMIT_REPORT.md)

| Suite | Result |
|---|---|
| Lint, types (133 files), `alembic check` | pass |
| Unit + integration | **654 passed**, 0 failed (31 deselected: real-network exchange tests) |
| Browser e2e | 13 passed, 2 failed (the same pre-existing "validation results" failure) |
| Real Binance WebSocket (data-stream.binance.vision, from this environment) | connected; ~3 messages/s; real BTC/ETH books; closed 1m bars delivered at the minute |

New tests (22): gate circuit breaker, probe, back-off and jitter, budget, cache, statistics,
gated raw clients (11); WebSocket stream reconnects, stale detection, ban-aware
connecting, attempt budget, message isolation (5); 429 storm simulation with 6 services
(1); flapping alert debounce, stream-first candles, passive health, stream books, start-up
during a pause (4); no trading during a pause and no duplicate order after recovery (1).

Bugs found by these tests and fixed before deployment: concurrent refusals of requests
already in flight escalated the back-off six steps at once (now one failure per circuit
opening, decided atomically in Redis).

Pending: the 24-hour production stability test (results appended to the report).

## 2026-11-22: Phase B (market data: timeframes, data quality)

| Suite | Result |
|---|---|
| Lint, types (132 files), `alembic check` (migration 0011) | pass |
| Unit + integration | **631 passed**, 0 failed (31 deselected: real-network exchange tests) |
| Browser e2e | 13 passed, 2 failed (the same pre-existing "validation results" failure) |

New tests (7): derived bars only from complete closed groups (a missing 1h bar removes
its 2h/6h/12h bucket, a still-forming bucket is never written, re-running writes no
duplicates); 3m bars are epoch-aligned like Binance; the data-quality report counts
missing bars, the largest gap, coverage and staleness (no data = stale); retention
removes only old 1m/3m bars; 1m polling fetches a few pages per poll (no startup burst)
and only for traded pairs; every timeframe has a duration and matches the database
constraint. The data-quality route requires sign-in.

## 2026-11-22: deep upgrade Phase A (A3 modes, A4 database role, A5 weight budget, A6 restarts)

| Suite | Result |
|---|---|
| Lint (ruff), types (mypy strict, 131 files), `alembic check` | pass |
| Unit + integration + security + exchange fakes | **624 passed**, 0 failed (28 deselected: real-key exchange tests) |
| Dashboard typecheck | pass |
| Browser e2e (desktop + 360 px) | 13 passed, 2 failed: the same pre-existing "validation results are shown honestly" failure (no AI-model rows in the e2e database) |

New tests this step (18):

* A6 restarts (`integration/test_execution.py`): crash after the exchange accepted and
  filled an order but before the answer was stored (recovered by client order ID, fills
  applied once, never resubmitted); crash before anything was sent (rejected after the
  settle delay, funds released, the pair unblocked); failure in the middle of applying a
  fill (nothing partial written; the next refresh applies it once). End to end in the
  trader: `integration/test_paper_trader.py::test_crash_between_submit_and_record_in_trader_cycle_recovers`.
* A3 modes: `unit/test_opmode.py` (which trader may place orders; fail-closed parsing);
  trader tests for SIGNALS_ONLY (verdict recorded, no order) and ANALYSIS_ONLY (no entry
  evaluation; a non-owner or unrecognised value cannot enable trading); API test (lowering
  is easy; LIVE_AUTONOMOUS needs the phrase, 2FA and every gate, and is refused today).
* A3 live ledger: funding is bounded by the latest exchange report, refuses stale reports,
  paper portfolios and non-positive amounts; the API refuses without a live portfolio.
* A5 weight budget (`unit/test_binance_guard.py`): header parsing, warning at 50%, pause
  until the next minute at 80% (spot 6000, futures 2400), weight recorded from httpx
  responses and from the ccxt adapter hook.
* A4 database role (`integration/test_db_roles.py`): the grant script is idempotent, keeps
  the audit log and research ledgers append-only, and `nexus_app` can write RLS-protected
  tables after migration 0010.

Bugs found and fixed by these tests:

* Orders interrupted between saving and the exchange's answer stayed `pending_submit`
  forever (blocking the pair and locking funds). Now recovered every cycle.
* Row-level security without policies locked the least-privilege role out of the research
  ledgers (and would lock it out of every table on Supabase). Migration 0010 adds policies
  for `nexus_app` only.

## 2026-11-22: deep upgrade Phase A (A1 balance reconciliation, A2 risk states)

| Suite | Result |
|---|---|
| Lint (ruff), types (mypy strict, 130 files) | pass |
| Unit + integration + security + exchange fakes | **606 passed**, 0 failed (28 deselected: live-exchange tests that need real keys) |
| Dashboard typecheck (tsc) | pass |
| Browser e2e (desktop + 360 px) | 11 passed, 2 failed: "validation results are shown honestly" on both viewports. Pre-existing: the e2e database has no AI-model evaluation rows, so the page correctly shows nothing to assert on. Not caused by this phase |

New tests this phase (30):

* `tests/unit/test_risk_state.py`: every trigger maps to its state; strictest wins; reduced
  risk multiplier; owner floor only adds restrictions; entry/cancel semantics.
* `tests/unit/test_reconcile.py`: equal, short, missing, extra funds, tolerance, blocking
  statuses.
* `tests/integration/test_paper_trader.py`:
  * stale candles put the state in NO_NEW_TRADES and fresh candles restore NORMAL, with
    exactly one audit row per change;
  * an owner floor blocks signals, survives a trader restart without a duplicate
    transition, and a floor written by a non-owner is ignored;
  * a reconciliation mismatch gives CLOSE_ONLY and cancels resting entries;
  * live reconciliation against a fake account: OK (with unmanaged funds), throttled,
    MISMATCH, fetch failure, Binance ban (no call made), ledger never edited, one audit row
    per status change;
  * paper reconciliation is not applicable.
* `tests/integration/test_api_security.py`: `/risk/state` requires sign-in; the floor can
  be raised without 2FA, relaxing live needs 2FA, EMERGENCY_STOP is refused as a floor, a
  viewer cannot change it.
* `e2e`: the Risk page shows the risk state, the balance check and the minimum-state
  control.

Changed test infrastructure: the order-lifecycle fixture marks candle data fresh (the
freshness rule has its own test), and `scripts/e2e.sh` appends a kill-switch clear record
at the start of each run (the switch is durable, so a previous run's engage was restored).

Not tested here: real Binance balance calls (the account API is banned on the shared IP
until 23 Nov 06:38 UTC, and live trading is off).
