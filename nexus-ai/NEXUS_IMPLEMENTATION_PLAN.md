# NEXUS implementation plan (deep upgrade)

Date: 2026-11-22. Based on NEXUS_AUDIT.md. Order: production-critical safety first, then
data and research capability, then presentation. Each phase ends with tests, a test-report
entry and docs; the owner approves each phase before it starts ("Phase N").

Rules for every phase: nothing removed that works; no faked data, models, signals,
backtests, balances or PnL; live trading stays off; risk ceilings unchanged without an
owner mandate; one new migration per schema change; Decimal for money.

## Constraints that shape the plan

* Hosting is Render free (512 MB, one process, shared egress IP). Anything memory-heavy
  (1m candles for many pairs, LightGBM/CatBoost training) runs offline (CLI) or needs a
  paid plan. Training never runs inside the web service.
* Supabase free database (~500 MB, 29 MB used). 1m candles cost about 0.5 MB per pair per
  day of history; plan: keep 1m for BTC/ETH only, 30 days rolling.
* Binance is banned on the shared IP until 23 Nov ~08:54 UTC. No deploys before then.

## Phase A: production safety (critical)

| # | Change | Files (planned) | Proof |
|---|---|---|---|
| A1 (done 22 Nov) | **Live balance reconciliation**: each live cycle fetch Binance balances, compare with the internal ledger per asset with a tolerance; any mismatch blocks new entries, raises a Telegram alert and an audit entry; never auto-corrects the ledger | `services/execution-engine` (reconcile), trader gate, `/live/reconciliation` API, live page panel | unit + integration tests with a fake exchange: match, mismatch, fetch failure (= block) |
| A2 (done 22 Nov) | **Persisted risk-state machine**: NORMAL, REDUCED_RISK, NO_NEW_TRADES, CLOSE_ONLY, EMERGENCY_STOP; derived from limits, streak, reconciliation, data health and the kill switch; stored in the audit log (durable like the kill switch); manual escalation any time, de-escalation owner-only; EMERGENCY_STOP never auto-clears | `nexus_risk/state.py`, migration only if a table is needed, trader, `/risk/state` API, dashboard badge | transition table tests; restart restores the state; Redis loss fails to the stricter state |
| A3 (done 22 Nov) | **Four operating modes**: ANALYSIS (no signals stored), SIGNALS (signals only), PAPER (current), LIVE (still behind the six gates); one owner setting with audit; LIVE can only be selected when the gates pass. Also: an owner-confirmed way to start the live ledger from the reconciled exchange balance (today it starts empty) | settings + trader + `/settings/mode` + settings page | tests that each mode cannot produce the next mode's side effects |
| A4 (prepared 22 Nov; owner step in NEXUS_DEPLOYMENT_GUIDE.md) | **Least-privilege database role in production**: connect the app as `nexus_app` instead of `postgres`; migrations keep the owner role | Render env (owner approval), `docs/NEXUS_DEPLOYMENT_GUIDE.md` | grants test already exists; verify `/health/ready` after switch |
| A5 (done 22 Nov) | **Rate-limit budget**: count Binance request weight per minute from response headers; slow down at 50%, stop at 80% of the limit | `nexus_common/binance_guard.py` | unit tests on header parsing and thresholds |
| A6 (done 22 Nov) | **Restart-during-execution tests**: crash between submit and persist, and between fill and position update | integration tests | tests |

## Phase B: market data

* B1 (done 22 Nov) Timeframes 30m, 2h, 6h, 12h (resampled from stored bars, no extra API calls); 1m and
  3m for BTC/ETH only, 30-day rolling retention.
* B2 (done 22 Nov) Data-quality report per pair/timeframe (gaps, duplicates, stale) on the data page.
* B3 Live trade (tick) stream for BTC/ETH with bounded memory: not started.

## Phase C: technical analysis

* C1 (done 22 Nov) Indicators: Supertrend, Ichimoku, Stochastic RSI, CCI, ROC, OBV, MFI, Keltner, pivots,
  RSI divergence, equal highs/lows. Each validated against a reference calculation in tests.
* C2 (done 22 Nov; feeding them to models is D1) Multi-timeframe agreement score (causal, aligned on bar close) as features.

## Strategy modules A-G (brief Phase 4): done 23 Nov

Built, tested and validated once on unseen 2022-2024 data: all 7 FAIL (NEXUS_BACKTEST_REPORT.md).

## Phase D: machine learning (offline training)

* D1 (done 23 Nov) Purged k-fold with embargo for every model; multi-target labels (direction after
  costs, triple barrier).
* D2 (done 23 Nov; research-only extra) Add LightGBM; HMM regime model; Isolation Forest anomaly flag (pinned versions, only
  if they install in the memory budget; otherwise offline CLI only).
* D3 (done 23 Nov: rollback added; result: no model passes, NEXUS_MODEL_REPORT.md) Model registry: version, data range, metrics, sha256, rollback. Retraining is a CLI the
  owner runs; promotion only through existing gates. Results written to NEXUS_MODEL_REPORT.md
  whatever they are (a failed model is reported as failed).

## Phase E: decision layer

* E1 (done 23 Nov) Signal fusion: strategies + models + context -> LONG / SHORT / WAIT / NO TRADE with a
  calibrated probability and reasons; the Risk Manager keeps the final veto.
* E2 (done 23 Nov) Strategy selector by regime with minimum sample-size rules (no strategy is selected
  without a validated edge in that regime).

## Phase F: execution

* F1 (done 23 Nov: post-only and stop-limit) Post-only and stop-limit orders (paper and
  live adapters), exchange-side protective stops for live spot (OCO) where supported.
  Moved to F2: OCO and placing exchange-side stops from the trader. An OCO is two live
  sell orders on one balance, which the one-active-order-per-side database rule does not
  allow yet (needs an order-list design), and both need a demo run before they are used.
* F2 Testnet/demo end-to-end run (needs a new demo key from the owner).

## Phase G: backtesting

* G1 Funding, borrow and slippage-by-depth models in the event backtester; per-regime and
  per-hour reports; parameter-stability report. Results to NEXUS_BACKTEST_REPORT.md.

## Phase H: dashboard and documents

* H1 Risk-state badge, mode switch, reconciliation panel, data-quality page, model registry
  page.
* H2 Documents: NEXUS_ARCHITECTURE.md, NEXUS_RISK_POLICY.md, NEXUS_MODEL_REPORT.md,
  NEXUS_BACKTEST_REPORT.md, NEXUS_TEST_REPORT.md, NEXUS_DEPLOYMENT_GUIDE.md (started in
  Phase A and kept current).

## Owner decisions needed (cannot be done by code)

1. A paid host with a dedicated or less-shared IP (Render Standard or similar) before any
   live trading; free hosting is fine for paper.
2. A Binance demo key for F2.
3. Approval to switch the production DB role (A4).
4. X Bearer token, if X sentiment is wanted.
5. Live trading: remains off. It is only worth discussing after a strategy passes a forward
   test.

## What this plan will not do

* Promise or imply profit. Most of these phases improve safety and research quality; none
  of them creates an edge by itself.
* Enable live trading, raise leverage, add martingale or averaging down, or bypass Binance
  regional restrictions.
