"""Batch-3 harness (grid v2, volatility targeting, BTC/ETH pairs): no look-ahead, costs
charged, rules applied as pre-registered, on synthetic data."""

from __future__ import annotations

import hashlib
import io
import math
import zipfile
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from nexus_backtest import batch3 as b3
from nexus_strategies import adaptive_grid as ag


def walk(days: int = 900, vol: float = 0.04, seed: int = 3) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=days, freq="D", tz="UTC")
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0, vol, days))), index=idx)


def test_vol_target_uses_no_future_prices():
    px = walk()
    base = b3.vol_target(px, 0.40)
    changed = px.copy()
    cut = px.index[500]
    changed[changed.index > cut] *= np.linspace(1, 3, int((changed.index > cut).sum()))
    alt = b3.vol_target(changed, 0.40)
    pd.testing.assert_series_equal(base[base.index <= cut], alt[alt.index <= cut])


def test_vol_target_scales_down_in_high_vol_and_charges_costs():
    px = walk(vol=0.08)                         # ~150% annualised: weight ~ 0.4 / 1.5
    r = b3.vol_target(px, 0.40)
    hold = px.pct_change()
    tail = slice(100, None)
    assert r.iloc[tail].std() < 0.5 * hold.iloc[tail].std()
    free = b3.vol_target(px, 0.40, cost=0.0)
    assert (1 + free).prod() > (1 + r).prod()             # costs reduce the result
    assert b3.trades_count(px, 0.40) > 0


def test_vol_target_low_vol_is_fully_invested_with_one_trade():
    px = walk(vol=0.005)                        # ~10% annualised: weight capped at 1
    r = b3.vol_target(px, 0.40, cost=0.0)
    hold = px.pct_change()
    np.testing.assert_allclose(r.iloc[31:].to_numpy(), hold.iloc[31:].to_numpy(),
                               rtol=1e-9, atol=1e-12)
    assert b3.trades_count(px, 0.40) == 1


def test_vol_report_gates_are_computed_per_asset():
    closes = {"BTC": walk(days=2500, seed=1), "ETH": walk(days=2500, seed=2)}
    for s in closes.values():
        s.index = pd.date_range("2019-12-01", periods=len(s), freq="D", tz="UTC")
    rep = b3.vol_report(closes)
    assert set(rep) == {"BTC", "ETH", "BTC_ETH_50_50"}
    for v in rep.values():
        assert set(v["gates"]) == {"sharpe_above_hold", "drawdown_10pts_smaller",
                                   "net_positive", "sharpe_above_hold_both_halves",
                                   "holds_at_2x_costs"}
        assert v["passed"] == all(v["gates"].values())
        assert {"target_30", "target_40", "target_50"} <= set(v)


def test_refine_tick_only_when_coarse():
    f = ag.Filters(Decimal("0.00001"), Decimal("1"), Decimal("1"), Decimal("5"))
    assert b3.refine_tick(f, 0.0022787).price_tick == Decimal("1E-7")
    g = ag.Filters(Decimal("0.01"), Decimal("0.00001"), Decimal("0.00001"), Decimal("5"))
    assert b3.refine_tick(g, 8893.03).price_tick == Decimal("0.01")


def _zip(path: Path, rows: list[list[float]]) -> None:
    buf = io.StringIO()
    pd.DataFrame(rows).to_csv(buf, header=False, index=False)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(path.stem + ".csv", buf.getvalue())


def test_load_spot15_and_checksums(tmp_path: Path):
    t0 = int(pd.Timestamp("2020-01-01", tz="UTC").timestamp() * 1000)
    rows = [[t0 + i * 900_000, 1 + i, 2 + i, 0.5 + i, 1.5 + i, 10] for i in range(8)]
    _zip(tmp_path / "BTCUSDT-15m-2020-01.zip", rows)
    df = b3.load_spot15(tmp_path, "BTC")
    assert len(df) == 8 and df.index[0] == pd.Timestamp("2020-01-01", tz="UTC")
    assert b3.hourly(df)["close"].tolist() == [4.5, 8.5]
    z = tmp_path / "BTCUSDT-15m-2020-01.zip"
    (tmp_path / (z.name + ".CHECKSUM")).write_text(
        hashlib.sha256(z.read_bytes()).hexdigest() + "  " + z.name)
    assert b3.verify_checksums(tmp_path)["verified"] == 1
    (tmp_path / (z.name + ".CHECKSUM")).write_text("0" * 64 + "  " + z.name)
    assert b3.verify_checksums(tmp_path)["mismatched"] == [z.name]


