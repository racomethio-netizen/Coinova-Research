"""Central grid risk manager: portfolio states (fail closed, latched shutdown), every
per-order limit, owner limit ceilings, and the independent-account allocator."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from nexus_risk import grid_risk as gr

D = Decimal


def view(**kw) -> gr.PortfolioView:
    base = dict(equity=D("10000"), peak_equity=D("10000"), day_start_equity=D("10000"),
                week_start_equity=D("10000"), futures_equity=D("5000"))
    base.update(kw)
    return gr.PortfolioView(**base)


def order(**kw) -> gr.OrderIntent:
    base = dict(market="spot", symbol="ETHUSDT", asset="ETH", side="buy", price=D("3000"),
                qty=D("0.05"), loss_if_stopped=D("15"))
    base.update(kw)
    return gr.OrderIntent(**base)


def test_states_and_fail_closed():
    m = gr.GridRiskManager()
    assert m.portfolio_state(view()) == ("NORMAL", [])
    assert m.portfolio_state(view(equity=D("9490")))[0] == "SHUTDOWN"          # -5.1%
    assert m.portfolio_state(view(latched_shutdown=True))[0] == "SHUTDOWN"
    for kw, word in ((dict(equity=D("9890"), peak_equity=D("9900")), "daily"),
                     (dict(equity=D("9690"), peak_equity=D("9700"),
                           day_start_equity=D("9700")), "weekly"),
                     (dict(data_ok=False), "fail closed"),
                     (dict(kill_switch=None), "unreadable"),
                     (dict(kill_switch=True), "kill switch"),
                     (dict(owner_floor="NO_NEW_TRADES"), "floor")):
        state, why = m.portfolio_state(view(**kw))
        assert state == "NO_NEW_ORDERS" and any(word in w for w in why), (kw, why)


def test_every_per_order_limit():
    m = gr.GridRiskManager()
    assert m.authorize(order(), view()) == (True, [])
    ok, why = m.authorize(order(loss_if_stopped=D("26")), view())
    assert not ok and "order risk" in why[0]
    ok, why = m.authorize(order(), view(exposure_by_asset={"SOL": D("5950")}))
    assert not ok and any("portfolio exposure" in w for w in why)
    ok, why = m.authorize(order(), view(exposure_by_asset={"ETH": D("2400")}))
    assert not ok and any("ETH exposure" in w for w in why)
    ok, why = m.authorize(order(), view(exposure_by_asset={"BTC": D("2400"), "SOL": D("2000")},
                                        btc_corr={"ETH": 0.9, "SOL": 0.85}))
    assert not ok and any("BTC-correlated" in w for w in why)
    ok, why = m.authorize(order(market="futures", margin=D("200")),
                          view(futures_margin=D("2400")))
    assert not ok and any("margin" in w for w in why)
    ok, why = m.authorize(order(), view(open_orders=120))
    assert not ok and any("open simulated orders" in w for w in why)
    ok, why = m.authorize(order(), view(kill_switch=True))
    assert not ok and "NO_NEW_ORDERS" in why[0]


def test_owner_limits_only_within_ceilings():
    assert gr.GridRiskLimits().problems() == []
    assert gr.GridRiskLimits(daily_loss=D("0.03")).problems()
    assert gr.GridRiskLimits(max_order_risk=D("0.02")).problems()
    assert gr.GridRiskLimits(leverage=D("2")).problems()
    lim = gr.GridRiskLimits.from_json({"daily_loss": "0.005", "max_open_orders": 50,
                                       "unknown": 1})
    assert lim.daily_loss == D("0.005") and lim.max_open_orders == 50
    assert gr.GridRiskLimits.from_json(lim.to_json()) == lim


def test_allocator_keeps_accounts_separate_and_caps_symbols():
    a = gr.PortfolioAllocator(gr.GridRiskLimits())
    plan = a.plan({"spot": D("5000"), "futures": D("5000")}, D("10000"),
                  {"spot": ["BTCUSDT"], "futures": ["ETHUSDT", "SOLUSDT"]})
    assert plan.per_grid["spot:BTCUSDT"] == D("2500")              # capped at 25% of 10k
    assert plan.per_grid["futures:ETHUSDT"] == D("2500")
    assert sum(v for k, v in plan.per_grid.items() if k.startswith("futures")) <= D("5000")
    assert gr.period_starts(datetime(2026, 11, 26, tzinfo=UTC)) == ("2026-11-26", "2026-W48")
