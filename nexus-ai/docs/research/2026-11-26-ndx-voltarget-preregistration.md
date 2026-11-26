# Pre-registration: volatility-managed NASDAQ-100 holding (multi-asset Stage 3)

_Committed before any run. Results are appended once below the line; nothing above it
changes afterwards._

## Why

* Moreira & Muir (2017), "Volatility-Managed Portfolios", *Journal of Finance*, found the
  effect first in US stocks: holding less when recent volatility is high raised
  risk-adjusted returns.
* The same rule passed NEXUS's test on BTC/ETH 50/50 (2026-11-26 batch 3).
* This tests it on the NASDAQ-100, the index QQQ, NQ futures and Binance's QQQ perpetual
  track.

## Data

* FRED NASDAQ100 daily closes (price index, no dividends), 1986 onward.
* FRED DTB3 (3-month T-bill), used only for a reported sensitivity.

## Rule

The same as the crypto test, with stock-market volatility.
* **Weight:** min(1, target / σ), where σ is the realised volatility of the last 30 daily
  returns, annualised with √252, using closes up to the previous day.
* **Rebalancing:** at the daily close, only when the held weight (which drifts with the
  price) is more than 10 points from target.
* **Target:** **15%** a year (gated). 10% and 20% are reported, not gated.
* **Cash:** earns **nothing** (conservative). A sensitivity with cash earning the T-bill
  rate is reported, not gated.
* **Costs:** 0.03% per side on traded notional, with a 2× stress.
* **Comparison:** the NASDAQ-100 price index held at 100%.

**Known biases, stated in advance:**
* Neither side gets dividends (about 1% a year). This hurts the hold more, because it is
  always fully invested.
* Cash earning nothing hurts the strategy.

## Window

* **Test window:** 1990-01-01 to 2026-10-31.
* **Halves:** 1990–2007 and 2008 to 2026-10.

## Gates

All must hold.

1. Sharpe higher than holding over the full window.
2. Maximum drawdown at least 10 points smaller than holding's.
3. Net return > 0.
4. Sharpe higher than holding in **both** halves.
5. Gates 1 and 3 still hold at 2× costs.

These are the same gates as the BTC/ETH test.

## Consequence

* **Pass:** RESEARCH signals for the stocks market, then a paper forward test (daily
  closes). Live needs a broker connector and the owner's authorization for stocks.
* **Fail:** recorded and shown in the app.

---

## Results

_(appended after the single run)_

### Results of the single run (2026-11-26, harness commit f7ee0d1)

Output: `research/backtesting/nexus_backtest/results/ndx_voltarget_2026-11.json`. The FRED
file hashes are recorded in it. Historical backtest only; no order was placed.

**PASSED: all 5 gates.**

| NASDAQ-100, 1990-01 to 2026-10 | Per year | Sharpe | Worst drop | Net |
|---|---|---|---|---|
| **Volatility-managed, 15% target** | **+11.5%** | **0.79** | **−44.0%** | +5,376% |
| at 2× costs | +11.4% | 0.79 | −44.0% | |
| Held at 100% (price index) | +14.1% | 0.63 | −82.9% | +12,581% |
| 10% target (reported) | +8.3% | 0.79 | −30.2% | |
| 20% target (reported) | +13.2% | 0.78 | −53.6% | |
| 15%, with cash earning the T-bill (reported) | +12.3% | 0.84 | −39.3% | |

* **By half** (Sharpe, strategy vs held):
  * 1990–2007: 0.71 vs 0.56; worst drop −44% vs −83%.
  * 2008–2026: 0.87 vs 0.72; worst drop −27% vs −50%.
* **Average weight in the index:** 0.73.

**What the pass means.**
* Same as for BTC/ETH: it earns **less** than holding (11.5% vs 14.1% a year), with a
  much smaller worst drop (−44% vs −83%) and a better return per unit of risk.
* It is a calmer way to hold the index, not a trading edge, and it can still lose almost
  half the money.
* Dividends (about 1% a year) are missing on both sides.

**Consequence.**
* The stocks market gets RESEARCH signals and a paper forward test from 2026-11-27
  (daily closes, 10,000 virtual USD).
* Live needs an IBKR (or other) connector and the owner's authorization for stocks.
