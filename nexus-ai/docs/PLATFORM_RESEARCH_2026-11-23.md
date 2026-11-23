# NEXUS platform audit, technology research and roadmap

_2026-11-23, for the owner's brief "NEXUS AI — Ultimate quantitative trading intelligence
platform" (phase 13, steps 1–3). Nothing in the architecture was changed for this report.
Live trading is OFF and stays OFF._

## How the research was done (what was actually inspected)

| Technology | What was read | Commit / version checked |
|---|---|---|
| NautilusTrader | Source: `crates/risk/src/engine/mod.rs` (pre-trade checks, trading states), `crates/execution/src/models/fill.rs` | 126bf01 (2026-11-23) |
| QuantConnect LEAN | Source: `Algorithm.Framework/{Alphas,Portfolio,Execution,Risk}`, `Common/Orders/Slippage/VolumeShareSlippageModel.cs`, `Execution/SpreadExecutionModel.py`, `Common/Brokerages/BinanceBrokerageModel.cs` (listed) | 32a302a (2026-11-20) |
| Freqtrade | Source: `plugins/protections/*`, `optimize/analysis/{lookahead,recursive}.py`, `templates/sample_strategy.py` | 77ec0c3 (2026-11-23) |
| FreqAI | Source: `freqai/data_kitchen.py` (recency weights), `freqai/freqai_interface.py` (feature pipeline, Dissimilarity Index, SVM / DBSCAN outliers) | same |
| Hummingbot | Source: `strategy_v2/executors/position_executor/*` (triple barrier, retries), executor list | 3351ada (2026-11-18) |
| Jesse | Source: `utils.py` (`risk_to_qty`, `limit_stop_loss`), `research/monte_carlo/*`, `research/rule_significance_testing/*` | ddb5a66 (2026-11-17) |
| OctoBot | Source: package layout, `tentacles/Evaluator/*`, `tentacles/Trading/Mode/*` (16 trading modes), `mixed_strategies_evaluator` | fe09c95 (2026-11-19) |
| Superalgos | Repository layout and README only (a JavaScript visual-scripting platform); strategy internals **not** read | 9f0fb59 (2026-05-07) |
| Backtrader | Source: `analyzers/*` (SQN, Sharpe, DrawDown…), broker and slippage notes | b853d7c (2023-04-19) |
| VectorBT | Source: `portfolio/base.py` (`from_signals`), `indicators/factory.py` (parameter products), LICENSE | 9ac1b69 (2026-11-11) |
| Optuna | Source tree: `samplers/` (TPE, CMA-ES, NSGA-II/III, GP, QMC), `pruners/` (median, hyperband, Wilcoxon…) | 0abf066 (2026-11-18), v5.1.0 |
| PyPortfolioOpt | Source: `hierarchical_portfolio.py` (HRP), module list | a6638d2 (2026-07-07), v1.6.0 |
| Qlib | Source: `contrib/model/*` (31 models: GBDT, XGBoost, CatBoost, LSTM, GRU, TCN, Transformer, TRA…), `contrib/data/loader.py` (Alpha158 / Alpha360 features) | 5435523 (2026-10-08), PyPI 0.9.7 (2025-08-15) |
| CCXT | Installed source in NEXUS (4.5.91): `async_support/base/throttler.py` (leaky bucket), `pro/binance.py` (user-data stream: listenKey and the WebSocket-API `userDataStream.subscribe.signature`) | 4.5.91 |
| Binance Spot API docs | `user-data-stream.md`, `filters.md`, `enums.md`, `rest-api.md`, `CHANGELOG.md` | 473c101 (2026-11-18) |
| TradingView | Pine Script documentation pages on repainting and strategies (read in the previous session). Individual public scripts were **not** audited. | n/a |
| LightGBM, XGBoost, CatBoost | Not cloned. Already integrated and tested in NEXUS (Phase D). PyPI metadata and license checked. | 4.7.0 / 3.4.1 / 1.2.10 |
| PyTorch, Stable-Baselines3, Gymnasium | **Source not inspected.** PyPI metadata only (version, date, license). | 2.15.0 / 2.9.2 / 1.4.0 |

