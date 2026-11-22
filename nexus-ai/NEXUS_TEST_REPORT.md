# NEXUS test report

How to run: `scripts/test.sh` with `NEXUS_TEST_DATABASE_URL` and `NEXUS_TEST_REDIS_URL`
(Postgres and Redis test databases; integration tests are skipped without them). Browser
tests: `scripts/e2e.sh` (see the script header).

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
