from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
from nexus_api.intelligence import analyze, event_or_none, klines_frame
from nexus_exchange.models import OrderBook

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def test_klines_frame_drops_the_unclosed_bar() -> None:
    def row(open_min: int, close_ms_offset: int) -> list[object]:
        t = int((NOW - timedelta(minutes=open_min)).timestamp() * 1000)
        return [t, "1", "2", "0.5", "1.5", "10", t + close_ms_offset, "0", 0, "0", "0", "0"]

    rows = [row(10, 299_999), row(5, 299_999), row(0, 299_999)]  # last bar still open
    df = klines_frame(rows, NOW)
    assert len(df) == 2 and df.index[-1] == NOW - timedelta(minutes=5)


def test_event_or_none_hides_nan() -> None:
    assert event_or_none(float("nan")) is None and event_or_none(None) is None
    assert event_or_none("bos_up") == "bos_up"


def _frame(tf: str, n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    step = pd.Timedelta(tf.replace("m", "min"))
    idx = pd.date_range(end=NOW - step, periods=n, freq=step, tz="UTC", name="open_time")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.003, n)))
    wick = rng.uniform(0.001, 0.004, n) * close
    return pd.DataFrame({"open": np.r_[close[0], close[:-1]], "high": close + wick,
                         "low": close - wick, "close": close,
                         "volume": rng.uniform(5, 15, n)}, index=idx)


def test_analysis_outputs_both_sides_with_exact_2r_and_reasons() -> None:
    frames = {tf: _frame(tf, 1000, i) for i, tf in enumerate(("5m", "15m", "1h", "4h", "1d"))}
    last = Decimal(str(float(frames["5m"]["close"].iloc[-1])))
    book = OrderBook("ETH/USDT", bids=((last - Decimal("0.01"), Decimal(50)),),
                     asks=((last + Decimal("0.01"), Decimal(50)),), timestamp=NOW)
    out = analyze("ETH/USDT", frames, book, frames, now=NOW, news_risk=0.1,
                  kill_switch=False, order_quote=Decimal(100))
    assert out["funding_rate"] is None and "451" in out["futures_data_note"]
    sides = {h["side"]: h for h in out["hypotheses"]}
    assert set(sides) == {"long", "short"}
    for h in sides.values():
        assert h["reward_risk"] == 2.0
        assert 0 <= h["score"] <= 100
        assert h["decision"] in ("ENTER", "NO_TRADE")
        assert h["decision"] == "ENTER" or h["no_trade_reasons"]
        assert set(h["reasons_for"]).isdisjoint(h["reasons_against"])
    assert all("error" not in v for v in out["timeframes"].values())
