"""Extended technical indicators (Phase C). Same contract as ``indicators.py``:

* Input: DatetimeIndex (UTC open time), float columns open/high/low/close/volume.
* Causal: the value at row t uses only rows <= t. Every function here is tested for
  "prefix invariance" (appending future rows never changes past values) and against an
  independent loop-based reference implementation.
* Statistics in float64; prices for orders are recomputed in Decimal downstream.

Not included on purpose: Ichimoku's Chikou span (the close plotted 26 bars in the PAST,
i.e. it compares today's price with the future of that bar; unusable without look-ahead).
Senkou spans are returned as the values that apply AT bar t (computed 26 bars earlier),
which is what a chart shows under the current bar.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nexus_strategies.indicators import atr, ema, rsi, sma, true_range

# --------------------------------------------------------------------------- trend


def dmi(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """+DI, -DI and ADX (Wilder smoothing)."""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / tr
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return pd.DataFrame({"plus_di": plus_di, "minus_di": minus_di,
                         "adx": dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()})


def supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0) -> pd.DataFrame:
    """Supertrend line and direction (+1 up, -1 down), standard recursive definition."""
    a = atr(df, n).to_numpy(float)
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy(float)
    close = df["close"].to_numpy(float)
    upper_basic, lower_basic = hl2 + mult * a, hl2 - mult * a
    size = len(df)
    upper, lower = np.full(size, np.nan), np.full(size, np.nan)
    line, direction = np.full(size, np.nan), np.full(size, np.nan)
    for i in range(size):
        if np.isnan(a[i]):
            continue
        if i == 0 or np.isnan(upper[i - 1]):
            upper[i], lower[i] = upper_basic[i], lower_basic[i]
            direction[i] = 1.0 if close[i] >= hl2[i] else -1.0
        else:
            upper[i] = (upper_basic[i] if upper_basic[i] < upper[i - 1]
                        or close[i - 1] > upper[i - 1] else upper[i - 1])
            lower[i] = (lower_basic[i] if lower_basic[i] > lower[i - 1]
                        or close[i - 1] < lower[i - 1] else lower[i - 1])
            if direction[i - 1] < 0:
                direction[i] = 1.0 if close[i] > upper[i - 1] else -1.0
            else:
                direction[i] = -1.0 if close[i] < lower[i - 1] else 1.0
        line[i] = lower[i] if direction[i] > 0 else upper[i]
    return pd.DataFrame({"supertrend": line, "direction": direction}, index=df.index)


def ichimoku(df: pd.DataFrame, tenkan: int = 9, kijun: int = 26,
             senkou_b: int = 52, shift: int = 26) -> pd.DataFrame:
    """Tenkan, Kijun and the cloud spans that apply AT each bar (computed ``shift`` bars
    earlier). Chikou is deliberately omitted (look-ahead)."""
    def mid(n: int) -> pd.Series:
        return (df["high"].rolling(n, min_periods=n).max()
                + df["low"].rolling(n, min_periods=n).min()) / 2
    t, k = mid(tenkan), mid(kijun)
    span_a = ((t + k) / 2).shift(shift)
    span_b = mid(senkou_b).shift(shift)
    return pd.DataFrame({"tenkan": t, "kijun": k, "span_a": span_a, "span_b": span_b,
                         "above_cloud": (df["close"] > pd.concat([span_a, span_b], axis=1)
                                         .max(axis=1)).where(span_b.notna()),
                         "below_cloud": (df["close"] < pd.concat([span_a, span_b], axis=1)
                                         .min(axis=1)).where(span_b.notna())},
                        index=df.index)


def ema_set(close: pd.Series, spans: tuple[int, ...] = (9, 20, 21, 50, 100, 200)
            ) -> pd.DataFrame:
    return pd.DataFrame({f"ema_{s}": ema(close, s) for s in spans})


# --------------------------------------------------------------------------- momentum


def roc(close: pd.Series, n: int = 12) -> pd.Series:
    """Rate of change in percent."""
    return 100 * (close / close.shift(n) - 1)


def momentum(close: pd.Series, n: int = 10) -> pd.Series:
    return close - close.shift(n)


def stoch_rsi(close: pd.Series, n: int = 14, k: int = 3, d: int = 3) -> pd.DataFrame:
    """Stochastic RSI (0..100): %K = SMA_k of the RSI's position in its n-bar range."""
    r = rsi(close, n)
    lo, hi = r.rolling(n, min_periods=n).min(), r.rolling(n, min_periods=n).max()
    raw = 100 * (r - lo) / (hi - lo).replace(0, np.nan)
    k_line = sma(raw, k)
    return pd.DataFrame({"k": k_line, "d": sma(k_line, d)})


