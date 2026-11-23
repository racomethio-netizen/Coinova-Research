# Spot strategy engine: audit and technical plan (2026-11-23)

Brief: a professional Binance SPOT strategy engine (Trend Pullback, Breakout + Retest), a
market-regime engine, risk and position management, realistic simulation, backtests,
paper trading and a dashboard. **Live trading stays disabled.** Nothing here can enable it.

## Audit: what already exists (reused, not replaced)

| Need | Existing component |
|---|---|
| Binance spot data: REST klines, WebSocket candles and book, symbol filters | `nexus_market.runner`, `binance_ws`, `ccxt_adapter.load_markets` (tick, step, min notional) |
| Rate limits and bans | `nexus_common.binance_guard` (central gate) |
| Data quality: gaps, duplicates, staleness, closed bars only, higher-timeframe alignment | `derived.quality_report`, unique candle key, `frames.align_higher`, staleness to NO_NEW_TRADES |
| Indicators | `indicators.py`, `ta.py` (EMA, RSI, ADX, ATR, relative volume, range state, breakout/retest) |
| Market structure | `nexus_analysis.structure.market_structure`, `levels.support_resistance` |
| Sizing from stop distance, fees and slippage | `nexus_risk.sizing.size_position` |
| Durable risk states, kill switch, loss streak | `nexus_risk.state`, `kill_switch`, `streak` |
| Paper execution on real books (latency, partial fills, outages, stale books) and Binance order rules | `nexus_execution.paper`, `brokers.PaperBroker` |
| Order lifecycle (intent first, idempotent, reconciled by client ID) | `nexus_execution.order_manager` |
| Backtests: walk-forward, Monte Carlo, slippage model, stability, breakdowns | `nexus_backtest` |
| Live gates (2FA, typed phrase, owner approval) | `nexus_execution.live_gate`, never set by code |

## Gaps: what is built

1. **Regime engine** `nexus_strategies/spot_regime.py`: TRENDING_UP, TRENDING_DOWN,
   RANGING, HIGH_VOLATILITY, LOW_LIQUIDITY, BTC_RISK_OFF, per closed bar, with a score
   breakdown.
2. **Strategies** `nexus_strategies/spot_strategies.py`:
   * `trend_pullback`: 15m entry, 1h and 4h filters, BTC filter;
   * `breakout_retest_v2`: configurable range, volume multiple and retest.
   * Both are causal, output their reasons, and use the `Strategy` interface.
3. **Spot risk and position rules** `nexus_risk/spot_rules.py`:
   * Limits: 0.25% risk per trade (0.50% maximum), 1% aggregate open risk, 2% daily and
     4% weekly drawdown halts (the brief asks 5% weekly; the hard ceiling is 4%, so 4%
     applies), 3 positions, 20% allocation, 3-loss cooldown, a correlation cluster limit.
   * Exits: TP1 (default 1.5R, partial exit), TP2 (3R), break-even including fees,
     optional ATR trailing. A stop never widens.
4. **Portfolio backtester** `nexus_backtest/spot_engine.py`:
   * the same rules as paper;
   * maker and taker fees, spread, slippage, latency (next-bar open), partial fills by bar
     volume share, rejected orders, step, tick and minimum notional.
5. **Report** `nexus_backtest/spot_report.py`: every metric in the brief, plus monthly and
   per-regime tables, a BTC buy-and-hold benchmark, Monte Carlo and bootstrap confidence
   intervals.
6. **Research data:** Binance 15m archives, checksum-verified, for 10 liquid symbols,
   2022-11 to 2026-11. Fixed default parameters, pre-registered, chronological train,
   validation and test. Parameter sensitivity is reported, never used to optimise.
7. **Paper validation runner** `nexus_execution/spot_paper.py`:
   * a separate paper portfolio, every closed 15m bar;
   * real fills through `PaperBroker` and `OrderManager`;
   * every signal, rejection, order, fill, stop and exit persisted.
8. **API and dashboard:** a "Spot strategy engine" page showing regime, BTC status,
   signals and rejection reasons, levels, size and risk, open positions, P&L, drawdown,
   fees, risk limits, performance, the 180-day validation status and
   **LIVE TRADING DISABLED**.
