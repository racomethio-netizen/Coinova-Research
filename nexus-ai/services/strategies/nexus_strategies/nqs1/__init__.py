"""NQS-1: adaptive, regime-aware forex strategy framework (research and paper only).

Modules:
* ``config``     indicator parameters, timeframe profiles, pair cost assumptions.
* ``features``   EMA 20/50/200, RSI 14, MACD 12/26/9, ADX 14, ATR 14, Donchian 20,
                 Bollinger 20/2, rolling momentum, volatility percentile.
* ``regime``     seven-class market regime per bar.
* ``strategies`` time-series momentum, multi-timeframe trend, volatility breakout,
                 mean reversion; carry analysis as a cost and filter.
* ``engine``     regime-based selection, multi-confirmation entries, expected value
                 after costs, exits (ATR stop, take profit, break-even, trailing, time,
                 opposite signal, emergency) and NEXUS risk sizing.
* ``ml``         optional chronological signal-quality veto (never adds risk).

Nothing here places orders. Execution, if ever approved, goes through NEXUS's order
path with its central risk controls.
"""