def cci(df: pd.DataFrame, n: int = 20) -> pd.Series:
    """Commodity Channel Index (Lambert): (TP - SMA) / (0.015 x mean absolute deviation)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    m = tp.rolling(n, min_periods=n).mean()
    mad = tp.rolling(n, min_periods=n).apply(lambda w: np.mean(np.abs(w - w.mean())),
                                             raw=True)
    return (tp - m) / (0.015 * mad.replace(0, np.nan))


# --------------------------------------------------------------------------- volatility


def keltner(df: pd.DataFrame, n: int = 20, atr_n: int = 10, mult: float = 2.0
            ) -> pd.DataFrame:
    mid = ema(df["close"], n)
    a = atr(df, atr_n)
    return pd.DataFrame({"mid": mid, "upper": mid + mult * a, "lower": mid - mult * a})


def historical_volatility(close: pd.Series, n: int = 30, periods_per_year: float = 365.0
                          ) -> pd.Series:
    """Annualized close-to-close volatility (log returns, sample std)."""
    return np.log(close).diff().rolling(n, min_periods=n).std() * np.sqrt(periods_per_year)


def volatility_regime(df: pd.DataFrame, short: int = 14, long: int = 100,
                      pct_window: int = 200) -> pd.DataFrame:
    """ATR percentile within a fixed trailing window, and expansion (short ATR well above
    long ATR) / contraction (well below)."""
    a_short, a_long = atr(df, short), atr(df, long)
    ratio = a_short / a_long
    pct = (a_short / df["close"]).rolling(pct_window, min_periods=pct_window).rank(pct=True)
    return pd.DataFrame({"atr_ratio": ratio, "atr_percentile": pct,
                         "expanding": (ratio > 1.2).where(ratio.notna()),
                         "contracting": (ratio < 0.8).where(ratio.notna())})


# --------------------------------------------------------------------------- volume


def obv(df: pd.DataFrame) -> pd.Series:
    sign = np.sign(df["close"].diff()).fillna(0.0)
    return (sign * df["volume"]).cumsum()


def mfi(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    flow = tp * df["volume"]
    known = tp.shift(1).notna()           # the first bar has no direction: not counted
    up = flow.where(tp > tp.shift(1), 0.0).where(known)
    down = flow.where(tp < tp.shift(1), 0.0).where(known)
    pos, neg = up.rolling(n, min_periods=n).sum(), down.rolling(n, min_periods=n).sum()
    out = 100 - 100 / (1 + pos / neg)
    return out.where(neg != 0, 100.0).where(pos.notna())


def relative_volume(volume: pd.Series, n: int = 20) -> pd.Series:
    """Current volume / mean of the PREVIOUS n bars."""
    return volume / volume.shift(1).rolling(n, min_periods=n).mean().replace(0, np.nan)


def volume_price_divergence(df: pd.DataFrame, n: int = 20) -> pd.Series:
    """+1 when price fell over n bars while OBV rose (accumulation), -1 when price rose
    while OBV fell (distribution), else 0."""
    dp = df["close"] - df["close"].shift(n)
    do = obv(df) - obv(df).shift(n)
    out = pd.Series(0.0, index=df.index)
    out[(dp < 0) & (do > 0)] = 1.0
    out[(dp > 0) & (do < 0)] = -1.0
    return out.where(dp.notna())


# --------------------------------------------------------------------------- candles


def candle_shape(df: pd.DataFrame) -> pd.DataFrame:
    """Body and wick shares of the bar's range (0..1; NaN for a zero-range bar)."""
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    top = df[["open", "close"]].max(axis=1)
    bottom = df[["open", "close"]].min(axis=1)
    return pd.DataFrame({"body": (df["close"] - df["open"]).abs() / rng,
                         "upper_wick": (df["high"] - top) / rng,
                         "lower_wick": (bottom - df["low"]) / rng,
                         "bullish": (df["close"] > df["open"]).astype(float)})


# --------------------------------------------------------------------------- levels


