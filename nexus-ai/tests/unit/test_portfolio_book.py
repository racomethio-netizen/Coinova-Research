"""Cross-market portfolio risk book: combined loss limits and drawdown pause, positions
cap, central 2:1, sizing in USD for USD-quoted and USD-based pairs, open-risk cap,
correlation clusters (measured and by currency), no leverage, spot exposure cap, and
allocation proposals (reductions always allowed)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal as D

from nexus_config.settings import RiskSettings
from nexus_risk import portfolio_book as pb

NOW = datetime(2026, 12, 1, 12, tzinfo=UTC)
LIM = pb.Limits.from_settings(RiskSettings())


def acct(name: str = "exness_demo", market: str = "forex", equity: str = "10000",
         positions: tuple[pb.Position, ...] = (), **kw: object) -> pb.Account:
    e = D(equity)
    base = dict(day_start=e, week_start=e, peak=e, reconciled=True)
    base.update(kw)
    return pb.Account(name, market, e, positions=positions, **base)  # type: ignore[arg-type]


def book(*accounts: pb.Account) -> pb.Book:
    return pb.Book(accounts or (acct(), acct("crypto_paper", "crypto")), NOW)


def eurusd(side: int = 1, entry: str = "1.1000", stop: str = "1.0980",
           target: str = "1.1040") -> pb.Proposal:
    return pb.Proposal("forex", "EURUSD", side, "m", "exness_demo", entry=D(entry),
                       stop=D(stop), target=D(target))


def test_defaults_follow_the_owner_brief():
    r = RiskSettings()
    assert (r.risk_per_trade, r.daily_loss_limit, r.weekly_loss_limit, r.max_drawdown,
            r.max_open_positions) == (D("0.0025"), D("0.01"), D("0.025"), D("0.05"), 3)


def test_sizing_risks_a_quarter_percent_of_the_account():
    a = pb.approve(eurusd(), book(), LIM, kill_switch=False)
    assert a.approved, a.reasons
    # 0.25% of 10,000 = 25 USD over a 20-pip stop with a 10% allowance
    assert abs(a.risk_usd - D(25)) < D("0.01")
    assert abs(a.quantity - D(25) / (D("0.0020") * D("1.1"))) < 1
    jpy = pb.Proposal("forex", "USDJPY", -1, "m", "exness_demo", entry=D("150.00"),
                      stop=D("150.50"), target=D("149.00"))
    j = pb.approve(jpy, book(), LIM, kill_switch=False)
    assert j.approved and abs(j.risk_usd - D(25)) < D("0.01")      # converted from JPY
    assert pb.usd_per_unit("BTC/USDT", D(60000)) == (D(60000), D(1))


def test_portfolio_limits_combine_both_accounts():
    down = book(acct(equity="9850", day_start=D("10000")), acct("crypto_paper", "crypto",
                                                              equity="9950",
                                                              day_start=D("10000")))
    a = pb.approve(eurusd(), down, LIM, kill_switch=False)        # 1.0% down in total
    assert not a.approved and any("daily loss" in r for r in a.reasons)
    weekly = book(acct(equity="9740", week_start=D("10000"), day_start=D("9740")),
                  acct("crypto_paper", "crypto", equity="9760", week_start=D("10000"),
                       day_start=D("9760")))
    assert any("weekly loss" in r for r in pb.approve(eurusd(), weekly, LIM,
                                                      kill_switch=False).reasons)
    dd = book(acct(equity="9400", peak=D("10000"), day_start=D("9400"),
                   week_start=D("9400")),
              acct("crypto_paper", "crypto", equity="9600", peak=D("10000"),
                   day_start=D("9600"), week_start=D("9600")))
    a = pb.approve(eurusd(), dd, LIM, kill_switch=False)
    assert not a.approved and a.pause                               # 5% down: pause
    assert not pb.approve(eurusd(), book(), LIM, kill_switch=True).approved
    stale = book(acct(reconciled=False))
    assert any("not reconciled" in r for r in pb.approve(eurusd(), stale, LIM,
                                                         kill_switch=False).reasons)


def test_trade_rules():
    assert not pb.approve(eurusd(target="1.1030"), book(), LIM, kill_switch=False).approved
    held = pb.Position("exness_demo", "forex", "EURUSD", 1, D(10000), D("1.1"), D("1.09"),
                       D("1.1"))
    assert not pb.approve(eurusd(), book(acct(positions=(held,))), LIM,
                          kill_switch=False).approved                # duplicate
    three = tuple(pb.Position("crypto_paper", "crypto", f"X{i}/USDT", 1, D(1), D(10), D(9),
                              D(10)) for i in range(3))
    full = book(acct(), acct("crypto_paper", "crypto", positions=three))
    assert any("3 open positions" in r for r in pb.approve(eurusd(), full, LIM,
                                                           kill_switch=False).reasons)
    assert not pb.approve(eurusd(), book(acct("crypto_paper", "crypto")), LIM,
                          kill_switch=False).approved                # no forex account


def test_correlated_positions_share_one_risk_budget():
    gbp = pb.Position("exness_demo", "forex", "GBPUSD", 1, D(4000), D("1.25"), D("1.22"),
                      D("1.25"))                                   # 120 USD at risk
    b = book(acct(positions=(gbp,)), acct("crypto_paper", "crypto"))
    a = pb.approve(eurusd(), b, LIM, kill_switch=False)            # both short USD
    assert not a.approved and any("correlated cluster GBPUSD" in r for r in a.reasons)
    measured = pb.approve(eurusd(), b, LIM, kill_switch=False,
                          correlations={frozenset({"EURUSD", "GBPUSD"}): 0.2})
    assert measured.approved, measured.reasons                     # measured: not together
    opposite = pb.approve(eurusd(side=-1, stop="1.1020", target="1.0960"), b, LIM,
                          kill_switch=False,
                          correlations={frozenset({"EURUSD", "GBPUSD"}): 0.85})
    assert opposite.approved                                       # short EUR hedges long GBP


def test_open_risk_cap_and_no_leverage():
    big = tuple(pb.Position("crypto_paper", "crypto", s, 1, D("0.1"), D(60000), D(59000),
                            D(60000)) for s in ("BTC/USDT", "ETH/USDT"))   # 100 USD each
    b = book(acct(), acct("crypto_paper", "crypto", positions=big))
    a = pb.approve(eurusd(), b, LIM, kill_switch=False,
                   correlations={frozenset({"EURUSD", "BTC/USDT"}): 0.1,
                                 frozenset({"EURUSD", "ETH/USDT"}): 0.1})
    assert not a.approved and any("open risk" in r for r in a.reasons)   # 225 > 200 cap
    wide = pb.approve(eurusd(stop="1.0999", target="1.1002"), book(), LIM, kill_switch=False)
    assert wide.notional_usd is None or wide.notional_usd <= D(20000)    # 1x of total
    capped = pb.approve(eurusd(stop="1.0999", target="1.1002"), book(), LIM,
                        kill_switch=False)
    assert not capped.approved and any("too small" in r for r in capped.reasons)


def test_spot_crypto_exposure_cap_and_allocations():
    btc = pb.Proposal("crypto", "BTC/USDT", 1, "m", "crypto_paper", entry=D(60000),
                      stop=D(59000), target=D(62000))
    a = pb.approve(btc, book(), LIM, kill_switch=False)
    assert a.approved and a.notional_usd <= D(3000)                 # 30% of 10,000
    tight = pb.Proposal("crypto", "BTC/USDT", 1, "m", "crypto_paper", entry=D(60000),
                        stop=D(59900), target=D(60200))
    t = pb.approve(tight, book(), LIM, kill_switch=False)           # capped at 30%: too small
    assert not t.approved and any("too small" in r for r in t.reasons)
    grow = pb.Proposal("crypto", "BTC/USDT", 1, "crypto_swing_trend", "crypto_paper",
                       kind="allocation", notional_usd=D(4000))
    assert not pb.approve(grow, book(), LIM, kill_switch=False).approved
    ok = pb.Proposal("crypto", "BTC/USDT", 1, "crypto_swing_trend", "crypto_paper",
                     kind="allocation", notional_usd=D(1500))
    assert pb.approve(ok, book(), LIM, kill_switch=False).approved
    cut = pb.Proposal("crypto", "BTC/USDT", -1, "crypto_swing_trend", "crypto_paper",
                      kind="allocation", notional_usd=D(1500))
    killed = pb.approve(cut, book(), LIM, kill_switch=True)
    assert killed.approved and killed.reasons == []                # reductions always allowed
