"""NQS-1 parameters. Indicator settings are the owner's specification; everything else is
a documented default, fixed before any test (see the NQS-1 pre-registration)."""

from __future__ import annotations

from dataclasses import dataclass, field

from nexus_config.settings import (
    DEFAULT_DAILY_LOSS,
    DEFAULT_DRAWDOWN,
    DEFAULT_OPEN_POSITIONS,
    DEFAULT_OPEN_RISK,
    DEFAULT_RISK_PER_TRADE,
    DEFAULT_WEEKLY_LOSS,
)

PAIRS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF")


@dataclass(frozen=True)
class Indicators:
    ema_fast: int = 20
    ema_mid: int = 50
    ema_slow: int = 200
    rsi: int = 14
    macd: tuple[int, int, int] = (12, 26, 9)
    adx: int = 14
    atr: int = 14
    donchian: int = 20
    bb: int = 20
    bb_k: float = 2.0
    momentum: int = 60              # rolling momentum, bars of the signal timeframe
    vol_pct_window: int = 250       # window for the volatility and band-width percentiles


@dataclass(frozen=True)
class Profile:
    """A trading horizon: the timeframe that generates signals and the higher one that
    confirms the trend. Lower timeframes are not used for entries (no tick data)."""
    name: str
    signal_tf: str
    trend_tf: str
    max_hold_bars: int
    tsmom_lookback: int


PROFILES = {
    "scalping": Profile("scalping", "M5", "M15", 36, 96),
    "intraday": Profile("intraday", "H1", "H4", 48, 120),
    "swing": Profile("swing", "D1", "W1", 30, 126),
}


@dataclass(frozen=True)
class Regime:
    adx_strong: float = 25.0
    adx_weak: float = 18.0
    vol_unstable: float = 0.97      # ATR% percentile at or above which trading stops
    vol_expansion: float = 0.80
    squeeze: float = 0.15           # band-width percentile at or below = contraction
    shock_atr: float = 3.0          # a bar range above 3x ATR marks the market unstable


@dataclass(frozen=True)
class Exits:
    stop_atr: float = 1.5           # initial stop distance
    take_profit_r: float = 2.0      # target in multiples of the initial risk
    breakeven_r: float = 1.0        # move the stop to entry after +1R
    trail_start_r: float = 1.5      # start trailing after +1.5R
    trail_atr: float = 2.5          # chandelier distance
    mr_take_profit: str = "mid"     # mean reversion exits at the Bollinger middle


@dataclass(frozen=True)
class Costs:
    """Per pair, in pips: spread, commission round trip and slippage per fill. Defaults
    are conservative assumptions for a retail ECN-style account, to be replaced by the
    spreads the Exness bridge reports."""
    spread: dict[str, float] = field(default_factory=lambda: {
        "EURUSD": 0.8, "GBPUSD": 1.2, "USDJPY": 1.0, "AUDUSD": 1.0, "USDCHF": 1.4})
    commission_rt: float = 0.7      # about $7 per standard lot round trip
    slippage: float = 0.2
    max_spread_atr: float = 0.15    # refuse a trade if spread exceeds 15% of ATR

    def pip(self, pair: str) -> float:
        return 0.01 if pair.endswith("JPY") else 0.0001


@dataclass(frozen=True)
class Risk:
    """NEXUS limits, read from the central configuration (the owner's mandate). The
    engine never exceeds them."""
    risk_per_trade: float = float(DEFAULT_RISK_PER_TRADE)
    daily_loss: float = float(DEFAULT_DAILY_LOSS)
    weekly_loss: float = float(DEFAULT_WEEKLY_LOSS)
    max_drawdown: float = float(DEFAULT_DRAWDOWN)
    max_open: int = DEFAULT_OPEN_POSITIONS
    max_open_risk: float = float(DEFAULT_OPEN_RISK)
    max_gross_exposure: float = 1.0  # total notional <= 1x equity
    max_currency_exposure: float = 0.6  # net notional in any one currency <= 0.6x equity


@dataclass(frozen=True)
class NQS1Config:
    ind: Indicators = Indicators()
    regime: Regime = Regime()
    exits: Exits = Exits()
    costs: Costs = Costs()
    risk: Risk = Risk()
    min_confirmations: int = 2      # never enter on one indicator alone
    min_target_r: float = 1.5
    max_cost_r: float = 0.15        # round-trip cost at most 15% of the risk distance
    lot_units: float = 1000.0       # broker lot step: 0.01 standard lot = 1,000 units