def pivots_from_daily(df: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """Classic floor pivots for each bar from the PREVIOUS completed UTC day."""
    d = daily.copy()
    p = (d["high"] + d["low"] + d["close"]) / 3
    lv = pd.DataFrame({"pivot": p, "r1": 2 * p - d["low"], "s1": 2 * p - d["high"],
                       "r2": p + (d["high"] - d["low"]), "s2": p - (d["high"] - d["low"])})
    lv.index = lv.index + pd.Timedelta(days=1)          # usable from the next day on
    day = df.index.floor("D")
    return lv.reindex(day).set_axis(df.index)


def range_state(df: pd.DataFrame, n: int = 20, width_atr: float = 3.0,
                adx_max: float = 20.0) -> pd.DataFrame:
    """Range/consolidation: the previous n bars' high-low range is narrow (in ATRs) and
    ADX is low. Breakout: close beyond that range. Values use bars before t for the range."""
    hi = df["high"].shift(1).rolling(n, min_periods=n).max()
    lo = df["low"].shift(1).rolling(n, min_periods=n).min()
    a = atr(df, 14)
    width = (hi - lo) / a
    adx_v = dmi(df)["adx"]
    ranging = ((width < width_atr) & (adx_v < adx_max)).where(width.notna() & adx_v.notna())
    return pd.DataFrame({"range_high": hi, "range_low": lo, "range_width_atr": width,
                         "ranging": ranging,
                         "breakout_up": (df["close"] > hi).where(hi.notna()),
                         "breakout_down": (df["close"] < lo).where(lo.notna())})


def breakout_follow_through(df: pd.DataFrame, n: int = 20, window: int = 5,
                            tol_atr: float = 0.25) -> pd.DataFrame:
    """After an upside breakout of the n-bar range: ``retest`` when, within ``window`` bars,
    a low comes back to the broken level (within tol x ATR) and the bar closes above it;
    ``failed`` when a close falls back below the level. Judged only on bars after the
    breakout (causal). Mirror for downside breakouts."""
    rs = range_state(df, n)
    a = atr(df, 14).to_numpy(float)
    hi_lv, lo_lv = rs["range_high"].to_numpy(float), rs["range_low"].to_numpy(float)
    up, down = rs["breakout_up"].to_numpy(), rs["breakout_down"].to_numpy()
    lo, hi, c = (df[k].to_numpy(float) for k in ("low", "high", "close"))
    size = len(df)
    retest_up, failed_up = np.zeros(size), np.zeros(size)
    retest_dn, failed_dn = np.zeros(size), np.zeros(size)
    active_up: list[tuple[int, float]] = []
    active_dn: list[tuple[int, float]] = []
    for i in range(size):
        keep_up = []
        for start, level in active_up:
            if i - start > window:
                continue
            if c[i] < level:
                failed_up[i] = 1.0
                continue
            if not np.isnan(a[i]) and lo[i] <= level + tol_atr * a[i]:
                retest_up[i] = 1.0
                continue
            keep_up.append((start, level))
        active_up = keep_up
        keep_dn = []
        for start, level in active_dn:
            if i - start > window:
                continue
            if c[i] > level:
                failed_dn[i] = 1.0
                continue
            if not np.isnan(a[i]) and hi[i] >= level - tol_atr * a[i]:
                retest_dn[i] = 1.0
                continue
            keep_dn.append((start, level))
        active_dn = keep_dn
        if up[i] is True or up[i] == 1.0:
            active_up.append((i, hi_lv[i]))
        if down[i] is True or down[i] == 1.0:
            active_dn.append((i, lo_lv[i]))
    return pd.DataFrame({"retest_up": retest_up, "failed_breakout_up": failed_up,
                         "retest_down": retest_dn, "failed_breakout_down": failed_dn},
                        index=df.index)


# --------------------------------------------------------------------------- liquidity


def equal_levels(df: pd.DataFrame, *, left: int = 2, right: int = 2, tol_atr: float = 0.1,
                 lookback: int = 50) -> pd.DataFrame:
    """Equal highs / equal lows: the newest confirmed swing is within ``tol_atr`` x ATR of
    an earlier confirmed swing of the same kind (within ``lookback`` bars). Reported on the
    bar that CONFIRMS the second swing. A liquidity pool, not a claim about who trades."""
    from nexus_analysis.structure import swings

    sw = swings(df, left, right)
    a = atr(df, 14)
    eq_high = pd.Series(np.nan, index=df.index)
    eq_low = pd.Series(np.nan, index=df.index)
    pos = {ts: i for i, ts in enumerate(df.index)}
    seen: dict[str, list[tuple[int, float]]] = {"high": [], "low": []}
    for ts, row in sw.iterrows():
        i, kind, price = pos[ts], str(row["kind"]), float(row["price"])
        tol = tol_atr * float(a.iloc[i]) if not np.isnan(a.iloc[i]) else None
        if tol is not None:
            for j, p in reversed(seen[kind]):
                if i - j > lookback:
                    break
                if abs(p - price) <= tol:
                    (eq_high if kind == "high" else eq_low).iloc[i] = price
                    break
        seen[kind].append((i, price))
    return pd.DataFrame({"equal_high": eq_high, "equal_low": eq_low})


def wick_rejection(df: pd.DataFrame, min_wick: float = 0.6) -> pd.Series:
    """+1 long lower wick (rejection of lower prices), -1 long upper wick, else 0."""
    shape = candle_shape(df)
    out = pd.Series(0.0, index=df.index)
    out[shape["lower_wick"] >= min_wick] = 1.0
    out[shape["upper_wick"] >= min_wick] = -1.0
    return out.where(shape["body"].notna())


def rsi_divergence(df: pd.DataFrame, n: int = 14, left: int = 2, right: int = 2
                   ) -> pd.Series:
    """Regular divergence on confirmed swings: +1 (bullish) when a swing low is lower than
    the previous swing low while RSI at it is higher; -1 (bearish) for higher high with
    lower RSI. Reported on the confirmation bar (no look-ahead)."""
    from nexus_analysis.structure import swings

    r = rsi(df["close"], n)
    sw = swings(df, left, right)
    out = pd.Series(0.0, index=df.index)
    prev: dict[str, tuple[float, float]] = {}
    for ts, row in sw.iterrows():
        kind = str(row["kind"])
        rv = float(r.loc[row["swing_time"]])
        price = float(row["price"])
        if np.isnan(rv):
            continue
        if kind in prev:
            pp, pr = prev[kind]
            if kind == "low" and price < pp and rv > pr:
                out.loc[ts] = 1.0
            elif kind == "high" and price > pp and rv < pr:
                out.loc[ts] = -1.0
        prev[kind] = (price, rv)
    return out