## 1. Repository audit (NEXUS as it stands)

**Shape.**
* Python backend: FastAPI with inline workers, 13 service packages, about 168 typed source
  files.
* Next.js dashboard: 38 pages.
* PostgreSQL on Supabase (17 migrations, row-level security); Redis on Render.
* One Render web service in Frankfurt runs everything.
* Tests: 868 automated tests (unit and integration) plus 15 Playwright end-to-end tests.

| Area | Exists (module) | Gap for this brief |
|---|---|---|
| Market data | WebSocket-first klines and books, central Binance weight gate, data-quality report, closed-bar store (`nexus_market`) | 1m / 3m / 30m are supported by the schema but **not ingested** (production stores 5m, 15m, 1h, 4h, 1d) |
| Market intelligence | Regime classifiers (`regime.py`, `spot_regime.py`, causal HMM), market structure, levels, sweeps, MTF scores, false-breakout follow-through (`nexus_analysis`, `ta.py`), no-trade rules | Classifiers are spread over several modules: no single "market state" with abnormal-move and false-breakout labels |
| Strategy library | 16 legacy rules, spot engine (2), global library (8), Crypto Trends | Missing: **volume intelligence** (relative volume, taker buy pressure from kline data), **relative strength vs BTC**, **volatility-adaptive sizing** as strategy families |
| Machine learning | Purged k-fold with embargo, labels, logit / XGBoost / LightGBM / CatBoost, isotonic calibration, Isolation-Forest OOD veto, HMM, registry with rollback, drift (`nexus_ai`) | No model passed. Missing: regime classification and **strategy-degradation** targets, monitoring dashboard, retraining controls |
| Ensemble / selection | Selector (per-regime OOS evidence; forward results can only remove a strategy), fusion with NO_TRADE default (`nexus_decision`) | Signal **correlation** is not measured (agreement is not de-duplicated); no sequential degradation test that auto-disables |
| Execution | Market / limit / post-only / stop-limit; precision and min-notional; client IDs; duplicate guard; unknown-state reconciliation (never resubmit); restart recovery; pre-trade checks (S3) | **No WebSocket user-data stream** (order state comes from REST polling); no order splitting; spot `PERCENT_PRICE_BY_SIDE` / `MARKET_LOT_SIZE` / `MAX_NUM_ORDERS` filters not checked locally (Binance would reject them) |
| Risk | Ceilings, durable kill switch, risk-state machine, stop-distance sizing with costs, correlation limits, loss streak, protections, 5% drawdown, owner profile 0.5% / 1% / 2% / 5% | **Abnormal-price-move guard** and **volatility-targeted sizing** not implemented |
| Backtesting lab | Next-open fills, fees, spread, volume slippage, partial fills, filters, walk-forward, PBO, Monte Carlo, stationary bootstrap, Holm, SQN | Calmar, exposure and turnover are not in every report; no bull / bear / sideways segmentation report; no Optuna |
| Paper (180 days) | 4 forward tests running plus the library forward test (S5, not yet deployed) | Model predictions and "missed opportunities" are not logged as such |
| Dashboard | Connection, markets, signals, positions, risk, kill switch, backtests, paper pages, Strategy lab… | No single strategy-ranking, ML-performance or trading-log view |
| Production | Health checks, structured JSON logs with secret redaction, Binance circuit breaker, restart recovery, Supabase pool of 15 sessions (hard limit), 2 GB instance | 12 inline workers on one instance; the **shared egress IP** gets Binance bans; **S1–S5 are committed but not deployed** |

**Known issues today**
1. **Undeployed work.** S1–S5 (warm-up fix, protections, pre-trade checks, library forward
   test) are not deployed. The deploy was blocked pending the owner's explicit "Deploy".
