# NEXUS test report

How to run: `scripts/test.sh` with `NEXUS_TEST_DATABASE_URL` and `NEXUS_TEST_REDIS_URL`
(Postgres and Redis test databases; integration tests are skipped without them). Browser
tests: `scripts/e2e.sh` (see the script header).

## 2026-11-23: crypto trends strategy (research, forward paper test)

* `tests/unit/test_crypto_trends.py` checks the strategy rules on synthetic prices.
  * Entry happens at the n-day high. The stop starts at the channel midpoint and never
    moves down.
  * There is no look-ahead: the weights of a shorter history equal the full history's.
  * Volatility sizing is capped at 100% (spot).
  * Trades fill at the next day's open, and costs reduce equity.
  * The 20% threshold reduces the number of trades.
  * The metrics are checked.
* The simulator agrees with an independent vectorised computation of the paper's formula:
  Sharpe 1.118 against 1.119.
* `tests/unit/test_trend_paper.py` checks the replay.
  * Nothing is recorded before START.
  * The replay is deterministic.
  * More data never changes days already recorded.
  * The review checks and decision are tested.
* `tests/integration/test_paper_trader.py::test_trend_paper_...` (PostgreSQL):
  * days are recorded once;
  * a data revision is flagged as a divergence and never rewritten;
  * a refused download is recorded as a gap.
* The live fetch was checked against Binance: 999 complete closed days per coin.
* Migration 0015 was upgraded, checked with `alembic check`, downgraded and upgraded again.
* Full run: `scripts/test.sh`, 806 passed. e2e: 15 passed, including the new
  "Trend strategy test" page.

## 2026-11-23: Binance-only static-IP proxy support

* `tests/unit/test_binance_proxy.py` uses a local recording proxy (`tests/proxyserver.py`)
  that, like QuotaGuard, requires Basic authentication.
  * Real requests went through the proxy from httpx, a ccxt Binance client, an aiohttp
    WebSocket, the `websockets` library, and NEXUS's own spot-stream connector.
  * A wrong password gets HTTP 407 and is never sent around the proxy.
  * Without the variable, nothing changes.
  * The QuotaGuard variable name is supported.
  * The log and status show host:port only, never the password.
  * A malformed URL fails loudly.
  * Only Binance clients get the proxy; the Kraken client does not.
* Also fixed: the spot stream used aiohttp's deprecated `receive_timeout` argument; it now
  uses `ClientWSTimeout`.
* Full run: `scripts/test.sh`, 795 passed.

## 2026-11-23: post-deploy robustness fixes

* **Trader start-up** (`tests/unit/test_trader_startup.py`).
  * While Binance is paused or unreachable, the trader waits for the pause to end (or
    retries) and then loads market rules. It no longer crashes and restarts.
  * A stop interrupts the wait at once.
  * Errors that are not exchange errors still raise.
  * Resources are released if start-up stops or fails. Before this, each crash leaked a
    Redis connection, plus a database connection in live mode.
* **Binance gate.** When the rate-limit state in Redis cannot be read, the message now
  says "rate-limit state unreadable" instead of "rate limit / IP ban". Calls are still
  refused (fail closed).
* **e2e.**
  * `scripts/e2e.sh` adds clearly labelled fixtures ("e2e fixture: not a real result"):
    one failed strategy validation and one rejected model.
  * It refuses any database whose name does not contain "e2e", so it can never write to
    production.
  * Result: **15 passed, 0 failed**. The 2 long-standing failures were missing fixture
    data, not app bugs.
* **Full run:** `scripts/test.sh`, 788 passed.

## 2026-11-23: Binance Demo Trading removed (owner decision)

* Removed:
  * the demo/testnet order-test endpoint and `ordertest.py`, with their tests
    (`tests/unit/test_ordertest.py`, the API order-test test);
  * the demo environment for new keys;
  * the demo hosts from the reachability check;
  * the demo option and order-test buttons on the Binance Connection page.
* New tests:
  * adding a demo key is refused (HTTP 422);
  * both probes refuse a demo environment, and the futures transport rejects it;
  * the order-test endpoint returns 404.
* The repository-hygiene test now allows no `set_leverage` caller outside the futures
  adapter.
* Full run: `scripts/test.sh`, 785 passed (789 before: the removed order tests).
* e2e: 13 passed, plus the same 2 known failures.

## 2026-11-23: Phase H (decision log)

* `tests/unit/test_decision_log.py` covers what gets recorded and when.
  * Strategy verdicts are compacted.
  * Small input changes do not create a new row; a change to the decision, blockers, risk
    state or setups does.
  * Write rules: never twice for one bar, never for an older bar, hourly when unchanged.
  * The snapshot ID format is fixed.
  * The rejection reason is the first blocker, or the real reason, never the generic
    "SHORT not available" note.
  * A pair without candles writes no row.
* `tests/integration/test_decision_log.py` runs on PostgreSQL.
  * Change and heartbeat writes work, pairs are independent, and writes are idempotent.
  * The trigger refuses UPDATE, and DELETE of a recent row.
  * Pruning removes only rows older than 180 days.
* `/api/v1/decision/log` was added to the "every route requires sign-in" and "works when
  signed in" tests.
