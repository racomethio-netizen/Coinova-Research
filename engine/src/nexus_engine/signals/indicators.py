"""Technical indicators in NumPy (ADR-003).

Every function is *causal*: output[i] depends only on inputs[0..i], so
`f(x)[:k] == f(x[:k])` for every k (tested). Values are NaN until the
indicator is defined; nothing is back-filled. Floats are used only here and
in feature math, never for prices that are published or for money (ADR-009).
Definitions match TA-Lib (verified by golden tests against TA-Lib, test-only).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

Arr = NDArray[np.float64]


def _arr(x: object) -> Arr:
    a = np.asarray(x, dtype=np.float64)
    if a.ndim != 1:
        raise ValueError("expected a 1-D series")
    return a


def sma(x: object, n: int) -> Arr:
    a = _arr(x)
    out = np.full(a.shape, np.nan)
    if n < 1 or len(a) < n:
        return out
    c = np.cumsum(np.insert(a, 0, 0.0))
    out[n - 1 :] = (c[n:] - c[:-n]) / n
    return out


def ema(x: object, n: int) -> Arr:
    """EMA with alpha = 2/(n+1), seeded with the SMA of the first n values (TA-Lib)."""
    a = _arr(x)
    out = np.full(a.shape, np.nan)
    if n < 1 or len(a) < n:
        return out
    alpha = 2.0 / (n + 1)
    prev = float(np.mean(a[:n]))
    out[n - 1] = prev
    for i in range(n, len(a)):
        prev = alpha * a[i] + (1 - alpha) * prev
        out[i] = prev
    return out


def _wilder(values: Arr, n: int, seed_start: int) -> Arr:
    """Wilder smoothing: seed = mean(values[seed_start : seed_start+n]), then (prev*(n-1)+v)/n."""
    out = np.full(values.shape, np.nan)
    first = seed_start + n - 1
    if first >= len(values):
        return out
    prev = float(np.mean(values[seed_start : seed_start + n]))
    out[first] = prev
    for i in range(first + 1, len(values)):
        prev = (prev * (n - 1) + values[i]) / n
        out[i] = prev
    return out


def rsi(close: object, n: int = 14) -> Arr:
    c = _arr(close)
    out = np.full(c.shape, np.nan)
    if len(c) <= n:
        return out
    d = np.diff(c, prepend=np.nan)
    gain = np.where(d > 0, d, 0.0)
    loss = np.where(d < 0, -d, 0.0)
    avg_g = _wilder(gain, n, 1)
    avg_l = _wilder(loss, n, 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_g / avg_l
        out = 100.0 - 100.0 / (1.0 + rs)
    out = np.where((avg_l == 0) & ~np.isnan(avg_g), 100.0, out)
    out = np.where((avg_l == 0) & (avg_g == 0), 50.0, out)
    out[:n] = np.nan
    return out


def true_range(high: object, low: object, close: object) -> Arr:
    h, lo, c = _arr(high), _arr(low), _arr(close)
    prev = np.roll(c, 1)
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev), np.abs(lo - prev)))
    tr[0] = np.nan  # undefined without a previous close (TA-Lib convention)
    return tr


def atr(high: object, low: object, close: object, n: int = 14) -> Arr:
    tr = true_range(high, low, close)
    return _wilder(tr, n, 1)


def macd(close: object, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[Arr, Arr, Arr]:
    """MACD line, signal line, histogram. The line is defined once the slow EMA is."""
    c = _arr(close)
    line = ema(c, fast) - ema(c, slow)
    sig = np.full(c.shape, np.nan)
    start = slow - 1
    if len(c) > start:
        sig[start:] = ema(line[start:], signal)
    return line, sig, line - sig


def bollinger(close: object, n: int = 20, k: float = 2.0) -> tuple[Arr, Arr, Arr]:
    """Middle (SMA), upper and lower bands with the population standard deviation (TA-Lib)."""
    c = _arr(close)
    mid = sma(c, n)
    sd = np.full(c.shape, np.nan)
    if len(c) >= n:
        windows = np.lib.stride_tricks.sliding_window_view(c, n)
        sd[n - 1 :] = windows.std(axis=1)
    return mid + k * sd, mid, mid - k * sd


def volume_ratio(volume: object, n: int = 20) -> Arr:
    """Volume relative to its SMA over the n bars *before* the current bar (no self-reference)."""
    v = _arr(volume)
    out = np.full(v.shape, np.nan)
    base = sma(v, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        out[n:] = v[n:] / base[n - 1 : -1]
    return out