2. **Futures data ban.** The futures data feed is paused by a Binance IP ban until 08:54:50
   UTC on 24, 25 or 26 Nov (the stored end shows only the time of day, so the exact day is
   unknown). Spot is unaffected.
3. **Daily warm-up.** Daily library strategies need about 400 stored daily bars. The stored
   1d history in production is not verified (the Supabase tool was unavailable during
   this audit).

**Evidence so far (Binance spot, after costs, pre-registered, tested once each)**
* About 45 rule sets and every ML configuration have been tested.
* Passed: **Crypto Trends (daily trend ensemble)** and **ATR-expansion breakout (daily)**.
* All 1h and shorter-timeframe rules failed. 4h results were not significant.
* This evidence drives the roadmap: costs dominate fast signals on Binance spot.

## 2. Technology research and comparison

| Technology | Type, language | Maintenance (last commit) | License | Useful for NEXUS | Integrate how |
|---|---|---|---|---|---|
| NautilusTrader | Event-driven engine, Rust + Python | Active (2026-11-23) | **LGPL-3.0** | Pre-trade risk checks; ACTIVE / REDUCING / HALTED states; fill model with fill and slippage probabilities; start-up reconciliation | **Ideas only** (S3 already done). Re-platforming NEXUS on it would be the rebuild the brief forbids |
| QuantConnect LEAN | Engine, C# (+ Python algorithms) | Active (2026-11-20) | Apache-2.0 | Alpha → portfolio → execution → risk pipeline; `VolumeShareSlippageModel` (slip = participation² × impact); `SpreadExecutionModel` (trade only while spread ≤ x%); drawdown and trailing risk models | Ideas: spread gate, volume-share slippage, the framework's separation of concerns. Not a dependency (.NET) |
| Freqtrade | Bot, Python | Active | **GPL-3.0** | Protections, lookahead and recursive analysis | Ideas only (re-implemented, no code copied): S1 and S2 done |
| FreqAI | ML layer of Freqtrade | Active | GPL-3.0 | Sliding retrain windows, recency weights, Dissimilarity Index abstain, outlier removal | Ideas: recency weights and DI (NEXUS's Isolation-Forest veto is equivalent) |
| Hummingbot | Market making / executors, Python + Cython | Active (2026-11-18) | Apache-2.0 | Triple barrier (TP / SL / time / trailing; SL and time exits must be market orders), capped retries, budget checker, TWAP executor | Ideas: time barrier (done in the library); TWAP / order splitting design |
| Jesse | Framework, Python | Active (2026-11-17) | MIT | Fee-inclusive `risk_to_qty`, rule-significance test (stationary bootstrap), trade and candle Monte Carlo | Done (S4) |
| OctoBot | Bot, Python | Active (2026-11-19) | **GPL-3.0** | 16 trading modes (DCA, grid, market making, index, AI…); evaluators averaged into one note (`total_evaluation / counter`) | Caution: averaging correlated evaluators is the "false independent confirmation" the brief warns about. Grid and DCA modes average down (forbidden). Nothing to import |
| Superalgos | Visual scripting platform, JavaScript | Slower (last commit 2026-05-07) | Apache-2.0 | Visual strategy design and data mining | Not suitable: different stack and paradigm. Internals not inspected |
| Backtrader | Backtester, Python | **Unmaintained** (last commit 2023-04-19) | **GPL-3.0** | Analyzers (SQN, VWR, Calmar), broker simulation | Ideas only (SQN done; Calmar to add) |
| VectorBT | Vectorized backtesting, Python / numba | Active (2026-11-11) | **Apache-2.0 + Commons Clause** (no right to sell a product deriving its value from it) | Very fast parameter sweeps (`from_signals`, parameter products) | **Not as a dependency**: the Commons Clause would restrict any future commercial NEXUS. NEXUS's own engines already vectorize signals |
| Optuna | Hyperparameter optimization | Active (v5.1.0, 2026-11-16) | MIT | TPE / CMA-ES / NSGA samplers, pruners, study storage in PostgreSQL | **Recommended dependency** (research extra only): controlled optimization inside walk-forward, with trial counts feeding a multiple-testing penalty |
| PyPortfolioOpt | Portfolio optimization | Active (v1.6.0, 2026-02-26) | MIT | HRP (no matrix inversion, robust for crypto's unstable covariances), Black-Litterman, CLA | Re-implement HRP (about 50 lines with SciPy). The package pulls in cvxpy, which is too heavy for the 2 GB instance |
| Qlib | Quant research platform (Microsoft) | Repo active (2026-10-08); last PyPI release 0.9.7 (2025-08-15), **no Python 3.13 wheels** (NEXUS runs 3.13) | MIT | Alpha158 / Alpha360 feature sets, 31 model implementations, research workflow | Ideas: Alpha158-style features for NEXUS's feature set. Not installable without building from source |
| LightGBM / XGBoost / CatBoost | Gradient boosting | Active | MIT / Apache-2.0 / Apache-2.0 | Tabular models | **Already integrated** (Phase D), with no validated edge |
| PyTorch | Deep learning | Active (2.15.0) | Apache-2.0 / BSD-style | Temporal nets (LSTM, TCN, Transformer as in Qlib) | Research-only, and only with a pre-registered reason. Very large install; tree models already fail to beat chance on these features |
| Stable-Baselines3 + Gymnasium | Reinforcement learning | Active (2.9.2 / 1.4.0) | MIT / MIT | RL agents and environments | **Not recommended.** RL learns the simulator's cost model and overfits a single price path; it has no edge where supervised models found none. The brief forbids AI executing or overriding risk anyway |
| CCXT | Exchange library | Active (4.5.91 installed) | MIT | Unified orders, precision, leaky-bucket throttler, `pro` WebSocket user-data stream (WS-API `userDataStream.subscribe.signature`) | **Already the adapter.** Add `watch_orders` / `watch_balance` |
| Binance Spot API | Official docs | Updated 2026-11-18 | Binance terms | listenKey user streams for `wss://stream.binance.com` are deprecated (docs removed 2025); use the **WebSocket API user data stream**. Filters include `PERCENT_PRICE_BY_SIDE`, `MARKET_LOT_SIZE`, `MAX_NUM_ORDERS`; self-trade prevention modes; unfilled-order-count limits | Implement the WS-API user stream and the missing local filter checks |
| TradingView public scripts | Pine Script | n/a | Per-script author terms | Rule ideas only. TradingView's documentation warns that repainting "invalidates backtesting results", and that the tester defaults to no commission and zero slippage | Rules re-tested with costs (done in S4) |

**Licensing rule for NEXUS.**
* Code from GPL projects (Freqtrade, Backtrader, OctoBot) is never copied, only ideas
  re-implemented.
* LGPL (Nautilus) only as an unmodified library, which is not planned.
* Commons-Clause software (VectorBT) is not used.
* MIT and Apache-2.0 dependencies are acceptable when justified.

## 3. Implementation roadmap (stages; each is tested and reported before the next)

The order follows the evidence:
* execution and safety first;
* then the data and strategy families that the evidence favours (daily / 4h, volume,
  volatility);
* ML and automated research last, because they can only promote what the gates prove.

| Stage | Brief phase | Content | Done when |
|---|---|---|---|
| R0 | 12 | Deploy S1–S5 (needs the owner's "Deploy"); verify workers, the warm-up state and the stored 1d history | Logs clean, Strategy lab shows data |
| R1 | 6 | **User-data WebSocket** (CCXT `watch_orders` over the WS API) feeding the order manager, with REST reconciliation kept as the source of truth after any disconnect; local checks for `PERCENT_PRICE_BY_SIDE`, `MARKET_LOT_SIZE`, `MAX_NUM_ORDERS`; spread gate (LEAN); order splitting for orders above x% of top-of-book depth (TWAP / participation cap) | Integration tests with a fake stream: missed events, duplicates and reconnects all reconcile; no double fills |
| R2 | 7 | Abnormal-move guard (k-sigma 1m return, spread blow-out, book-depth collapse → REDUCING); volatility-targeted sizing (risk scaled by forecast / long-run volatility, capped at 1×) | Unit tests; risk keeps final authority |
| R3 | 2 | One **MarketState** service over the existing classifiers: bull / bear / sideways / volatile / low liquidity / breakout / false breakout / abnormal, multi-timeframe, causal. Ingest 1m and derive 3m / 30m (for execution and abnormal-move detection, not for signals) | Prefix-invariance and start-up tests on every label; storage measured |
| R4 | 3 | New families, pre-registered and tested once on 4h and 1d: **volume intelligence** (relative volume, acceleration, volume-price divergence, *taker-buy ratio from Binance klines*, which are real historical buy pressure); **relative strength vs BTC**; **volatility-adaptive** variants | Results appended to the pre-registration, Holm across the family |
| R5 | 8 | Lab report additions: Calmar, exposure, turnover, bull / bear / sideways segmentation, deflated Sharpe | Unit tests on known series |
| R6 | 5 | Ensemble upgrade: measure **signal correlation** (effective number of independent signals); cost-aware expected value; **CUSUM / sequential test** on forward R per strategy, which auto-disables (disabling is always allowed; re-enabling needs new validated evidence); portfolio weights by **HRP** across validated strategies | Tests: correlated duplicates do not raise confidence; a degraded synthetic strategy is disabled |
| R7 | 9 | Paper programme completeness: log model predictions and missed opportunities (rejected signals whose simulated outcome is recorded); real paper orders (OrderManager + PaperBroker) for validated strategies, to measure execution differences | Persisted, survives restarts, e2e |
| R8 | 4 | ML: regime classification and strategy-degradation targets, monitoring page, retraining controls (scheduled, versioned, rollback, never self-activating) | Pre-registered evaluation; no model can trade or override risk |
| R9 | 11 | **Optuna** research loop: walk-forward objective, trials stored, trial count feeding a deflated-Sharpe / Holm penalty, candidate versions, promotion only through a pre-registered hold-out plus 180 days of paper | Nothing reaches live automatically, by construction |
| R10 | 10 | Dashboard: strategy rankings, ML performance, trading logs; an audit that every figure is labelled live / backtest / paper | e2e at 360 px and desktop |

**Not planned, with reasons:**
* Futures and leverage: the brief forbids them.
* Grid, DCA and martingale: they average down.
* Reinforcement-learning execution: no evidence of benefit, high overfitting risk.
* Microservices: one instance with inline workers is sufficient. The constraints are the
  database pool and memory, not CPU.
* 1m / 3m indicator scalping: the evidence shows costs dominate. 1m data is still used for
  execution quality and abnormal-move detection.

## 4. Risks and limitations

* **No guarantee of an edge.** The two strategies that passed are slow daily systems with
  modest returns. Most hypotheses fail, and more sophistication does not change that.
  Reports will say so.
* **Multiple testing grows with every idea.** Each new test raises the chance of a false
  pass. The plan therefore counts all trials (R9) and keeps the pre-registered, test-once
  discipline.
* **Infrastructure.**
  * The shared egress IP is exposed to Binance bans caused by other tenants. A dedicated IP
    is the owner's decision (Oracle Always Free is the only free option found).
  * The 15-session Supabase pool and the 2 GB instance cap how many workers can run.
  * 1m ingestion adds about 1 million rows per pair per 2 years (measured before enabling).
* **Execution realism.** There is no historical order book, so backtests model spread and
  partial fills from bars. Paper fills use live books but cannot model market impact on
  real size.
* **Live trading.**
  * It stays OFF, with no real orders, withdrawals or API-permission changes.
  * Each needs the owner's separate explicit authorisation.
  * The supervised testnet order (F2) and the live gates remain the owner's steps.
