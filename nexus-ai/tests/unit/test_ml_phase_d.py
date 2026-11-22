"""Phase D: labels, purged CV, model candidates, OOD, causal HMM, metrics, research loop."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from nexus_ai.candidates import CalibratedModel, OodDetector, available_families, disagreement
from nexus_ai.cv import label_end_times, purged_kfold
from nexus_ai.evaluation import expected_calibration_error, trading_metrics
from nexus_ai.hmm_regime import HmmRegime
from nexus_ai.labels import direction_labels, forward_returns, triple_barrier, volatility_targets
from sklearn.metrics import roc_auc_score


def bars(n: int = 600, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, 0.002, n)) * close
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + spread,
                         "low": np.minimum(open_, close) - spread, "close": close,
                         "volume": rng.uniform(1, 10, n)}, index=idx)


# ---------------------------------------------------------------- labels
def test_forward_returns_enter_at_next_open_and_are_net_of_costs() -> None:
    df = bars()
    f = forward_returns(df, (4,), round_trip_cost=0.003)
    t = 100
    expected = np.log(df["close"].iloc[t + 4] / df["open"].iloc[t + 1]) - 0.003
    assert f["fwd_4"].iloc[t] == pytest.approx(expected)
    assert f["fwd_4"].iloc[-4:].isna().all()                     # no future, no label


def test_direction_has_a_neutral_band() -> None:
    fwd = pd.Series([0.01, 0.001, -0.002, -0.01, np.nan])
    assert direction_labels(fwd, band=0.003).tolist()[:4] == [1.0, 0.0, 0.0, -1.0]
    assert np.isnan(direction_labels(fwd, band=0.003).iloc[4])


def test_triple_barrier_outcome_and_volatility_targets() -> None:
    df = bars(800)
    lab = triple_barrier(df, horizon=16)
    done = lab.dropna()
    assert set(done["outcome"].unique()) <= {-1.0, 0.0, 1.0}
    assert (done.loc[done.outcome == 1, "label"] == 1).all()
    assert (done.loc[done.outcome != 1, "label"] == 0).all()
    vt = volatility_targets(df, horizon=16, lookback=32)
    r = np.log(df["close"]).diff()
    t = 200
    assert vt["future_vol"].iloc[t] == pytest.approx(r.iloc[t + 1:t + 17].std())
    assert vt["future_vol"].iloc[-16:].isna().all()


# ---------------------------------------------------------------- purged CV
def test_purged_kfold_never_trains_on_overlapping_or_embargoed_samples() -> None:
    idx = pd.date_range("2026-01-01", periods=1000, freq="15min", tz="UTC")
    ends = pd.Series(idx + pd.Timedelta(hours=6), index=idx)
    folds = list(purged_kfold(idx, ends, 4, pd.Timedelta(hours=12)))
    assert len(folds) == 4
    covered = np.concatenate([te for _, te in folds])
    assert sorted(covered.tolist()) == list(range(1000))          # every sample tested once
    for train, test in folds:
        a, b = idx[test[0]], idx[test[-1]]
        b_end = max(b, ends.iloc[test].max())
        tr_s, tr_e = idx[train], ends.iloc[train]
        assert not ((tr_s <= b_end) & (tr_e.to_numpy() >= a)).any()        # no overlap
        late = tr_s[tr_s > b]
        assert (late > b_end + pd.Timedelta(hours=12)).all()               # embargo
        assert len(set(train) & set(test)) == 0


def test_label_end_times_from_positions() -> None:
    idx = pd.date_range("2026-01-01", periods=3, freq="15min", tz="UTC")
    df = pd.DataFrame({"pos": [0, 1, 2], "end_idx": [4, 1, 10]}, index=idx)
    ends = label_end_times(df, pd.Timedelta(minutes=15))
    assert ends.iloc[0] == idx[0] + pd.Timedelta(minutes=75)
    assert ends.iloc[1] == idx[1] + pd.Timedelta(minutes=15)


# ---------------------------------------------------------------- models
def synthetic(n: int = 6000, signal: float = 1.0, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    X = pd.DataFrame(rng.normal(size=(n, 4)), index=idx, columns=["a", "b", "c", "d"])
    logit = signal * X["a"] - 0.5 * signal * X["b"]
    y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    X["label"] = y
    return X


@pytest.mark.parametrize("family", available_families())
def test_every_family_learns_a_planted_signal_and_is_calibrated(family: str) -> None:
    df = synthetic()
    tr, te = df.iloc[:4500], df.iloc[4500:]
    ends = pd.Series(tr.index + pd.Timedelta(hours=1), index=tr.index)
    m = CalibratedModel(family, ["a", "b", "c", "d"]).fit(tr, tr["label"], ends)
    p = m.predict_proba(te)
    assert roc_auc_score(te["label"], p) > 0.7
    assert expected_calibration_error(te["label"].to_numpy(float), p) < 0.08
    assert m.info["n_fit"] < 4500 * 0.8                    # calibration rows held out


def test_disagreement_and_ood_flags() -> None:
    assert disagreement([np.array([0.5, 0.5]), np.array([0.5, 0.9])]).tolist() == \
        pytest.approx([0.0, 0.2])
    df = synthetic(3000)
    det = OodDetector(["a", "b", "c", "d"]).fit(df)
    normal_rate = det.flag(synthetic(1000, seed=5)).mean()
    shifted = synthetic(1000, seed=6)
    shifted[["a", "b", "c", "d"]] += 6.0
    assert normal_rate < 0.05 and det.flag(shifted).mean() > 0.9


def test_hmm_filter_is_causal() -> None:
    df = bars(1500, seed=3)
    X = pd.DataFrame({"ret_16": np.log(df["close"] / df["close"].shift(16)),
                      "rvol_96": np.log(df["close"]).diff().rolling(96).std()}, index=df.index)
    hmm = HmmRegime().fit(X.iloc[:1000])
    full = hmm.filter(X)
    part = hmm.filter(X.iloc[:1200])
    pd.testing.assert_frame_equal(part, full.iloc[:1200])
    assert set(hmm.names) == {"bear", "neutral", "bull"}


def test_trading_metrics_by_hand() -> None:
    m = trading_metrics(pd.Series([0.02, -0.01, 0.01, -0.01]))
    assert m["expectancy"] == pytest.approx(0.0025)
    assert m["profit_factor"] == pytest.approx(1.5)
    assert m["win_rate"] == 0.5
    assert m["max_drawdown"] == pytest.approx(1.00969902 / 1.02 - 1)    # peak 1.02 -> 1.0097
    assert trading_metrics(pd.Series(dtype=float))["trades"] == 0
    p = np.array([0.1, 0.1, 0.9, 0.9])
    assert expected_calibration_error(np.array([0.0, 0.0, 1.0, 1.0]), p) == pytest.approx(0.1)


# ---------------------------------------------------------------- research loop
def _research_dataset(signal: float, seed: int) -> pd.DataFrame:
    parts = []
    for s_i, sym in enumerate(("BTC/USDT", "ETH/USDT")):
        df = synthetic(8000, signal=signal, seed=seed + s_i)
        rng = np.random.default_rng(seed + 10 + s_i)
        df["ret"] = np.where(df["label"] == 1, 0.012, -0.007) + rng.normal(0, 0.001, len(df))
        df["pos"] = np.arange(len(df))
        df["end_idx"] = df["pos"] + 8
        df["symbol"] = sym
        df["ret_16"] = rng.normal(0, 0.01, len(df))
        df["rvol_96"] = np.abs(rng.normal(0.003, 0.001, len(df)))
        df["direction_16"] = rng.integers(-1, 2, len(df)).astype(float)
        df["vol_expansion"] = rng.integers(0, 2, len(df)).astype(float)
        parts.append(df)
    return pd.concat(parts).sort_index(kind="stable")


def test_research_loop_abstains_without_edge_and_trades_with_one() -> None:
    from nexus_ai.research import ResearchConfig, walk_forward_v2

    cfg = ResearchConfig(train_days=40, test_days=15, families=("logit", "xgb"))
    feats = ["a", "b", "c", "d"]
    noise = walk_forward_v2(_research_dataset(0.0, 1), feats, cfg)
    assert not noise["passed"]
    assert noise["system"]["windows_abstained"] >= 1 or (noise["system"]["expectancy"] or 0) <= 0.003
    edge = walk_forward_v2(_research_dataset(1.5, 2), feats, cfg)
    assert edge["system"]["trades"] > 50 and edge["system"]["expectancy"] > 0
    assert edge["system"]["mean_auc"] > 0.7
    assert set(edge["model_comparison"]) == {"logit", "xgb"}
    for w in edge["windows"]:
        assert w["chosen"] in ("logit", "xgb")            # chosen from train CV only
