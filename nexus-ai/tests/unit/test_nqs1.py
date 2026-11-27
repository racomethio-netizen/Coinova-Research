"""NQS-1 on synthetic data: no look-ahead, regimes, confirmations, the cost and
expected-value checks, NEXUS sizing and limits, exits, carry, the ML veto's chronology,
and the walk-forward harness."""

from __future__ import annotations

import itertools
from typing import Any

import numpy as np
import pandas as pd
import pytest
from nexus_backtest import nqs1_bt as bt
from nexus_strategies.nqs1 import ml
from nexus_strategies.nqs1.config import PROFILES, NQS1Config, Risk
from nexus_strategies.nqs1.engine import Portfolio, Position, choose, prepare, rollovers
from nexus_strategies.nqs1.features import align_higher, compute, resample
from nexus_strategies.nqs1.regime import SELECTION, classify

H1 = PROFILES["intraday"]


def synth(n: int, *, drift: float = 0.0, vol: float = 0.0008, seed: int = 0,
          price: float = 1.10, start: str = "2024-01-01") -> pd.DataFrame:
    """Hourly weekday bars from a random walk (log returns ~ N(drift, vol))."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n * 2, freq="1h", tz="UTC")
    idx = idx[idx.day_of_week < 5][:n]
    close = price * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    open_ = np.r_[price, close[:-1]]
    wig = np.abs(rng.normal(0, vol / 2, (2, n)))
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * (1 + wig[0]),
                         "low": np.minimum(open_, close) * (1 - wig[1]), "close": close},
                        index=idx)


# ------------------------------------------------------------------ look-ahead
def test_decision_frame_never_uses_future_bars():
    sig = synth(3000, seed=1)
    trend = resample(sig, "H4")
    full = prepare(sig, trend, H1)
    cut = sig.index[2200]
    part = prepare(sig[sig.index < cut], trend[trend.index < cut], H1)
    cols = [c for c in part.columns if part[c].dtype != object]
    pd.testing.assert_frame_equal(part[cols], full.loc[part.index, cols])
    assert (part["regime"] == full.loc[part.index, "regime"]).all()
    pd.testing.assert_frame_equal(choose(part, "adaptive"),
                                  choose(full, "adaptive").loc[part.index])


def test_higher_timeframe_bar_is_used_only_after_it_closes():
    sig = synth(400, seed=2)
    h4 = resample(sig, "H4")
    al = align_higher(sig, compute(h4), "H4", ("close",))
    for t in sig.index[50:120]:
        closed = h4[h4.index + pd.Timedelta("4h") <= t]
        assert al.at[t, "htf_close"] == closed["close"].iloc[-1]


def test_weekly_bars_cover_monday_to_friday():
    d = synth(24 * 15, seed=3).resample("1D").agg({"open": "first", "high": "max",
                                                   "low": "min", "close": "last"}).dropna()
    w = resample(d, "W1")
    assert all(t.day_of_week == 6 for t in w.index)          # labelled Sunday


# ---------------------------------------------------------------------- regimes
def test_regimes_trend_range_shock_and_warmup():
    up = compute(synth(1500, drift=0.0004, vol=0.0006, seed=4))
    reg = classify(up)
    assert (reg.iloc[:200] == "INSUFFICIENT_DATA").all()
    tail = reg.iloc[600:]
    assert (tail == "STRONG_BULL").mean() > 0.3
    assert (tail == "STRONG_BEAR").mean() == 0
    flat = compute(synth(1500, vol=0.0006, seed=5))
    assert (classify(flat).iloc[600:] == "SIDEWAYS").any()
    shock = synth(1500, vol=0.0006, seed=6)
    i = 900
    shock.iloc[i, shock.columns.get_loc("high")] = shock["close"].iloc[i] * 1.03
    assert classify(compute(shock)).iloc[i] == "UNSTABLE"


def test_adaptive_picks_follow_regime_and_need_two_confirmations():
    f = prepare(synth(4000, drift=0.0001, vol=0.0009, seed=7), resample(
        synth(4000, drift=0.0001, vol=0.0009, seed=7), "H4"), H1)
    pk = choose(f, "adaptive")
    on = pk[pk["dir"] != 0]
    assert len(on) > 0
    for t, row in on.iterrows():
        regime = f.at[t, "regime"]
        assert row["strategy"] in SELECTION[regime]
        assert row["conf"] >= (3 if regime == "VOL_CONTRACTION" else 2)
        if regime == "STRONG_BULL":
            assert row["dir"] > 0
        if regime == "STRONG_BEAR":
            assert row["dir"] < 0
    assert not set(f.loc[on.index, "regime"]) & {"WEAK_TREND", "UNSTABLE", "INSUFFICIENT_DATA"}
    single = choose(f, "breakout")
    assert set(single.loc[single["dir"] != 0, "strategy"]) <= {"breakout"}


# ---------------------------------------------------------- portfolio fixtures
def frame(path: list[tuple[float, float, float, float]], *, picks: dict[int, int] | None = None,
          atr: float = 0.005, start: str = "2024-01-08 08:00", stop_atr: float = 1.0,
          strategy: str = "trend", p_win: float = float("nan"),
          regime: str = "STRONG_BULL") -> tuple[pd.DataFrame, pd.DataFrame]:
    idx = pd.date_range(start, periods=len(path), freq="1h", tz="UTC")
    f = pd.DataFrame(path, columns=["open", "high", "low", "close"], index=idx)
    f["atr"], f["bb_mid"], f["ema20"], f["regime"] = atr, f["close"], f["close"], regime
    pk = pd.DataFrame({"dir": 0, "conf": 0, "stop_atr": 0.0, "kind": "r", "strategy": "",
                       "target_r": 2.0, "p_win": p_win}, index=idx)
    for i, d in (picks or {}).items():
        pk.loc[idx[i], ["dir", "conf", "stop_atr", "strategy"]] = [d, 3, stop_atr, strategy]
    return f, pk


def flat(n: int, px: float = 1.1) -> list[tuple[float, float, float, float]]:
    return [(px, px, px, px)] * n


def one(path: list[tuple[float, float, float, float]], **kw: Any) -> Portfolio:
    cfg = kw.pop("cfg", None)
    carry = kw.pop("carry", None)
    veto = kw.pop("veto", None)
    f, pk = frame(path, **kw)
    return Portfolio({"EURUSD": f}, {"EURUSD": pk}, H1, cfg, carry=carry, veto=veto)


COST = (0.8 + 0.7 + 2 * 0.2) * 0.0001            # EURUSD round trip in price


def test_sizing_is_quarter_percent_risk_in_broker_lots_and_costs_hit_equity():
    p = one([*flat(1), (1.1, 1.101, 1.099, 1.1), (1.1, 1.1, 1.094, 1.095), *flat(2, 1.095)],
            picks={0: 1})
    book = p.run()
    (tr,) = book.trades
    assert tr["units"] == 5000                      # 25 USD / 0.005, a multiple of 1,000
    assert tr["risk_usd"] == pytest.approx(25.0)    # 0.25% of 10,000
    assert tr["reason"] == "stop" and tr["exit"] == pytest.approx(1.095)
    assert tr["costs_usd"] == pytest.approx(COST * 5000)
    assert tr["pnl_usd"] == pytest.approx(-25.0 - COST * 5000)
    assert book.equity == pytest.approx(10_000 + tr["pnl_usd"])
    assert tr["r"] == pytest.approx(tr["pnl_usd"] / 25.0)


@pytest.mark.parametrize(("bar2", "reason", "exit_px"), [
    ((1.1, 1.111, 1.0995, 1.108), "target", 1.11),
    ((1.1, 1.111, 1.094, 1.1), "stop", 1.095),       # both reachable: stop assumed first
    ((1.09, 1.092, 1.088, 1.09), "stop_gap", 1.09),  # gapped through the stop
])
def test_exits_target_stop_first_and_gap(bar2: tuple[float, ...], reason: str, exit_px: float):
    book = one([*flat(2), bar2, *flat(2, bar2[3])], picks={0: 1}).run()
    assert book.trades[0]["reason"] == reason
    assert book.trades[0]["exit"] == pytest.approx(exit_px)


def test_break_even_then_trailing_exit_at_entry():
    path = [*flat(2), (1.1, 1.1055, 1.1001, 1.104), (1.104, 1.104, 1.0995, 1.1), *flat(2)]
    (tr,) = one(path, picks={0: 1}).run().trades
    assert tr["reason"] == "trailing"
    assert tr["exit"] == pytest.approx(1.1)
    assert tr["pnl_usd"] == pytest.approx(-COST * 5000)


def test_time_exit_and_opposite_signal():
    (tr,) = one(flat(60), picks={0: 1}).run().trades
    assert tr["reason"] == "time" and tr["bars"] == H1.max_hold_bars
    trades = one(flat(10), picks={0: 1, 4: -1}).run().trades
    assert trades[0]["reason"] == "opposite_signal"
    assert trades[1]["dir"] == -1                   # the opposite setup is then taken


def test_drawdown_halt_is_durable_and_closes_everything():
    path = [*flat(2), (1.0, 1.0, 0.99, 1.0), *flat(10, 1.0)]
    p = one(path, picks={0: 1, 5: 1, 8: 1})
    book = p.run()
    assert book.halted is not None and "drawdown" in book.halted
    assert len(book.trades) == 1
    assert book.rejections.get("halted (drawdown limit)") == 2


def test_daily_loss_limit_blocks_new_trades_that_day():
    cfg = NQS1Config(risk=Risk(daily_loss=0.001))
    path = [*flat(1), (1.1, 1.101, 1.099, 1.1), (1.1, 1.1, 1.094, 1.095), *flat(4, 1.095)]
    book = one(path, picks={0: 1, 4: 1}, cfg=cfg).run()
    assert len(book.trades) == 1
    assert book.rejections == {"daily loss limit": 1}


def test_session_spread_cost_ev_and_veto_rejections():
    fri = one(flat(4), picks={0: 1}, start="2024-01-12 18:00").run()      # decided 19:00
    assert fri.rejections == {"session (rollover or weekend)": 1} and not fri.trades
    thin = one(flat(4), picks={0: 1}, atr=0.0004).run()
    assert thin.rejections == {"spread too wide for volatility": 1}
    costly = one(flat(4), picks={0: 1}, atr=0.0008, stop_atr=1.0).run()
    assert costly.rejections == {"costs too large for the stop": 1}
    ev = one(flat(4), picks={0: 1}, p_win=0.2).run()                       # 0.2*2 - 0.8 < 0
    assert ev.rejections == {"negative expected value after costs": 1}
    vetoed = one(flat(4), picks={0: 1}, veto=lambda pos, r: True).run()
    assert vetoed.rejections == {"ML veto": 1} and not vetoed.trades


class Recorder(Portfolio):
    def _mark(self, t: Any, px: dict[str, float]) -> None:
        super()._mark(t, px)
        gross, ccy = self._exposure(px)
        eq = self.book.curve[-1][1]
        open_risk = sum(abs(p.entry - p.stop) * p.units / (p.entry if not p.pair.endswith(
            "USD") else 1) for p in self.book.open.values())
        self.seen.append((len(self.book.open), gross / eq,
                          max((abs(v) for v in ccy.values()), default=0) / eq, open_risk / eq))


def test_shared_book_limits_positions_exposure_and_open_risk():
    pairs = {"EURUSD": 1.1, "GBPUSD": 1.27, "USDJPY": 150.0, "AUDUSD": 0.66, "USDCHF": 0.88}
    frames, picks = {}, {}
    for pair, px in pairs.items():
        frames[pair], picks[pair] = frame(flat(12, px), picks={0: 1, 6: 1},
                                          atr=px * 0.004)
    p = Recorder(frames, picks, H1)
    p.seen = []                                     # type: ignore[attr-defined]
    p.run()
    seen = p.seen                                   # type: ignore[attr-defined]
    assert max(s[0] for s in seen) <= 3
    assert max(s[1] for s in seen) <= 1.0 + 1e-9
    assert max(s[2] for s in seen) <= 0.6 + 1e-9
    assert max(s[3] for s in seen) <= 0.01 + 1e-9


def test_rollover_count_and_carry_charge():
    t = pd.Timestamp
    # Tue 21:00 (1) + Wed 21:00 (3, the weekend) + Thu 21:00 (1)
    assert rollovers(t("2024-01-09 20:00", tz="UTC"), t("2024-01-11 22:00", tz="UTC")) == 5
    assert rollovers(t("2024-01-12 22:00", tz="UTC"), t("2024-01-15 20:00", tz="UTC")) == 0
    days = pd.date_range("2023-12-01", "2024-02-01", freq="1D", tz="UTC")
    carry = {"EURUSD": pd.Series(0.03, index=days)}
    (tr,) = one(flat(40), picks={0: 1}, carry=carry).run().trades
    assert tr["carry_usd"] == pytest.approx(2 * (0.03 - 0.005) / 365 * 5000 * 1.1)
    assert tr["pnl_usd"] == pytest.approx(-COST * 5000 + tr["carry_usd"])
    stale = {"EURUSD": pd.Series([0.03], index=[t("2023-12-01", tz="UTC")])}
    (tr,) = one(flat(40), picks={0: 1}, carry=stale).run().trades
    assert tr["carry_usd"] == 0                     # a stale rate is never charged


def test_carry_from_fred_rate_differentials_with_staleness(tmp_path: Any):
    def fred(sid: str, dates: list[str], vals: list[float]) -> None:
        pd.DataFrame({"observation_date": dates, sid: vals}).to_csv(
            tmp_path / f"{sid}.csv", index=False)

    fred("DTB3", ["2024-01-01", "2024-01-02"], [5.0, 5.0])
    for ccy, rate in (("EUR", 4.0), ("GBP", 5.25), ("JPY", 0.1), ("AUD", 4.35),
                      ("CHF", 1.5)):
        fred(bt.FX[ccy][2], ["2023-12-01", "2024-01-01"], [rate, rate])
    c = bt.build_carry(tmp_path)
    assert c["EURUSD"].iloc[-1] == pytest.approx(0.04 - 0.05)
    assert c["USDJPY"].iloc[-1] == pytest.approx(0.05 - 0.001)
    assert c["EURUSD"].index[-1] == pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(
        days=bt.CARRY_STALE_DAYS)


def test_mean_reversion_target_is_the_middle_known_before_the_bar():
    path = [*flat(2), (1.1, 1.103, 1.0995, 1.102), *flat(3, 1.102)]
    f, pk = frame(path, picks={0: 1}, stop_atr=1.0)
    pk.loc[pk.index[0], "kind"] = "mid"
    f["bb_mid"] = [1.106, 1.106, 1.102, 1.102, 1.102, 1.102]
    (tr,) = Portfolio({"EURUSD": f}, {"EURUSD": pk}, H1).run().trades
    # Bar 2 reaches its OWN middle (1.102) but not the one known when it opened (1.106);
    # the exit comes on bar 3, at the middle known by then.
    assert tr["reason"] == "target" and tr["closed"] == f.index[3]
    assert tr["exit"] == pytest.approx(1.102)


# ---------------------------------------------------------------------------- ML
def fake_trades(n: int, seed: int = 0) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        adx = float(rng.uniform(10, 50))
        win = rng.random() < (0.8 if adx > 30 else 0.15)
        out.append({"features": {"adx": adx, "rsi": 50.0}, "strategy": "trend",
                    "regime": "STRONG_BULL", "r": 2.0 if win else -1.0})
    return out


def test_ml_needs_enough_trades_and_only_vetoes():
    assert ml.train(fake_trades(ml.MIN_TRADES - 1)) is None
    veto = ml.make_veto(ml.train(fake_trades(400)))

    def probe(adx: float) -> Position:
        return Position("EURUSD", 1, "trend", "STRONG_BULL", pd.Timestamp("2024-01-01"),
                        1.1, 1000, 1.095, 0.005, None, "r", {"adx": adx, "rsi": 50.0})

    assert veto(probe(15), 2.0) is True
    assert veto(probe(45), 2.0) is False


def test_window_veto_applies_only_inside_its_test_window():
    wv = bt.WindowVeto()
    a, b = pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-07-01", tz="UTC")
    wv.spans.append((a, b, lambda pos, r: True))
    pos = Position("EURUSD", 1, "trend", "X", a, 1.1, 1, 1.0, 0.1, None, "r", {})
    assert wv(pos, 2.0) is True
    pos.opened = b
    assert wv(pos, 2.0) is False


# ----------------------------------------------------------------------- harness
def test_windows_are_consecutive_and_train_precedes_test():
    w = bt.windows(pd.Timestamp("2015-01-01", tz="UTC"), pd.Timestamp("2020-01-01", tz="UTC"))
    assert w[0][1] == pd.Timestamp("2017-01-01", tz="UTC")
    for (a, b, c), (_, b2, _) in itertools.pairwise(w):
        assert a < b < c and b2 == c
    assert w[-1][2] == pd.Timestamp("2020-01-01", tz="UTC")


def test_walk_forward_trades_only_out_of_sample(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bt, "TRAIN_MONTHS", 2)
    monkeypatch.setattr(bt, "TEST_MONTHS", 1)
    monkeypatch.setattr(bt, "WARMUP_MONTHS", 1)
    monkeypatch.setattr(bt, "MIN_TRAIN_TRADES", 1)
    monkeypatch.setattr(bt, "GRID", {"stop_scale": (1.0,), "take_profit_r": (1.5, 2.0),
                                     "adx_strong": (25.0,)})
    data = {}
    for i, pair in enumerate(("EURUSD", "GBPUSD")):
        s = synth(24 * 130, drift=0.00005 * (1 - 2 * i), vol=0.0009, seed=10 + i,
                  price=1.1 + 0.2 * i)
        data[pair] = (s, resample(s, "H4"))
    wf = bt.walk_forward(data, H1, "adaptive", {}, workers=1)
    first_test = pd.Timestamp(wf["windows"][0]["test"][0], tz="UTC")
    assert len(wf["windows"]) >= 2
    assert all(pd.Timestamp(t["opened"]) >= first_test for t in wf["result"]["trades"])
    assert wf["result"]["curve"][0][0] >= first_test
    m = bt.metrics(wf["result"])
    assert m["trades"] == len(wf["result"]["trades"])
    stress = bt.metrics(wf["stress"])
    if m["trades"]:                                 # same decisions, double the charge
        assert stress["trades"] == m["trades"]
        assert stress["costs_usd"] == pytest.approx(2 * m["costs_usd"], rel=0.1)


def test_monte_carlo_gates_and_holm():
    r = np.r_[np.full(60, 2.0), np.full(90, -1.0)]
    mc = bt.monte_carlo(r)
    assert mc == bt.monte_carlo(r)                  # seeded
    assert 0 <= mc["prob_drawdown_over_5pct"] <= 1 and mc["return_median"] > 0
    assert bt.monte_carlo(r[:5]) == {"note": "fewer than 10 trades"}
    m = {"trades": 150, "net_return": 0.04, "profit_factor": 1.3, "sharpe": 0.6,
         "max_drawdown": -0.03, "halted": None}
    g = bt.gates(m, [0.01, 0.02, -0.01], {"prob_drawdown_over_5pct": 0.1},
                 {"net_return": 0.01}, 0.01, 0.05)
    assert all(g.values())
    g = bt.gates(m | {"max_drawdown": -0.06}, [0.01], {"prob_drawdown_over_5pct": 0.1},
                 {"net_return": 0.01}, 0.01, 0.05)
    assert not g["max_drawdown_within_5pct"]
    assert bt.holm_flags({"a": 0.01, "b": 0.03, "c": None}) == {"a": True, "b": False,
                                                                "c": False}


def test_command_line_writes_results_and_report(tmp_path: Any,
                                                monkeypatch: pytest.MonkeyPatch):
    import json
    import sys

    for name, val in (("TRAIN_MONTHS", 2), ("TEST_MONTHS", 1), ("WARMUP_MONTHS", 1),
                      ("MIN_TRAIN_TRADES", 1), ("MIN_MONTHS", 4)):
        monkeypatch.setattr(bt, name, val)
    monkeypatch.setattr(bt, "GRID", {"stop_scale": (1.0,), "take_profit_r": (2.0,),
                                     "adx_strong": (25.0,)})
    for i, pair in enumerate(("EURUSD", "GBPUSD")):
        s = synth(24 * 130, vol=0.0009, seed=20 + i, price=1.1 + 0.2 * i)
        s.index.name = "time"
        s.reset_index().assign(time=lambda d: d["time"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")) \
            .to_csv(tmp_path / f"{pair}_H1.csv", index=False)
    out = tmp_path / "nqs1.json"
    monkeypatch.setattr(sys, "argv", ["nqs1_bt", "--data", str(tmp_path), "--profiles",
                                      "intraday,swing", "--out", str(out), "--workers", "1"])
    bt.main()
    rep = json.loads(out.read_text())
    assert rep["skipped"] == {"swing": "no D1 data"}
    prof = rep["profiles"]["intraday"]
    assert prof["pairs"] == ["EURUSD", "GBPUSD"]
    assert set(prof["walk_forward"]["gates"]) >= {"at_least_100_trades",
                                                   "significant_after_holm"}
    assert set(prof["comparison_default_params"]) == set(bt.MODES)
    assert rep["paper"] is False and rep["kind"].startswith("historical backtest")
    md = out.with_suffix(".md").read_text()
    assert "## Profile: intraday" in md and "Profile **swing** not run" in md
    assert ("PASSED" in md) != ("FAILED" in md)
