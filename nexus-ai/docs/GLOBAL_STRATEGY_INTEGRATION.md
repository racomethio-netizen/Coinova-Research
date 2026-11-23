# Global strategy integration: audit, research and plan

_Written 2026-11-23 for the owner's "MASTER COMMAND: global trading strategy integration".
Live trading stays disabled throughout. Nothing here enables it._

## 1. Repository audit

NEXUS already implements most of the brief. This upgrade therefore **adds what is missing**
rather than rebuilding what works. Everything below was checked in the code, not taken from
the docs.

| Brief section | What already exists (module) | State |
|---|---|---|
| Strategy library (§2) | 16 rule strategies, all long-only spot: trend, momentum, breakout, mean reversion, pullback, sweep, VWAP, volatility expansion, MTF confluence, Supertrend, market structure (`nexus_strategies/strategies.py`, `library_v2.py`, `library_v3.py`). There are also Trend Pullback and Breakout + Retest (`spot_strategies.py`), and the daily Crypto Trends ensemble (`nexus_backtest/crypto_trends.py`). | Built, tested for lookahead. **All 18 intraday rules failed** pre-registered out-of-sample tests; only the daily Crypto Trends ensemble passed. |
| Advanced analysis (§2E) | Market structure, support/resistance levels, liquidity sweeps, MTF scores, correlation (`nexus_analysis/*`) | Built |
| Machine learning (§3) | Labels, causal features, purged k-fold with embargo, logit / XGBoost / LightGBM / CatBoost, isotonic calibration, Isolation-Forest out-of-distribution veto, causal HMM regimes, abstaining walk-forward, model registry with rollback (`nexus_ai/*`) | Built. **No model passed** (AUC about 0.51). Volatility *is* predictable (AUC 0.76 to 0.81). |
| Strategy selector (§4) | Regime classifier (trend / range / high volatility), selector that needs per-regime out-of-sample evidence, forward results that can only remove a strategy, signal fusion with blockers and a NO_TRADE default (`nexus_decision/*`) | Built. It returns NO_TRADE today because nothing is validated. |
| Execution (§5) | Market / limit / post-only / stop-limit orders; precision and min-notional rounding; client order IDs; a duplicate-order guard (a database unique index); balance reservation; unknown-order reconciliation by client ID (never resubmit); cancel and recovery; restart recovery; live balance reconciliation; a central Binance weight gate with circuit breaker (`nexus_exchange/*`, `nexus_execution/*`, `nexus_common/binance_guard.py`) | Built |
| Risk engine (§6) | Ceilings: 1% per trade, 2% daily, 4% weekly, 10% drawdown, 3 positions. Also: a durable kill switch, persisted risk-state machine, loss-streak lock, correlation limits and sizing from stop distance (`nexus_risk/*`, `nexus_config/settings.py`). | Built. Paper defaults are 0.25% / 1% / 3%. |
| Backtesting (§7) | Next-bar-open fills, fees, volume-aware slippage, partial fills, Binance filters, walk-forward, parameter stability and PBO, Monte Carlo, bootstrap CIs, full metrics including Sortino and recovery factor (`nexus_backtest/*`) | Built |
| 180-day paper (§8) | Paper broker mirroring Binance's refusals, persisted signals / orders / fills / decision log, three running forward tests (carry, taker flow, spot engine) and Crypto Trends starting 24 Nov | Running |
| Dashboard (§9) | 37 pages: connection, markets, signals, candidates, positions, orders, risk, kill switch, strategies, backtests, paper, performance, system health, spot engine, trend test… | Built |

**Bugs found during this audit**

1. **Live/backtest mismatch in the spot engine (fixed).** The live runner loaded 4,000
   15-minute bars, which is only about 250 four-hour bars.
   * The 4h EMA200 therefore depended on where the data started. It was up to 3.5% away
     from the full-history value the backtest used.
   * It now loads 12,000 bars, and opens new positions only after 9,600 bars (three EMA200
     spans). Open positions are still managed without the warm-up.
   * Found with a start-up sensitivity check modelled on Freqtrade's `recursive-analysis`,
     now a unit test.
   * The 16 library strategies were checked the same way (600-bar live window against full
     history) and match exactly.
2. **Binance futures pause message (fixed).** The message showed only the time of day, so a
   multi-day Binance IP ban looked as if it had expired. It now prints the date.

## 2. Global strategy research report

Each project's **source code** was cloned and read: Freqtrade 77ec0c3, Hummingbot 3351ada,
Jesse ddb5a66, NautilusTrader 126bf01 and Backtrader b853d7c. TradingView's Pine Script
documentation was read for repainting and strategy-tester behaviour. Only public, open-source
logic is used, and nothing proprietary. None of these projects claims its strategies are
profitable.

