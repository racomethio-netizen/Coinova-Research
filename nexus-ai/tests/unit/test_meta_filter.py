"""The ML trade filter on synthetic data: features never look ahead, training is in time
order with purged calibration, the filter helps when a feature truly predicts the
outcome, and does nothing useful on noise."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from nexus_ai.meta_filter import FEATURES, MetaFilter, bar_features, signal_features
from nexus_backtest import ml_filter_bt as mf
from nexus_strategies import modules as mods

sys.path.insert(0, str(Path(__file__).parent))
from test_strategy_modules import fx_m5


def test_bar_features_never_look_ahead():
    m5 = fx_m5(days=120)
    full = bar_features(mods.fx_frames(m5), "5m")
    cut = m5.index[int(len(m5) * 0.7)]
    part = bar_features(mods.fx_frames(m5[m5.index < cut]), "5m")
    known = full[full.index + pd.Timedelta(minutes=5) <= cut]
    part = part[part.index + pd.Timedelta(minutes=5) <= cut]
    pd.testing.assert_frame_equal(known, part, check_freq=False)
    assert known.tail(200).notna().all().all()


def test_signal_features_add_the_trade_terms():
    m5 = fx_m5(days=60)
    frames = mods.fx_frames(m5)
    sig = mods.get("fx_scalp_pullback").signals(frames, mods.get("fx_scalp_pullback").config)
    x = signal_features(bar_features(frames, "5m"), sig, frames["5m"]["close"],
                        frames["5m"]["spread"], 2)
    assert list(x.columns) == list(FEATURES) and len(x) == len(sig)
    assert set(x["side"].unique()) <= {1.0, -1.0} and (x["risk_atr"] >= 1.0 - 1e-9).all()
    assert (x["cost_risk"] <= 0.15 + 1e-9).all() and (x["symbol_code"] == 2).all()


def synthetic(n: int = 4000, signal: bool = True, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    x = pd.DataFrame(rng.normal(size=(n, len(FEATURES))), columns=list(FEATURES))
    good = x["adx"] > 0.3 if signal else pd.Series(rng.random(n) < 0.35)
    win = rng.random(n) < np.where(good, 0.6, 0.2)
    x["net_r"] = np.where(win, 1.9, -1.05)
    x["entry"] = pd.date_range("2016-01-04", periods=n, freq="20h", tz="UTC")
    x["exit"] = x["entry"] + pd.Timedelta(hours=3)
    return x


def test_filter_learns_a_real_edge_and_calibrates():
    df = synthetic()
    tr, te = df.iloc[:3000], df.iloc[3000:]
    f = MetaFilter().fit(tr[list(FEATURES)], (tr["net_r"] > 0).to_numpy(int), tr["entry"],
                         tr["exit"])
    p = f.probability(te[list(FEATURES)])
    assert f.info["fit_rows"] + f.info["calibration_rows"] + f.info["purged"] == 3000
    assert 0 <= p.min() and p.max() <= 1
    allowed = te["net_r"][f.allows(te[list(FEATURES)])]
    assert allowed.mean() > te["net_r"].mean() + 0.3          # the filter picks the edge


def test_filter_on_noise_finds_nothing():
    df = synthetic(signal=False, seed=3)
    tr, te = df.iloc[:3000], df.iloc[3000:]
    f = MetaFilter().fit(tr[list(FEATURES)], (tr["net_r"] > 0).to_numpy(int), tr["entry"],
                         tr["exit"])
    from sklearn.metrics import roc_auc_score
    auc = roc_auc_score((te["net_r"] > 0).to_numpy(int), f.probability(te[list(FEATURES)]))
    assert abs(auc - 0.5) < 0.08


def test_walk_forward_trains_only_on_closed_trades(monkeypatch: pytest.MonkeyPatch):
    df = synthetic(n=3500)
    seen: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    real_fit = MetaFilter.fit

    def spy(self, x, y, entry, exit_, **kw):
        seen.append((exit_.max(), entry.max()))
        return real_fit(self, x, y, entry, exit_, **kw)

    monkeypatch.setattr(MetaFilter, "fit", spy)
    rows = [{"trade": {"symbol": "EURUSD", "side": 1, "signal_time": int(e.value),
                       "entry_time": int(e.value), "entry": 1.0, "stop": 0.99,
                       "target": 1.02, "risk": 0.01, "exit_time": int(z.value), "exit": 1.0,
                       "reason": "time", "net_r": float(r), "bars": 3, "carry": 0.0},
             "double": None, "features": {k: float(df.at[i, k]) for k in FEATURES}}
            for i, (e, z, r) in enumerate(zip(df["entry"], df["exit"], df["net_r"],
                                              strict=True))]
    res = mf.evaluate_module("fx_scalp_pullback", rows, df["exit"].max())
    years = [f["year"] for f in res["folds"] if "train" in f]
    assert years and len(seen) == len(years)
    for (last_exit, _), year in zip(seen, years, strict=True):
        assert last_exit < pd.Timestamp(f"{year}-01-01", tz="UTC")
    assert res["metrics"]["mean_r"] > res["unfiltered_same_years"]["mean_r"]
