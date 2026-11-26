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