* Smoke run on the local copy of real Binance history:
  * one NO_TRADE per pair, with snapshot `binance:BTC/USDT:15m@2026-11-14T09:00:00+00:00`;
  * reason "risk state unknown" (no risk state is cached locally);
  * all 16 strategies "failed out-of-sample validation";
  * about 2.4 KB per row.
* Migration 0014 was upgraded, checked with `alembic check`, downgraded and upgraded again.
* **Fixed a timing flake** in `test_operating_mode_easy_to_lower_...`. The test reused a
  two-factor code when a 30 s boundary fell between two requests, and the code was
  correctly refused as already used. It now always takes an unused step.
* **Full run:** `scripts/test.sh`, 789 passed.
* **Browser (e2e):** 13 passed, including the new Decision log page on desktop and at
  360 px. The same 2 failures as in earlier phases remain: "validation results are shown
  honestly" needs validation rows in the e2e database. 1 skipped by design.

## 2026-11-23: Phase G1 (backtesting: slippage model, reports, parameter stability)

`tests/unit/test_backtest_g1.py`:
* **Square-root impact.** Four times the size gives twice the impact. The rate is capped,
  and missing history pays the cap, never zero.
* **No look-ahead in liquidity inputs.** A day's volume and volatility are used only after
  that day ends. Changing future volume does not change past inputs.
* **Default behaviour unchanged.** The engine records the slippage cost of both legs and the
  initial risk (R). All 11 existing backtest tests pass without changes.
* **New metric fields.** Average R and slippage match the trade's own values.
* **Breakdowns.** Group counts are correct, small groups are flagged, and the hour blocks
  are right.
* **Stability measures.**
  * One-step neighbours are found correctly.
  * A true, persistent ranking gives PBO 0, rank correlation above 0.8, and the same set
    chosen every time.
  * Pure noise gives a low correlation and a changing choice.
  * The stability report's out-of-sample trades equal `walk_forward()`'s exactly (over 50
    trades).

`ruff` and `mypy --strict` are clean on `research/backtesting`. Full run `scripts/test.sh`: 782 passed.

## 2026-11-23: Phase F1 (post-only and stop-limit orders)

* `tests/unit/test_order_types.py` covers four areas.
  * **Request rules.** A stop-limit order needs a stop price. A stop price is only valid on a
    stop-limit order. The limit must be on the correct side of the stop. Post-only is allowed
    only on a GTC limit order. Stop-limit orders are GTC.
  * **Rounding.** A sell stop rounds up (it never triggers lower than intended) and a buy stop
    rounds down. The minimum order value is checked at the limit price.
  * **Paper simulation.** A post-only order that would take liquidity is refused, as Binance
    refuses a `LIMIT_MAKER` order. A stop-limit order that would trigger immediately is
    refused. The trigger reference is the best bid for a sell stop and the best ask for a buy
    stop. Once triggered, the order fills up to its limit as a taker. If the price gaps through
    the limit, the order rests unfilled. The paper broker's full lifecycle is tested:
    waiting, then triggered without a fill, then filled as a maker at its limit.
  * **Binance wire format.** Real ccxt request building, run offline: what the adapter sends
    becomes `STOP_LOSS_LIMIT` (with `stopPrice` and `timeInForce=GTC`) or `LIMIT_MAKER`.
* `tests/exchange/test_ccxt_adapter.py` covers the adapter.
  * Stop-limit submit parameters are correct, and a response with a trigger price is read back
    as a stop-limit order.
  * An adapter without stop-limit support refuses the order without calling the exchange.
  * Post-only and IOC are tested separately. Their combination is now refused: Binance's
    `LIMIT_MAKER` takes no time-in-force, and no NEXUS code used the combination.
* `tests/integration/test_execution.py` runs on real PostgreSQL.
  * A paper entry fills, then a protective stop-limit sell is placed, locking the BTC. The
    price gaps through the stop: the order is marked triggered but nothing fills. A NEW order
    manager, standing in for a restart, still knows the order was triggered. It fills at the
    limit, and the position closes with reason `stop_loss`.
  * A post-only order that would take is rejected and its reserve released. A passive one
    rests.
  * The database refuses a stop-limit row without a stop price.
* Migration 0013 was upgraded, checked with `alembic check`, downgraded and upgraded again.
* Full run: `scripts/test.sh` (ruff, mypy --strict, pytest with Postgres and Redis): 772 passed.
* Not tested: real Binance stop-limit or post-only orders. That needs a demo key (F2).

## 2026-11-23: strategy modules A-G

| Suite | Result |
|---|---|
| Lint, types (139 files), `alembic check` (migration 0012) | pass |
| Unit + integration | **718 passed**, 0 failed |
| Pre-registered walk-forward (research, not a code test) | all 7 FAIL on unseen 2022-2024 data; see NEXUS_BACKTEST_REPORT.md |

New tests (10): every A-G strategy is causal (results unchanged when later data is
added) and produces stops below and targets above the close; every grid combination is a
valid parameter set; market-structure enters on a held retest of the broken high and not
on a failed one; breakout entries are retests above the broken level.

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
