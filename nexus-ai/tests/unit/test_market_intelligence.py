"""Market intelligence snapshot (``nexus_analysis.intelligence``) for forex and crypto
on synthetic bars: FX sessions, daily VWAP anchoring, the regime label, and a complete
snapshot from closed bars."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from nexus_analysis import intelligence as intel
from nexus_strategies import modules as mods

sys.path.insert(0, str(Path(__file__).parent))
from test_strategy_modules import crypto_regimes, fx_m5


def test_fx_sessions_and_weekend():
    at = lambda d, h: datetime(2026, 11, d, h, 0, tzinfo=UTC)  # noqa: E731
    assert intel.fx_session(at(25, 3)) == "Asia-Pacific"
    assert intel.fx_session(at(25, 9)) == "London"
    assert intel.fx_session(at(25, 13)) == "London / New York overlap"
    assert intel.fx_session(at(25, 18)) == "New York"
    assert intel.fx_session(at(28, 12)) == "closed"                  # Saturday


def test_daily_vwap_resets_each_day():
    idx = pd.date_range("2026-11-24 21:00", periods=4, freq="1h", tz="UTC")
    df = pd.DataFrame({"high": [2.0, 4, 6, 8], "low": [2.0, 4, 6, 8], "close": [2.0, 4, 6, 8],
                       "volume": [1.0, 1, 1, 1]}, index=idx)
    fx = intel.daily_vwap(df, "forex")           # FX day starts at 22:00 UTC
    assert list(fx) == [2.0, 4.0, 5.0, 6.0]
    cr = intel.daily_vwap(df, "crypto")          # UTC day starts at 00:00
    assert list(cr) == [2.0, 3.0, 4.0, 8.0]


def test_regime_thresholds():
    assert intel.regime(0.8, 0.95) == "HIGH_VOLATILITY"
    assert intel.regime(0.6, 0.5) == "TRENDING_UP"
    assert intel.regime(-0.6, 0.5) == "TRENDING_DOWN"
    assert intel.regime(0.1, 0.5) == "RANGING"
    assert intel.regime(None, None) is None


def test_snapshots_for_both_markets():
    raw = fx_m5(days=90)
    no_volume = intel.snapshot("EURUSD", "forex", mods.fx_frames(raw), "1h",
                               (raw.index[-1] + pd.Timedelta(hours=1)).to_pydatetime())
    assert no_volume["vwap"]["value"] is None                # no volume: no VWAP, not a guess
    fx = mods.fx_frames(raw.assign(volume=100.0))            # MT5 tick volume
    now = (fx["1h"].index[-1] + pd.Timedelta(hours=1)).to_pydatetime()
    s = intel.snapshot("EURUSD", "forex", fx, "1h", now, spread=0.0001,
                       event_risk={"blocked": False})
    assert s["insufficient_history"] is False and s["regime"] is not None
    assert s["liquidity"]["spread"] == 0.0001 and s["liquidity"]["spread_vs_atr"] > 0
    assert s["vwap"]["value"] is not None and set(s["trend_by_timeframe"]) >= {"5m", "1h"}
    assert s["data_age_minutes"] == 0.0
    cr = mods.crypto_frames(crypto_regimes(days=60))
    now = (cr["1h"].index[-1] + pd.Timedelta(hours=1)).to_pydatetime()
    c = intel.snapshot("BTC/USDT", "crypto", cr, "1h", now)
    assert c["session"] == "24/7" and c["liquidity"]["quote_volume_24h"] > 0
    assert c["levels"]["support"] is None or c["levels"]["support"] < c["price"] * 1.5
    empty = intel.snapshot("GBPUSD", "forex", {}, "1h", now)
    assert empty["insufficient_history"] is True and empty["bars"] == 0
