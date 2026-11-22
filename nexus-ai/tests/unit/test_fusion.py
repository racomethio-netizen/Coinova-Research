"""Phase E: strategy selector and signal fusion."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from nexus_decision.fusion import LONG, NO_TRADE, WAIT, Candidate, Context, ModelView, fuse
from nexus_decision.selector import SelectorRules, assess, select

GOOD = {"passed": True, "allowed_regimes": ["trend_up"],
        "regime_edges": {"trend_up": {"n": 80, "expectancy": 0.004, "std": 0.01,
                                      "profit_factor": 1.6, "win_rate": 0.46}}}
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
CTX = Context(regime="trend_up", risk_state="NORMAL", entries_allowed=True, data_fresh=True,
              spread_bps=1.0)
NO_MODEL = ModelView(False, reason="no validated model is active")


# ---------------------------------------------------------------- selector
def test_selector_requires_every_piece_of_evidence() -> None:
    assert assess("a", GOOD, "trend_up").eligible
    lb = assess("a", GOOD, "trend_up").lower_bound
    assert lb == pytest.approx(0.004 - 0.01 / 80 ** 0.5)
    assert "failed out-of-sample validation" in assess("a", {**GOOD, "passed": False},
                                                       "trend_up").reasons
    assert "never validated" in assess("a", {}, "trend_up").reasons
    assert any("no validated edge in the range regime" in r
               for r in assess("a", GOOD, "range").reasons)
    few = {**GOOD, "regime_edges": {"trend_up": {**GOOD["regime_edges"]["trend_up"], "n": 12}}}
    assert any("12 out-of-sample trades" in r for r in assess("a", few, "trend_up").reasons)
    noisy = {**GOOD, "regime_edges": {"trend_up": {**GOOD["regime_edges"]["trend_up"],
                                                   "std": 0.08}}}
    assert any("robust expectancy" in r for r in assess("a", noisy, "trend_up").reasons)
    no_std = {**GOOD, "regime_edges": {"trend_up": {"n": 80, "expectancy": 0.004,
                                                    "profit_factor": 1.6}}}
    assert any("statistics incomplete" in r for r in assess("a", no_std, "trend_up").reasons)


def test_recent_results_can_only_remove_a_strategy() -> None:
    bad_forward = {"n": 40, "expectancy": -0.003, "std": 0.01}
    a = assess("a", GOOD, "trend_up", forward=bad_forward)
    assert not a.eligible and any("forward paper" in r for r in a.reasons)
    great_recent = {"n": 40, "expectancy": 0.05, "std": 0.01}
    assert not assess("b", {}, "trend_up", forward=great_recent).eligible


def test_selector_ranks_by_robust_estimate() -> None:
    better = {**GOOD, "regime_edges": {"trend_up": {**GOOD["regime_edges"]["trend_up"],
                                                    "expectancy": 0.006}}}
    ranked = select({"a": GOOD, "b": better, "c": {}}, "trend_up", rules=SelectorRules())
    assert [x.strategy for x in ranked] == ["b", "a", "c"]
    assert ranked[-1].eligible is False


# ---------------------------------------------------------------- fusion
def cand(code: str = "a") -> Candidate:
    return Candidate(code, "1h", NOW, entry=100.0, stop=98.0, target=105.0)


def test_no_validated_edge_is_no_trade_even_with_setups() -> None:
    failed = assess("a", {**GOOD, "passed": False}, "trend_up")
    sig = fuse("BTC/USDT", now=NOW, candidates=[cand()], assessments=[failed], model=NO_MODEL,
               ctx=CTX)
    assert sig.decision == NO_TRADE and sig.probability is None
    assert any("no strategy or model has a validated edge" in r for r in sig.reasons)
    assert sig.sources["setups_now"] == ["a"]        # seen, but it gets no vote


def test_validated_edge_without_setup_waits() -> None:
    sig = fuse("BTC/USDT", now=NOW, candidates=[], assessments=[assess("a", GOOD, "trend_up")],
               model=NO_MODEL, ctx=CTX)
    assert sig.decision == WAIT


def test_long_uses_measured_probability_expected_return_and_expiry() -> None:
    sig = fuse("BTC/USDT", now=NOW, candidates=[cand()], assessments=[assess("a", GOOD, "trend_up")],
               model=NO_MODEL, ctx=CTX)
    assert sig.decision == LONG and sig.strategy == "a"
    assert sig.probability == 0.46 and "n=80" in (sig.probability_source or "")
    assert sig.expected_return == 0.004 and sig.reward_risk == pytest.approx(2.5)
    assert sig.expires_at == "2026-11-23T14:00:00+00:00"
    assert any("SHORT not available" in r for r in sig.reasons)


@pytest.mark.parametrize(("change", "blocker"), [
    ({"entries_allowed": False, "risk_state": "CLOSE_ONLY"}, "risk state CLOSE_ONLY"),
    ({"data_fresh": False}, "market data stale"),
    ({"spread_bps": 40.0}, "spread 40.0 bps"),
    ({"spread_bps": None}, "no live order book"),
    ({"news_block": "high-impact"}, "news"),
    ({"regime": "high_vol"}, "high-volatility regime"),
])
def test_blockers_make_it_no_trade(change: dict[str, object], blocker: str) -> None:
    sig = fuse("BTC/USDT", now=NOW, candidates=[cand()], assessments=[assess("a", GOOD, "trend_up")],
               model=NO_MODEL, ctx=replace(CTX, **change))  # type: ignore[arg-type]
    assert sig.decision == NO_TRADE and any(blocker in b for b in sig.blockers)


def test_context_is_reported_not_voted() -> None:
    ctx = replace(CTX, mtf_conflict=True, btc_trend=-1.0, funding_24h=0.001)
    sig = fuse("ETH/USDT", now=NOW, candidates=[cand()], assessments=[assess("a", GOOD, "trend_up")],
               model=NO_MODEL, ctx=ctx)
    assert sig.decision == LONG                                 # context never adds votes
    assert len(sig.conflicts) == 3 and "halved" in (sig.position_size_note or "")
    assert sig.probability == 0.46                              # unchanged by context


def test_a_validated_model_can_veto_but_not_be_counted_twice() -> None:
    neg = ModelView(True, version="3", feature_version="f2", probability=0.31,
                    expected_return=-0.001, disagreement=0.02)
    sig = fuse("BTC/USDT", now=NOW, candidates=[cand()], assessments=[assess("a", GOOD, "trend_up")],
               model=neg, ctx=CTX)
    assert sig.decision == WAIT and any("expects a loss" in c for c in sig.conflicts)
    ood = replace(neg, expected_return=0.002, out_of_distribution=True)
    assert fuse("BTC/USDT", now=NOW, candidates=[cand()], assessments=[assess("a", GOOD, "trend_up")],
                model=ood, ctx=CTX).decision == NO_TRADE
    pos = replace(neg, probability=0.58, expected_return=0.002)
    sig = fuse("BTC/USDT", now=NOW, candidates=[cand()], assessments=[assess("a", GOOD, "trend_up")],
               model=pos, ctx=CTX)
    assert sig.decision == LONG and sig.model_version == "3" and sig.probability == 0.58
