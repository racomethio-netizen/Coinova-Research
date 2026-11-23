"""Spot strategy engine: risk rules, position management, regime, strategies, backtester."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from nexus_risk.spot_rules import (
    BookState,
    Position,
    SpotRiskConfig,
    entry_checks,
    manage_bar,
    size,
)

NOW = datetime(2026, 1, 5, tzinfo=UTC)
CFG = SpotRiskConfig()


def book(**kw: object) -> BookState:
    st = BookState(10_000, 10_000, [], 10_000, 10_000)
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def pos(symbol: str = "BTCUSDT", entry: float = 100, stop: float = 98, qty: float = 1) -> Position:
    return Position(symbol, "trend_pullback", qty, entry, stop, stop, entry + 3, entry + 6, NOW,
                    highest=entry)


# ---------------------------------------------------------------- sizing and limits
def test_size_from_stop_distance_not_fixed() -> None:
    q1, _ = size(10_000, 10_000, 100, 98, CFG)
    q2, _ = size(10_000, 10_000, 100, 96, CFG)
    # Wider stop, smaller size: risk per unit = stop distance + both fees.
    assert q2 == pytest.approx(25 / (4 + 100 * 0.001 + 96 * 0.001), rel=1e-6) and q2 < q1
    # 0.25% of 10,000 = 25 at risk, including both fees.
    per_unit = 2 + 100 * 0.001 + 98 * 0.001
    assert q1 == pytest.approx(min(25 / per_unit, 0.20 * 10_000 / 100), rel=1e-6)


def test_size_caps_allocation_cash_step_and_min_notional() -> None:
    q, _ = size(10_000, 10_000, 100, 99.99, CFG)            # tiny stop -> allocation cap
    assert q * 100 <= 0.20 * 10_000 + 1e-6
    q, _ = size(10_000, 50, 100, 98, CFG)                    # no leverage: cash cap
    assert q * 100 * 1.001 <= 50 + 1e-9
    q, _ = size(10_000, 10_000, 100, 98, CFG, step=1.0)
    assert q == int(q)
    q, why = size(1_000, 1_000, 100, 98, CFG, min_notional=500)
    assert q == 0 and "minimum notional" in (why or "")


def test_weekly_ceiling_enforced() -> None:
    with pytest.raises(ValueError, match="ceiling"):
        SpotRiskConfig(max_weekly_drawdown=0.05)
    with pytest.raises(ValueError):
        SpotRiskConfig(risk_per_trade=0.01)


def test_entry_checks() -> None:
    assert entry_checks(book(), "SOLUSDT", 100, 98, NOW, CFG) == []
    assert "daily drawdown limit reached" in entry_checks(book(equity=9_790), "X", 100, 98,
                                                          NOW, CFG)
    assert "weekly drawdown limit reached" in entry_checks(
        book(equity=9_590, day_start_equity=9_590), "X", 100, 98, NOW, CFG)
    st = book(cooldown_until=NOW + timedelta(hours=1))
    assert any("cooldown" in r for r in entry_checks(st, "X", 100, 98, NOW, CFG))
    three = [pos("A"), pos("B"), pos("C")]
    assert "maximum open positions" in entry_checks(book(positions=three), "X", 100, 98, NOW, CFG)
    assert any("averaging" in r for r in entry_checks(book(positions=[pos("X")]), "X", 100, 98,
                                                       NOW, CFG))
    # Open risk: two positions risking 40 each (0.8%) + new 25 (0.25%) > 1%.
    big = [pos("A", 100, 60, 1), pos("B", 100, 60, 1)]
    assert "aggregate open risk limit" in entry_checks(book(positions=big), "X", 100, 98, NOW, CFG)
    corr = book(positions=[pos("BTCUSDT"), pos("ETHUSDT")],
                correlated={"SOLUSDT": {"BTCUSDT", "ETHUSDT", "SOLUSDT"}})
    assert "correlated positions limit" in entry_checks(corr, "SOLUSDT", 100, 98, NOW, CFG)


# ---------------------------------------------------------------- position management
def test_stop_first_and_gap_fills_at_open() -> None:
    p = pos()
    assert manage_bar(p, 99, 110, 97, 105, 1.0, CFG)[0].kind == "stop"   # stop before TPs
    p = pos()
    f = manage_bar(p, 95, 96, 94, 95, 1.0, CFG)[0]
    assert f.kind == "stop" and f.price == 95                          # gap: the open


def test_tp1_partial_then_breakeven_including_fees_never_widen() -> None:
    p = pos()
    fills = manage_bar(p, 101, 103.5, 100.5, 103, 1.0, CFG)
    assert [f.kind for f in fills] == ["tp1"] and fills[0].qty == 0.5 and p.qty == 0.5
    be = 100 * 1.001 / 0.999
    assert p.stop == pytest.approx(be) and p.stop > p.entry               # covers fees
    old = p.stop
    manage_bar(p, 103, 103.2, 102.9, 103, 1.0, CFG)
    assert p.stop >= old                                                  # never lower
    assert manage_bar(p, 103, 106.5, 102.9, 106, 1.0, CFG)[0].kind == "tp2"


def test_trailing_after_tp1() -> None:
    cfg = SpotRiskConfig(trailing_atr=1.0)
    p = pos()
    manage_bar(p, 101, 103.5, 100.5, 103, 1.0, cfg)
    manage_bar(p, 103, 105, 103, 105, 1.0, cfg)
    assert p.stop == pytest.approx(104.0)


# ---------------------------------------------------------------- regime and strategies
def _m15(n: int = 6000, drift: float = 0.0004, seed: int = 0, vol: float = 0.004) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="15min", tz="UTC")
    c = 100 * np.exp(np.cumsum(drift + rng.normal(0, vol, n)))
    o = np.concatenate([[c[0]], c[:-1]])
    hi = np.maximum(o, c) * (1 + np.abs(rng.normal(0, vol / 2, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, vol / 2, n)))
    return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c,
                         "volume": rng.uniform(50_000, 150_000, n)}, index=idx)


def test_regime_labels_uptrend_downtrend_and_liquidity() -> None:
    from nexus_backtest.spot_engine import resample
    from nexus_strategies.spot_regime import RegimeConfig, classify_spot

    up = _m15(drift=0.0006)
    reg = classify_spot(up, "15m", resample(up, "1h", 4), resample(up, "4h", 16), None,
                        is_btc=True, cfg=RegimeConfig(max_atr_pct=1.0))
    tail = reg["regime"].dropna().iloc[-500:]
    assert (tail == "TRENDING_UP").mean() > 0.5
    down = _m15(drift=-0.0006)
    reg = classify_spot(down, "15m", resample(down, "1h", 4), resample(down, "4h", 16), None,
                        is_btc=True, cfg=RegimeConfig(max_atr_pct=1.0))
    assert (reg["regime"].dropna().iloc[-500:] == "TRENDING_DOWN").mean() > 0.5
    thin = up.assign(volume=1.0)
    reg = classify_spot(thin, "15m", resample(thin, "1h", 4), resample(thin, "4h", 16), None,
                        is_btc=True)
    assert set(reg["regime"].dropna().unique()) == {"LOW_LIQUIDITY"}


def test_btc_risk_off_blocks_alts() -> None:
    from nexus_backtest.spot_engine import resample
    from nexus_strategies.spot_regime import RegimeConfig, classify_spot

    alt, btc = _m15(drift=0.0006, seed=1), _m15(drift=-0.0006, seed=2)
    reg = classify_spot(alt, "15m", resample(alt, "1h", 4), resample(alt, "4h", 16),
                        resample(btc, "4h", 16), is_btc=False, cfg=RegimeConfig(max_atr_pct=1.0))
    assert (reg["regime"].dropna().iloc[-500:] == "BTC_RISK_OFF").mean() > 0.5


def test_strategies_are_causal_and_explain_rejections() -> None:
    from nexus_backtest.spot_engine import resample
    from nexus_strategies.spot_regime import classify_spot
    from nexus_strategies.spot_strategies import breakout_retest, trend_pullback

    m = _m15(drift=0.0003, seed=4)

    def run(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        h1, h4 = resample(df, "1h", 4), resample(df, "4h", 16)
        reg = classify_spot(df, "15m", h1, h4, None, is_btc=True)
        reg1 = classify_spot(h1, "1h", h1, h4, None, is_btc=True)
        return trend_pullback(df, h1, reg), breakout_retest(h1, reg1)

    tp_full, bo_full = run(m)
    tp_part, bo_part = run(m.iloc[:4000])
    cut = tp_part.index[-1]
    cols = ["candidate", "entry", "reason"]
    pd.testing.assert_frame_equal(tp_full.loc[:cut, cols], tp_part[cols])
    cut1 = bo_part.index[-1]
    pd.testing.assert_frame_equal(bo_full.loc[:cut1, cols], bo_part[cols])
    cands = tp_full[tp_full.candidate]
    assert len(cands) > 0
    rejected = cands[~cands.entry]
    assert rejected["reason"].notna().all()            # every rejection is explained
    assert (tp_full.loc[tp_full.entry, "tp2"] > tp_full.loc[tp_full.entry, "tp1"]).all()


# ---------------------------------------------------------------- backtester
def test_backtester_reproducible_and_no_look_ahead() -> None:
    from nexus_backtest.spot_engine import EngineConfig, SymbolRules, run

    data = {"BTCUSDT": _m15(drift=0.0004, seed=5), "ETHUSDT": _m15(drift=0.0004, seed=6)}
    rules = {s: SymbolRules(step=0.0001) for s in data}
    start = data["BTCUSDT"].index[3000]
    end = data["BTCUSDT"].index[-1]
    a = run(data, rules, EngineConfig(), start=start, end=end)
    b = run(data, rules, EngineConfig(), start=start, end=end)
    assert a.equity.equals(b.equity) and a.trades == b.trades          # seeded, reproducible
    cut = data["BTCUSDT"].index[5000]
    short = {s: d.loc[:cut] for s, d in data.items()}
    c = run(short, rules, EngineConfig(), start=start, end=cut)
    early = [t for t in a.trades if t["exit_time"] < cut - pd.Timedelta(days=1)]
    assert early == [t for t in c.trades if t["exit_time"] < cut - pd.Timedelta(days=1)]
    entries = [o for o in a.orders if o.get("kind") == "entry"]
    assert entries and all(o["time"] >= o["decided"] for o in entries)   # never before
    for t in a.trades:
        assert t["fees"] > 0