### Freqtrade (`freqtrade/`)
- **Strategy interface.** Indicators, entry and exit are separate; `startup_candle_count`
  declares the warm-up; informative (higher) timeframes are merged one bar late.
- **Protections** (`plugins/protections/`):
  - `StoplossGuard`: stop trading after N stop-losses within a lookback.
  - `MaxDrawdown`: pause when the drawdown within a lookback exceeds X.
  - `LowProfitPairs`: lock a pair whose recent summed profit is below a threshold.
  - `CooldownPeriod`: wait after closing a trade.
  - Each protection works globally or per pair, with a lock duration.
- **Bias tools** (`optimize/analysis/`):
  - `lookahead`: re-run the backtest on data cut at each trade and check that the
    signals are unchanged.
  - `recursive`: check that indicator values do not depend on the start-up length.
- **Exits.** Stoploss, trailing stop, ROI table and exit signal. Hyperopt warns about
  overfitting.

### FreqAI (`freqtrade/freqai/`)
- **Features.** Expanded over periods, timeframes and shifted candles. The model retrains on
  a sliding window (`train_period_days` / `backtest_period_days`), so the backtest is a true
  walk-forward.
- **Recency weights** (`weight_factor`): `w = exp(-i / (weight_factor * n))`, so recent
  rows count more.
- **Outlier handling.** SVM / DBSCAN outlier removal, plus a **Dissimilarity Index**
  (`DI_threshold`): when a new point is far from the training data, `do_predict = 0` and the
  model abstains.

### Hummingbot (`hummingbot/strategy_v2/executors/`)
- **PositionExecutor "triple barrier".** Stop-loss, take-profit, **time limit** and trailing
  stop. The stop-loss and time-limit exits must be market orders, and retries are capped
  (`max_retries`).
- **Other executors.** DCA, grid, TWAP and cross-exchange market making.
- **Order handling.** An in-flight order tracker with client order IDs, a rate-limit
  throttler and a budget checker that confirms funds before submitting.

### Jesse (`jesse/`)
- **Sizing.** `risk_to_qty` sizes from the stop *including fees*; `limit_stop_loss` caps
  the stop distance.
