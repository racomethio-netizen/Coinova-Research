# Adaptive grid system (PAPER ONLY)

Autonomous adaptive grids on dynamically selected Binance spot and USD-M futures markets,
behind one central risk manager. Built 2026-11-26 at the owner's request.

**Simulated orders and balances on live Binance market data. There is no live path.**

## Architecture (reuses existing NEXUS components)

| Part | Module | Notes |
|---|---|---|
| Universe | `nexus_market/universe.py` | `SymbolDiscoveryService`, `LiquidityAnalyzer`, `SpreadAnalyzer`, `VolatilityAnalyzer`, `UniverseRiskFilter`, `SymbolRankingEngine`, `DynamicUniverseEngine`. Two-stage scan, hysteresis, separate spot and futures rules. |
| Regime | `nexus_strategies/grid_regime.py` | 8 classes from closed 1h bars. Volatility thresholds are relative to each symbol's own 30-day median. |
| Engine | `nexus_strategies/adaptive_grid.py` | ATR spacing with a fee floor; support/resistance-bounded range; adaptive density; risk-capped equal sizing; slots with partial fills; spot balances; futures at 1x isolated with funding and liquidation price. |
| Risk | `nexus_risk/grid_risk.py` | `GridRiskManager` covers both accounts and fails closed. `PortfolioAllocator` keeps the accounts independent. |
| Orchestrator | `nexus_execution/adaptive_grid_paper.py` | Inline worker `adaptive-grid`. Persistence is atomic per tick, with rollback. |
| Weight gate | `nexus_common/binance_guard.py` | Whole-market request weights added (ticker/24hr 80/40, bookTicker 4/5, premiumIndex 10). |
| Ledger | migration `0020_adaptive_grid` | `grid_universe_scans` and `adaptive_grid_events`: append-only, RLS, no API-role access, unique `seq`. |
| API | `/api/v1/adaptive-grid`, `/universe`, `/control`, `/limits` | Controls and limits are owner-only and audited. |
| Dashboard | `/adaptive-grid/` | Permanent PAPER banner, controls, regimes, grids, fills, performance, universe, limits, log. |
| Backtest | `nexus_backtest/adaptive_grid_bt.py` | Same engine and regime code on historical 15m bars. Pre-registered. |

## How it decides

1. **Every hour**, the universe is rescanned.
   * **Spot** needs at least 50M USDT of 24h volume, a spread of 5 bps or less, at least
     10k USDT of top-of-book depth, and no abnormal moves.
   * **Futures** needs at least 200M USDT of volume, a spread of 3 bps or less, at least
     180 days listed, and PERPETUAL USDT contracts only.
   * Up to 4 symbols per market are selected.
2. **Every 15 minutes**, each selected symbol's regime is classified from closed hourly
   bars:
   * spot runs a long grid in SIDEWAYS or WEAK_BULL;
   * futures runs a neutral grid in SIDEWAYS, a long grid in WEAK_BULL, a short grid in
     WEAK_BEAR;
   * a strong trend is handed to the NEXUS trend engines;
   * everything else is NO_TRADE.
3. **A grid starts** only with RSI between 30 and 70 and a top-of-book imbalance of at
   most ±0.6.
   * Its allocation is the account capital / 4, capped at 25% of total paper equity.
   * Every entry order is approved by the risk manager. Exits are never blocked.
4. **A grid is suspended** when its regime no longer fits: resting entries are cancelled
   and exits stay.
   * It resumes when the regime returns.
   * It is rebuilt only when flat and after a 6-hour cool-down.
   * A stop-loss closes it and latches until the owner presses Start.

## Paper accounts

* Spot account: `NEXUS_AGRID_SPOT_CAPITAL` (default 5,000 USDT).
* Futures account: `NEXUS_AGRID_FUTURES_CAPITAL` (default 5,000 USDT).
* They are independent: spot cash is never used for futures margin.
* `NEXUS_AGRID_MODE=off` stops the worker. There is no live setting.

## Risk limits (defaults; owner-adjustable only within the ceilings)

| Limit | Default | Ceiling |
|---|---|---|
| Loss if an order fills and its grid stops | 0.25% of equity | 1% |
| Daily loss → no new orders | 1% | 2% |
| Weekly loss → no new orders | 3% | 4% |
| Drawdown → shutdown (closes all, latched) | 5% | 10% |
| Portfolio exposure | 60% | 100% |
| Per-asset exposure | 25% | 100% |
| BTC-correlated cluster (corr ≥ 0.8) | 45% | 100% |
| Futures margin use | 50% | 100% |
| Open simulated orders | 120 | 500 |
| Leverage | 1x | 1x (fixed) |

Martingale and size increases are impossible: every slot has the same size. Each grid's
worst case (all entries filled, then the stop) must be at most 12% of its allocation, or
the grid is refused.

## Controls (dashboard, owner, audited)

* **Start paper trading:** clears a stop or shutdown and retires stopped grids.
* **Pause / Resume:** pausing cancels resting entries and keeps exits.
* **Stop all:** closes every simulated position at the book.
* **Emergency shutdown:** like Stop all, and latched until Start.
* **Risk limits:** accepted only within the ceilings.

## Safety verification

* `test_no_live_order_path` scans the worker for order endpoints, keys, signing, live
  brokers or live mode, and checks it only requests `ticker/bookTicker`, `klines` and
  `premiumIndex`.
* The API test shows that no "go live" action exists. Owner endpoints refuse limits
  above the ceilings.
* The worker uses public market data only and has no access to the encrypted exchange
  credentials.

## Known limitations

* **Fill realism.** Fills are simulated from book tickers every 10 seconds, with a
  trade-through rule and 25% of the displayed quantity. Queue position and hidden
  liquidity are unknown. Prices that cross and recover between two polls are missed
  (conservative for fills; stops are detected at the next poll).
* **Futures maintenance margin.** The rate is a conservative 1% parameter, not
  Binance's per-tier brackets, which need an authenticated endpoint.
* **Futures filters in the backtest.** BTCUSDT and ETHUSDT values are as documented by
  Binance. Production reads live `exchangeInfo`.
* **Risk scope.** Portfolio limits cover the two grid accounts plus the NEXUS kill
  switch. They do not cover the separate paper ledgers of the bot, the spot engine or
  the forward tests.
* **Futures data access.** If Binance futures endpoints are rate-limited or banned from
  the server, the futures market reads as stale and takes no new orders.
