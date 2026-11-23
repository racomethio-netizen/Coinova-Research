# NEXUS integration map, 2026-11-23 (owner brief: "world-class quantitative trading engine")

Part 1 of the brief: what already exists, what each reference project contributes, and
what was missing.

**Status legend**
* **Working**: implemented, tested and running in production in paper mode.
* **Built**: implemented and tested, but not running live (research, gated or disabled).
* **Partial**: some pieces exist, with gaps listed.
* **Missing**.

**Reference projects are used for concepts only. None is installed as a dependency:**
* Hummingbot: connector / order-tracker / in-flight-order design.
* Freqtrade: the strategy interface and protections.
* FreqAI: feature pipeline, chronological retraining and outlier detection.
* TradingView: indicator definitions.
* 3Commas: portfolio automation ideas.
* Institutional practice: implementation shortfall, risk budgets, pre-registration.

## Platform as it stands

| Layer | Where | Status |
|---|---|---|
| Backend | FastAPI `apps/api`. 12 workers run inline on their own event loop and thread (since 2026-11-23). | Working |
| Frontend | Next.js static dashboard `apps/dashboard`, 30+ pages, served by the API | Working |
| Database | Supabase Postgres (us-west-2) via SQLAlchemy and Alembic. Decimal / NUMERIC for money. Audit log. | Working |
| Events / cache | Redis (Render Key Value, free plan): heartbeats, state, rate-limit gate, WebSocket fan-out | Working |
| Server | Render Frankfurt. A US region is blocked by Binance (HTTP 451). | Working |

## Map by brief part

### Part 2: exchange engine

| Component | Module | Status |
|---|---|---|
| Binance spot REST | `nexus_exchange/ccxt_adapter.py` (CcxtSpotAdapter) | Working |
| Binance USD-M futures | `binance_futures.py`, `binance_futures_transport.py` | Built (paper only; futures disabled) |
| WebSocket market data and order book | `nexus_market/binance_ws.py`: depth20, klines, one combined stream | Working |
| Historical data | `nexus_market/runner.py` backfill (1,499 daily bars per pair); data.binance.vision archives for research | Working |
| Balances and reconciliation | `nexus_execution/reconcile.py` (one-sided ledger ≤ exchange rule) | Built (live only) |
| Orders, partial fills, client IDs | `order_manager.py`: deterministic client IDs, lookup before submit, reconcile after timeout | Working (paper); testnet round trip built |
| Rate limits | `nexus_common/binance_guard.py`: central weight gate and circuit breaker | Working |
| Disconnection recovery | WebSocket reconnect with back-off; stale-data guard in the trader | Working |
| Capability registry | `nexus_exchange/registry.py`: live CCXT detection | Working. **KuCoin and Gate.io added this session** (detected only). |
| OKX, Bybit, Kraken, KuCoin, Gate.io trading | none | **Missing.** Only Kraken has a spot adapter, untested live. Detection is not support: nothing is claimed for the others. |

### Part 3: strategy engine

| Brief module | Module | Status |
|---|---|---|
| A. Time-series momentum | `nexus_backtest/tsmom*.py`, `crypto_trends.py` | Built. TSMOM grid failed (0 of 96). Crypto Trends passed and is in forward paper. |
| B. Trend following (EMA 20/50/200, ADX, MACD, ATR, volume) | `bot_strategy.py`, `library_global.py`, `spot_strategies.py` | Working (paper bot) |
| C. Breakout (Donchian, volatility expansion, failure) | `library_global.py` (`atr_expansion_breakout` passed), `spot_strategies.py` breakout / retest | Working (forward paper) |
| D. Mean reversion (VWAP, Bollinger, RSI, regime filter) | `library_global.py`, `library_v2.py`, `library_v3.py` | Built (did not pass validation; disabled) |
| E. Futures intelligence (funding, OI, basis, liquidation) | `nexus_market/derivatives.py` (worker), `futures_sizing.py` (liquidation distance), `carry_paper.py` | Working (data and paper) |
| F. Statistical arbitrage | **`nexus_strategies/stat_arb.py` (new)** | Built: correlation screen, Engle-Granger cointegration (statistics match statsmodels), half-life, z-score signals with a cost gate, reasons on every decision. **Research only:** a short leg needs futures. |
| Standard signals with reasons | `base.py` Strategy / Signal, decision log, proposals | Working |

### Part 4: machine learning

| Component | Module | Status |
|---|---|---|
| LightGBM, XGBoost, logistic regression, CatBoost (if installed) | `nexus_ai/candidates.py` | Built |
| Random forest | `candidates.py` family `rf` (**new**, opt-in) | Built |
| Regime classification | `hmm_regime.py` (causal forward filter), `spot_regime.py` | Built |
| Volatility forecasting | `features.py` / `analysis` realised-vol features | Partial: features only, no dedicated forecast model |
| Anomaly detection | IsolationForest gate in `candidates.py` | Built |
| Purged CV, walk-forward, out-of-sample, leakage guards | `cv.py`, `research.py`, `phase_d.py` | Built |
| Versioning | `registry.py` (sha256 artifacts; activation only by the owner) | Built |
| Drift and calibration | `drift.py` (PSI and live calibration), isotonic calibration | Built |
| Automatic rejection | registry status `rejected`; edge monitor for live strategies | Built |
| Baseline comparison | `phase_d.py` reports against simple rules | Built. Result: no model beat the baselines, none active. |

