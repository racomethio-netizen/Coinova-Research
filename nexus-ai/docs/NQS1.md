# NQS-1: adaptive forex strategy

NQS-1 trades the five forex majors (EURUSD, GBPUSD, USDJPY, AUDUSD, USDCHF). A regime
classifier decides which setup is allowed, and it refuses to trade when the market is
unclear.

**Status: tested 2026-11-27, FAILED in all three profiles** (pre-registered, on 11
years of Exness's own tick data). It stays in research and places no orders, live or
demo.
* Scalping: −0.04R a trade.
* Intraday: −0.01R a trade.
* Swing: −0.15R a trade.

Details: `docs/research/2026-11-27-nqs1-report.md` and the results section of the
pre-registration.

## Where the code is

| Part | File |
|---|---|
| Settings (indicators, profiles, regimes, exits, costs, NEXUS risk) | `services/strategies/nexus_strategies/nqs1/config.py` |
| Indicators, timeframe resampling, higher-timeframe view without look-ahead | `nqs1/features.py` |
| Seven regimes and regime → setup selection | `nqs1/regime.py` |
| The four setups and the carry rate | `nqs1/strategies.py` |
| Decision frame, adaptive selection, the portfolio simulator (entries, sizing, limits, exits, carry) | `nqs1/engine.py` |
| Optional ML veto | `nqs1/ml.py` |
| Walk-forward test, Monte Carlo, sensitivity, comparison, report | `research/backtesting/nexus_backtest/nqs1_bt.py` |
| Tests on synthetic data | `tests/unit/test_nqs1.py` |
| Pre-registration (rules, gates, decision) | `docs/research/2026-11-27-nqs1-preregistration.md` |

## How a trade happens

1. **Every bar close:** the indicators and the regime are computed from closed bars
   only.
   * The higher timeframe is used only after its bar has closed.
2. **Regime selection:**

   | Regime | Allowed setup |
   |---|---|
   | STRONG_BULL / STRONG_BEAR | trend, in the trend's direction only |
   | VOL_EXPANSION | breakout |
   | VOL_CONTRACTION | breakout, needing 3 confirmations |
   | SIDEWAYS | mean reversion |
   | WEAK_TREND, UNSTABLE, INSUFFICIENT_DATA | none (NO_TRADE) |
3. **Confirmations:** at least two independent ones; never a single indicator.
   Time-series momentum adds one when it agrees.
4. **Entry checks, in order:**
   * NEXUS halts: drawdown, daily and weekly loss;
   * already in the pair; position count;
   * trading session;
   * spread compared with volatility;
   * cost compared with the stop;
   * reward of at least 1.5R;
   * expected value after costs;
   * exposure and open-risk limits;
   * lot size;
   * the optional ML veto.
5. **Fill and exit:**
   * the order fills at the next bar's open;
   * exits: stop, take profit, break-even, trailing stop, tighter stop in unstable
     markets, opposite signal, time limit, and emergency close at the drawdown halt.

## Risk (shared with the rest of NEXUS)

* **Limits.** NQS-1 reads the central defaults from `nexus_config.settings`:
  * 0.25% risk per trade;
  * a 1% daily loss limit and a 3% weekly one;
  * a 5% drawdown halt;
  * at most 3 positions and 1% aggregate open risk.
* **Exposure.** On top of these, it uses at most 1× equity in total notional and 0.6×
  in any one currency (the NEXUS 1× leverage ceiling).
* **Exposure counts pending orders**, so setups decided on the same bar share the limits.
* **What it never does:**
  * raise a limit;
  * average down, use martingale sizing or grids;
  * let the ML model enlarge a trade or bypass a check.

**Small accounts:** a position is rounded down to 0.01 lot (1,000 units). On a balance
of a few hundred dollars, 0.25% risk is often smaller than one 0.01 lot with an ATR
stop, so many trades would be refused as "below the minimum lot". That is the risk
limit working, not a fault.

## Running the test

1. **Deploy, so NEXUS can receive the bars.** The history endpoint and its database
   table (migration 0023) arrive with the next deploy.
2. **Export history once from your Windows PC.** Steps:
   * In MT5, set Tools → Options → Charts → "Max. bars in chart" to *Unlimited* and
     restart the terminal.
   * Run `py -3 nexus_mt5_bridge.py --export-history` while the forex market is open.
   * The dashboard (All markets → Exness bridge → Price history) shows what arrived.
3. **Run the test** (in the research environment), then fetch the series as CSV files
   from `GET /api/v1/research/history/{PAIR}/{TF}.csv`. The command is
   `python -m nexus_backtest.nqs1_bt --data <folder> --fred <fred folder> --out results/nqs1_<date>.json --commit <sha>`.
   * It writes the JSON result and a markdown comparison report.

## Integration and next steps

* NQS-1 uses NEXUS's central risk settings and runs inside the research harness.
* The forex market is capped at RESEARCH in NEXUS: there is no forex order connector,
  and live forex trading is not available.
* **If a profile passes its pre-registered gates:**
  * a paper runner on the Exness **demo** account comes next, with live orders disabled
    and at least 3 months and 100 trades;
  * any live use needs the owner's explicit authorization for the forex market.
* **If nothing passes:** NQS-1 stays in research, and the result is published as it is.