def _pair_data(monkeypatch: pytest.MonkeyPatch, *, funding: float) -> None:
    rng = np.random.default_rng(7)
    idx = pd.date_range("2023-11-01", "2024-04-01", freq="15min", tz="UTC", inclusive="left")
    n = len(idx)
    lb = np.log(30_000) + np.cumsum(rng.normal(0, 0.001, n))
    e = np.zeros(n)
    for i in range(1, n):                         # stationary spread, half-life ~ 8 bars
        e[i] = 0.98 * e[i - 1] + rng.normal(0, 0.002)
    la = np.log(2_000) + 0.9 * (lb - lb[0]) + e
    f_idx = pd.date_range("2023-11-01", "2024-04-01", freq="8h", tz="UTC", inclusive="left")

    def fake(_: Path, sym: str):
        lp = la if sym == "ETHUSDT" else lb
        m15 = pd.DataFrame({"open": np.exp(lp), "high": np.exp(lp), "low": np.exp(lp),
                            "close": np.exp(lp), "volume": 1.0}, index=idx)
        return m15, None, pd.Series(funding, index=f_idx)

    monkeypatch.setattr(b3.bt, "load_futures", fake)
    monkeypatch.setattr(b3, "PAIR_END", pd.Timestamp("2024-04-01", tz="UTC"))


def test_pairs_trades_cointegrated_spread_and_stress_pays_more(monkeypatch):
    _pair_data(monkeypatch, funding=0.0)
    runs = b3.pairs(Path("unused"))
    base, stress = runs[1.0], runs[2.0]
    assert base["trades"] > 5 and base["cointegrated_refits"] > 0
    assert stress["trades"] == base["trades"]            # same signals, higher charges
    assert stress["fees"] == pytest.approx(2 * base["fees"])
    assert stress["stats"]["net_return"] < base["stats"]["net_return"]
    assert base["funding"] == 0.0


def test_pairs_funding_is_charged_on_both_legs(monkeypatch):
    _pair_data(monkeypatch, funding=0.0)
    free = b3.pairs(Path("unused"))[1.0]
    _pair_data(monkeypatch, funding=0.001)
    paid = b3.pairs(Path("unused"))[1.0]
    assert paid["trades"] == free["trades"]
    # Long ETH / short ~0.9x BTC at gross 1x: net long notional pays positive funding,
    # short-spread trades receive it; either way funding changes the result.
    assert paid["funding"] != 0.0
    assert not math.isclose(paid["stats"]["net_return"], free["stats"]["net_return"])


def _sideways_then_crash() -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(4)
    n_side, n_fall = 60 * 96, 20 * 96
    x = np.zeros(n_side + n_fall)
    for t in range(1, len(x)):
        drift = -0.0012 if t >= n_side else 0.0
        x[t] = (x[t - 1] * (0.995 if t < n_side else 1.0) + drift
                + rng.normal(0, 0.0015))
    c = 60_000 * np.exp(x)
    o = np.r_[c[0], c[:-1]]
    hi = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.0015, len(x))))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.0015, len(x))))
    idx = pd.date_range("2023-11-20", periods=len(x), freq="15min", tz="UTC")
    m15 = pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c, "volume": 50.0},
                       index=idx)
    return m15, b3.hourly(m15)


def test_grid_v2_closes_inventory_on_regime_break_and_v1_does_not():
    m15, h1 = _sideways_then_crash()
    job = {"market": "spot", "symbol": "BTCUSDT", "m15": m15, "h1": h1,
           "params": b3.bt.DEFAULT, "alloc": Decimal("1000"),
           "filters": ag.Filters(Decimal("0.01"), Decimal("0.00001"), Decimal("0.00001"),
                                 Decimal("5"))}
    v1 = b3.bt.run_symbol(job)
    v2 = b3.bt.run_symbol({**job, "close_on_regime_break": True})
    assert v1["regime_closes"] == 0
    assert v2["regime_closes"] >= 1
    closes = [t for t in v2["trips"] if t["regime_close"]]
    assert closes and not any(t["stop"] for t in closes)
