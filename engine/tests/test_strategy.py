"""trend_pullback v1: look-ahead safety, rule firing, proposal invariants, HOLD paths."""

import json
from dataclasses import replace
from decimal import Decimal

import pytest
from synthetic import aggregate, random_walk

from nexus_engine.signals.risk import CostModel, net_reward_risk
from nexus_engine.signals.series import CandleSeries
from nexus_engine.signals.strategy import Decision, Params, evaluate
from nexus_engine.timeframes import tf_delta

H = tf_delta("1h")


def build(seed: int, n: int = 1400) -> tuple[CandleSeries, CandleSeries, list]:
    # Start 4h-aligned so aggregation produces valid 4h candles.
    own = random_walk("1h", n, seed)
    htf = aggregate(own, "1h", "4h")
    return CandleSeries.from_candles("1h", own), CandleSeries.from_candles("4h", htf), own


def scan(seed: int) -> list[Decision]:
    own, htf, candles = build(seed)
    return [evaluate(own, htf, c.open_time + H) for c in candles[1000:]]


# Seeds found by scanning 80 synthetic series: signals are rare by design (~5 in 32,000 evaluations).
BUY_SEED, SELL_SEED = 71, 32


@pytest.fixture(scope="module")
def decisions() -> list[Decision]:
    return [d for seed in (BUY_SEED, SELL_SEED) for d in scan(seed)]


def test_decisions_do_not_depend_on_future_candles() -> None:
    """Truncation invariance: deciding at as_of with or without later candles is identical."""
    own, htf, candles = build(21)
    for i in range(1000, 1400, 7):
        as_of = candles[i].open_time + H
        full = evaluate(own, htf, as_of)
        trunc_own = CandleSeries.from_candles("1h", candles[: i + 1])
        trunc_htf = CandleSeries.from_candles("4h", aggregate(candles[: i + 1], "1h", "4h"))
        assert evaluate(trunc_own, trunc_htf, as_of) == full


def test_rules_fire_in_both_directions_and_mostly_hold(decisions: list[Decision]) -> None:
    kinds = [d.decision for d in decisions]
    assert "BUY" in kinds and "SELL" in kinds
    assert kinds.count("HOLD") / len(kinds) > 0.9  # signals are rare; nothing is forced


def test_proposals_are_well_formed(decisions: list[Decision]) -> None:
    for d in decisions:
        if d.decision == "HOLD":
            assert d.proposal is None and d.reasons
            continue
        p = d.proposal
        assert p is not None and d.explanation is not None
        if d.decision == "BUY":
            assert p.stop_loss < p.entry_low <= p.entry_high < p.take_profits[0]
        else:
            assert p.stop_loss > p.entry_high >= p.entry_low > p.take_profits[0]
        assert list(p.take_profits) == sorted(p.take_profits, reverse=d.decision == "SELL")
        assert p.risk_reward >= Decimal("1.5")
        for x in (p.entry_low, p.entry_high, p.stop_loss, *p.take_profits):
            assert x.as_tuple().exponent == -8
        assert p.entry_expires_at == d.as_of + 6 * H

        # Explanations contain only strings (stable JSON for hashing).
        def leaves(o: object) -> list[object]:
            if isinstance(o, dict):
                return [v for x in o.values() for v in leaves(x)]
            if isinstance(o, list):
                return [v for x in o for v in leaves(x)]
            return [o]

        assert all(isinstance(v, str) for v in leaves(d.explanation))
        assert json.dumps(d.explanation)


def test_hold_paths() -> None:
    own, htf, candles = build(31)
    as_of = candles[-1].open_time + H
    assert evaluate(CandleSeries.from_candles("1h", candles[:100]), htf, candles[99].open_time + H).reasons == ("insufficient_history",)
    assert evaluate(own, htf, as_of + H).reasons == ("stale_data",)
    gapped = CandleSeries.from_candles("1h", candles[:1300] + candles[1301:])
    assert evaluate(gapped, htf, as_of).reasons == ("data_quality_gap",)
    assert evaluate(own, None, as_of).reasons == ("htf_missing",)
    short_htf = CandleSeries.from_candles("4h", aggregate(candles, "1h", "4h")[-50:])
    assert evaluate(own, short_htf, as_of).reasons == ("htf_insufficient_history",)
    strict = replace(Params(), max_atr_pct=0.0001)
    assert evaluate(own, htf, as_of, strict).reasons == ("volatility_out_of_bounds",)


def test_net_reward_risk_includes_costs() -> None:
    zero = CostModel(Decimal(0), Decimal(0))
    assert net_reward_risk("BUY", Decimal(100), Decimal(95), Decimal(110), zero) == Decimal(2)
    assert net_reward_risk("SELL", Decimal(100), Decimal(105), Decimal(90), zero) == Decimal(2)
    with_costs = net_reward_risk("BUY", Decimal(100), Decimal(95), Decimal(110), CostModel())
    # cost = 100*1.0005*1.001; reward = 110*0.9995*0.999 - cost; risk = cost - 95*0.9995*0.999
    cost = Decimal(100) * Decimal("1.0005") * Decimal("1.001")
    expected = (Decimal(110) * Decimal("0.9995") * Decimal("0.999") - cost) / (cost - Decimal(95) * Decimal("0.9995") * Decimal("0.999"))
    assert with_costs == expected and with_costs < 2