- **Monte Carlo** (`research/monte_carlo`): shuffled trades and perturbed candles.
- **Rule significance test** (`research/rule_significance_testing`): the rule's returns are
  centred and resampled with a **stationary bootstrap** (block length about 10) to get a
  p-value that the edge is not chance (Aronson's method).

### NautilusTrader (`crates/risk/src/engine`)
- **Pre-trade RiskEngine.** It checks price and quantity precision, **max notional per
  order**, **order submit and modify rate limits**, cash balance for sells, and risk
  increases.
- **Trading state.** One of ACTIVE, **REDUCING** (only orders that reduce a position) or
  HALTED.
- **Simulation.** A fill model with `prob_fill_on_limit` and `prob_slippage`. The same code
  runs in backtest and live, and the venue state is reconciled at start-up.

### Backtrader (`backtrader/`)
- **Broker simulation.** Percent or fixed slippage, volume fillers, and warnings that
  cheat-on-open and cheat-on-close are lookahead.
- **Analyzers.** Sharpe, DrawDown, TradeAnalyzer, Calmar, VWR and **SQN** (√N × mean /
  stdev of trade returns, meaningful only from 30 trades).

### TradingView (Pine Script documentation)
- **Repainting.** `request.security(..., lookahead = barmerge.lookahead_on)` without a `[1]`
  offset "will return data from the future on historical bars". Unconfirmed realtime bars,
  `calc_on_every_tick` and `varip` also make historical and live behaviour differ.
- **Strategy tester defaults.** Fills at the next bar's open, **no commission and zero
  slippage**, and an assumed intrabar path.
- **TradingView's own warning.** Repainting "invalidates backtesting results".
- **What NEXUS takes.** Public scripts are unvalidated and most use the cost-free default,
  so NEXUS takes only the rule definitions of standard indicator strategies and re-tests
  them with costs.

## 3. Strategy comparison

| Technique | Source | In NEXUS before | Action |
|---|---|---|---|
| Lookahead check on signals | Freqtrade lookahead-analysis | Yes (prefix-invariance tests on every strategy) | Keep |
| Start-up (recursive) check | Freqtrade recursive-analysis | Indicators only | **Added** for the live window; found bug 1 |
| Protections: stop-loss guard, low-profit pair lock, lookback drawdown, cooldown | Freqtrade | Loss streak and cooldown only | **Add** (S2) |
| Portfolio drawdown limit of 5% (owner's research limit) | Brief §6 | 10% ceiling | **Add** to the new profile (S2) |
| Max notional per order, submit-rate limit, REDUCING state | NautilusTrader | Weight gate, duplicate guard and balance reservation only | **Add** (S3) |
| Time-limit barrier | Hummingbot | Some research engines | **Add** to library exits (S4) |
| Fee-inclusive risk sizing | Jesse | Yes (spot rules) | Keep |
| Stationary-bootstrap significance test | Jesse | Plain bootstrap CIs | **Add** (S4) |
| SQN | Backtrader | No | **Add** (S4) |
| Multiple-testing correction | Research practice | PBO only | **Add** Holm correction across the library (S4) |
| Recency weights, dissimilarity abstain | FreqAI | Isolation-Forest out-of-distribution veto | Optional (S6); the veto is equivalent |
| Standard indicator strategies (EMA, ADX, MACD, RSI, ROC, Donchian, Bollinger, VWAP) | TradingView built-ins, Freqtrade sample | Similar rules, failed on 15m / 1h | **Add** the brief's library on 1h, 4h and 1d with time barriers; test once (S4) |
| Market making / grid / DCA | Hummingbot | No | **Not added.** Grids and DCA average down, which the mandate forbids; market making needs a dedicated IP, low latency and maker rebates |
| Reinforcement learning | FreqAI | No | **Not added.** Overfits easily, and no evidence beats the simpler models that already failed |

**Evidence so far.** On Binance spot after costs:
* Intraday indicator rules (15m / 1h) have failed in every test, 18 rules in total.
* The only edge that survived is slow **daily** trend following (Crypto Trends: Sharpe 1.11
  against 0.74 for buy-and-hold, drawdown halved).
* The new library therefore adds the **4h and 1d** timeframes, where trading costs are small
  relative to moves, instead of re-tuning intraday rules.

## 4. Technical architecture (additions only)

```
nexus_strategies/library_global.py   brief §2 library: 8 independent strategies, each with
                                     its own entry, exit, ATR stop and time barrier
nexus_risk/protections.py            Freqtrade-style protections (pure functions on closed
                                     trades); used by the spot rules and the paper runner
nexus_risk/spot_rules.py             + max_drawdown and the owner's research profile
                                     (0.5% / 1% max / 2% daily / 5% drawdown)
nexus_execution/pretrade.py          Nautilus-style pre-trade checks (max notional, submit
                                     rate, REDUCING) called by OrderManager.place
nexus_backtest/significance.py       stationary bootstrap p-value, SQN, Holm correction
nexus_backtest/library_run.py        one pre-registered run: 8 strategies x 10 pairs x
                                     (1h, 4h, 1d), train / validation / test splits
```

* All additions reuse the existing database, order manager, paper broker, decision log and
  dashboard.
* Nothing in payments or authentication changes.

## 5. Implementation roadmap (stages, each tested before the next)

| Stage | Content | Exit criterion |
|---|---|---|
| S1 | Start-up (recursive) analysis; live warm-up fix | Unit test green ✅ |
| S2 | Protections plus the owner's research risk profile (0.5% / 1% / 2% daily / 5% drawdown) | Unit tests; spot-engine paper keeps its pre-registered 0.25% profile |
| S3 | Pre-trade checks in OrderManager (max notional, submit rate, REDUCING) | Unit and integration tests; all existing tests still pass |
| S4 | Global library, significance tools, pre-registration, one validation run | Pre-registration committed before the run; results appended once |
| S5 | Paper programme for the library: every signal recorded, daily / weekly / monthly reports, API, "Strategy lab" dashboard page (simulated balances labelled) | e2e green; history persists across restarts |
| S6 (optional) | FreqAI recency weights and a volatility-forecast regime input for the selector | Pre-registered; can only reduce risk or abstain |

## 6. Risks and limitations

* **No strategy is guaranteed profitable.** 18 intraday rules and every ML configuration have
  failed. The likeliest result for an indicator library is failure again, and it will be
  reported as such.
* **Multiple testing.** 8 strategies × 3 timeframes is 24 tests. At a 5% level about one
  "pass" is expected by chance, so the Holm correction and a held-out test split are
  mandatory.
* **Data limits.** No historical order-book data, so spread and partial fills are modelled
  from bars. Volume profile is not reliable from bar data and is not used for entries.
* **Shared IP.** Render's shared egress IP can be banned by other tenants' traffic, which
  creates gaps in futures data. Spot paper uses WebSocket data and is less affected. A
  dedicated IP is an owner decision.
* **Risk profile.**
  * The owner's 0.5% per trade and 2% daily loss are within the ceilings (1% and 2%), so
    they apply to the new library's paper programme.
  * The weekly ceiling stays 4%.
  * The 5% portfolio drawdown is tighter than the 10% ceiling, and is enforced.
* **180 days means 180 calendar days of recorded paper data.** No report will claim it
  earlier.
* **Live trading, API permissions, withdrawals and real orders stay off.** Each needs the
  owner's separate explicit authorisation.