**ML never authorises orders.** Its output is one input to fusion. The deterministic risk
engine and pre-trade checks decide.

### Part 5: orchestrator

`nexus_decision/selector.py` and `fusion.py`, the agents and proposals, and the
Risk-Manager verdict.
* Regime → eligible strategies → cost-aware ranking → risk budget → cash when nothing
  qualifies.
* Correlated exposure is limited by `limits.py`.
* **Working**: it keeps the book in cash because no strategy is validated for live
  trading. Trading frequency is never forced.

### Part 6: risk engine

`nexus_risk/limits.py`, `spot_rules.py`, `protections.py`, `edge_monitor.py`, the kill
switch and the risk-state machine.
* Limits: 0.25% per trade, 1% daily, 3% weekly, 5% drawdown, 1% aggregate open risk,
  futures 1x.
* Volatility (ATR) sizing; exposure and correlation caps; liquidation and funding checks.
* Durable kill switch; reconciliation.
* No martingale and no averaging down (enforced by the order manager).
* **Working.**

### Part 7: execution

| Component | Module | Status |
|---|---|---|
| Limit / market / IOC, idempotent client IDs, reconcile after timeout | `order_manager.py`, `testnet_run.py` | Working (paper); testnet built |
| Post-only | adapter capability flag, `paper.py` | Built |
| Pre-trade gate (fat finger, price band, rate, REDUCING) | `pretrade.py` | Working |
| Spread and slippage estimation, liquidity checks, slicing, post-only vs IOC choice | **`nexus_execution/liquidity.py` (new)** | Built. Wired into the testnet round trip (**REJECTED_BY_LIQUIDITY** when the book is too thin or wide). Not yet wired into the paper trader. |
| Execution quality report | Phase 4 paper realism | Working |

### Part 8: research lab

| Component | Module | Status |
|---|---|---|
| Event-driven backtest: fees, slippage, spread, funding, partial fills, precision, stops, liquidity | `engine.py`, `library_engine.py`, `futures_engine.py` | Built |
| Walk-forward, out-of-sample, Monte Carlo, parameter stability / PBO, cost stress | `walkforward.py`, `montecarlo.py`, `stability.py`, `significance.py` | Built |
| Metrics (Sharpe, Sortino, Calmar via CAGR / DD, PF, expectancy, win rate, duration, fees, exposure) | `metrics.py` | Built |
| Bull / bear / sideways / high-vol / low-liquidity / crash breakdown | **`market_conditions.py` (new)**: causal labels, per-condition metrics, fragile-condition list | Built |
| Pre-registration and rejection of failures | `docs/research/*`, Holm, stationary bootstrap | Working practice: 0 of 96 TSMOM; 1 of 8 library strategies passed |

### Part 9: modes

* RESEARCH / PAPER / SHADOW (SIGNALS_ONLY) / LIVE; PAPER is the default.
* Live trading requires:
  * config;
  * owner 2FA approval at most 30 days old;
  * kill switch clear;
  * a production key with withdrawals off;
  * a key test under 7 days old.
* Nothing switches to live on restart or deploy. Spot and futures permissions are
  separate.
* **Working.** The Binance testnet round trip is built (owner + 2FA); it waits for the
  owner's testnet key.

### Part 10: dashboard

Pages exist for:
* overview, exchanges, markets, intelligence, strategies, strategy lab, trading bot;
* spot, futures, positions, risk, orders, backtests;
* paper, models, trades, system, security (audit), connection.

Paper results are labelled paper. **Working** (the latest backend work is pending deploy).

## Bugs and incomplete items found in this audit

1. **Dashboard sluggishness.** Fixed in f795696 (workers on their own thread), pending
   deploy.
2. The liquidity planner is not yet wired into the paper trader's order path (next step).
3. No trading adapter for OKX, Bybit, KuCoin or Gate.io. Kraken's adapter has never been
   exercised against the exchange.
4. No dedicated volatility-forecast model (features only).
5. Stat-arb has no pre-registered out-of-sample test yet. It cannot trade without futures.
6. The production Binance key last tested with spot trading off and 0 USDT. The owner
   must re-run the key Test.

## Next phases (in order)

1. Deploy (needs the owner's "Deploy").
2. Testnet round trip with the owner's testnet key: the first real exchange evidence.
3. Wire `liquidity.plan` into the paper trader. Record the plan with each decision.
4. Pre-register and run a stat-arb pairs test on futures data (with funding), out of
   sample.
5. OKX adapter on the same interface, with a fake-exchange test suite, then a demo-account
   round trip.
