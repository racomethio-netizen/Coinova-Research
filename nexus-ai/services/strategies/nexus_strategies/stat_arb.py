"""Statistical arbitrage: pair selection, cointegration, spread signals (research module).

Method (Engle and Granger 1987; Gatev, Goetzmann and Rouwenhorst 2006; Vidyamurthy 2004):

1. **Correlation screen** of log returns over the formation window (cheap first filter).
2. **Engle-Granger test**: OLS of log price A on log price B gives the hedge ratio beta;
   an augmented Dickey-Fuller regression on the residual spread gives a t-statistic. It
   is compared with MacKinnon's (2010, table 2: two variables, constant) finite-sample
   critical values. No p-value is invented: the report gives the statistic and the 1/5/10%
   critical values. Cointegrated = statistic below the 5% value.
3. **Half-life** of mean reversion from the AR(1) fit of the spread changes:
   half_life = -ln 2 / b. Pairs that revert too slowly (or not at all) are rejected.
4. **Signal** on the z-score of the spread (rolling mean and standard deviation, closed
   bars only):
   * |z| >= ``entry_z``: trade the spread back towards its mean (short the rich leg, long
     the cheap leg);
   * |z| <= ``exit_z``: exit;
   * |z| >= ``stop_z``: stop (the relationship may have broken).
   An entry also needs the expected reversion (``|z| - exit_z`` standard deviations of
   the spread) to exceed the round-trip cost of four fills (two legs, in and out) times
   ``cost_margin``.

Every signal carries its inputs and the reasons it is valid or rejected.

**Status: research only.** A pair trade needs a short leg. On Binance that means USD-M
futures, which NEXUS keeps disabled, at 1x. This module does not place orders. A pair
strategy can be enabled only after a pre-registered out-of-sample test passes and the
owner approves futures.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd

# MacKinnon (2010), Table 2, N=2 (one regressor plus the dependent variable), constant
# term, no trend: (tau_inf, b1, b2) for crit(T) = tau_inf + b1/T + b2/T^2.
_MACKINNON_EG2 = {"1%": (-3.89644, -10.9519, -22.527),
                  "5%": (-3.33613, -6.1101, -6.823),
                  "10%": (-3.04445, -4.2412, -2.720)}


def eg_critical_values(nobs: int) -> dict[str, float]:
    t = float(nobs)
    return {k: a + b / t + c / (t * t) for k, (a, b, c) in _MACKINNON_EG2.items()}


def _ols(y: np.ndarray, X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Coefficients, their standard errors and the residuals."""
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    dof = max(len(y) - X.shape[1], 1)
    sigma2 = float(resid @ resid) / dof
    cov = sigma2 * np.linalg.pinv(X.T @ X)
    return coef, np.sqrt(np.clip(np.diag(cov), 0.0, None)), resid


def adf_tstat(x: np.ndarray, max_lags: int | None = None, *, constant: bool = True
              ) -> tuple[float, int]:
    """ADF t-statistic of rho in dx_t = [c +] rho x_{t-1} + sum g_i dx_{t-i} + e_t.

    The lag count is chosen by AIC on a common sample (Schwert's rule for the maximum).
    The chosen model is then re-fitted on all usable observations, as statsmodels'
    ``adfuller`` does. Returns (stat, lags)."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 20:
        raise ValueError("ADF needs at least 20 observations")
    if max_lags is None:
        max_lags = math.ceil(12 * (n / 100) ** 0.25)
    max_lags = max(0, min(max_lags, n // 2 - 2))
    dx = np.diff(x)

    def design(p: int, start: int) -> tuple[np.ndarray, np.ndarray]:
        m = len(dx) - start
        cols = ([np.ones(m)] if constant else []) + [x[start:-1]]
        cols += [dx[start - i:len(dx) - i] for i in range(1, p + 1)]
        return dx[start:], np.column_stack(cols)

    best_p, best_aic = 0, math.inf
    for p in range(max_lags + 1):
        y, X = design(p, max_lags)
        _, _, resid = _ols(y, X)
        rss = float(resid @ resid)
        aic = len(y) * math.log(rss / len(y)) + 2 * X.shape[1] if rss > 0 else -math.inf
        if aic < best_aic:
            best_p, best_aic = p, aic
    y, X = design(best_p, best_p)
    coef, se, _ = _ols(y, X)
    k = 1 if constant else 0
    stat = coef[k] / se[k] if se[k] > 0 else -math.inf
    return float(stat), best_p


def half_life(spread: np.ndarray) -> float | None:
    """Mean-reversion half-life in bars, None if the spread does not revert."""
    s = np.asarray(spread, dtype=float)
    ds = np.diff(s)
    X = np.column_stack([np.ones(len(ds)), s[:-1]])
    coef, _, _ = _ols(ds, X)
    b = coef[1]
    if not b < -1e-9:                          # no (or numerically no) reversion
        return None
    return float(-math.log(2) / math.log1p(b)) if b > -1 else 0.0


@dataclass(frozen=True)
class CointResult:
    a: str
    b: str
    beta: float
    intercept: float
    adf_stat: float
    adf_lags: int
    critical_values: dict[str, float]
    cointegrated: bool
    half_life: float | None
    correlation: float
    spread_std: float
    nobs: int

    def to_json(self) -> dict[str, Any]:
        return {"pair": f"{self.a}/{self.b}", "beta": round(self.beta, 6),
                "intercept": round(self.intercept, 6), "adf_stat": round(self.adf_stat, 4),
                "adf_lags": self.adf_lags,
                "critical_values": {k: round(v, 4) for k, v in self.critical_values.items()},
                "cointegrated_5pct": self.cointegrated,
                "half_life_bars": None if self.half_life is None else round(self.half_life, 2),
                "return_correlation": round(self.correlation, 4),
                "spread_std": self.spread_std, "nobs": self.nobs}


def engle_granger(price_a: pd.Series, price_b: pd.Series, *, a: str = "A", b: str = "B"
                  ) -> CointResult:
    """Engle-Granger test of log(A) = c + beta log(B) + e on aligned closes."""
    df = pd.concat([price_a, price_b], axis=1, join="inner").dropna()
    if len(df) < 30 or (df <= 0).any().any():
        raise ValueError("need at least 30 aligned positive prices")
    la, lb = np.log(df.iloc[:, 0].to_numpy(float)), np.log(df.iloc[:, 1].to_numpy(float))
    coef, _, resid = _ols(la, np.column_stack([np.ones(len(lb)), lb]))
    stat, lags = adf_tstat(resid, constant=False)   # residuals are mean zero by OLS
    crit = eg_critical_values(len(resid))
    corr = float(np.corrcoef(np.diff(la), np.diff(lb))[0, 1])
    return CointResult(a, b, float(coef[1]), float(coef[0]), stat, lags, crit,
                       bool(stat < crit["5%"]), half_life(resid), corr,
                       float(np.std(resid, ddof=1)), len(resid))


@dataclass(frozen=True)
class PairConfig:
    min_correlation: float = 0.6
    min_half_life: float = 1.0          # bars; faster is likely noise or bad data
    max_half_life: float = 30.0         # bars; slower ties up capital too long
    z_window: int = 60
    entry_z: float = 2.0
    exit_z: float = 0.5
    stop_z: float = 4.0
    fee_rate: float = 0.0005            # per fill (taker, futures)
    slippage_rate: float = 0.0005       # per fill
    cost_margin: float = 2.0            # expected reversion must exceed costs x this


def select_pairs(closes: pd.DataFrame, cfg: PairConfig | None = None) -> list[dict[str, Any]]:
    """Every pair of columns screened and tested on the given (formation) window.
    Ranked: cointegrated pairs first, then by ADF statistic (more negative = stronger)."""
    cfg = cfg or PairConfig()
    out: list[dict[str, Any]] = []
    for a, b in combinations(closes.columns, 2):
        row: dict[str, Any] = {"pair": f"{a}/{b}", "eligible": False, "reasons": []}
        try:
            res = engle_granger(closes[a], closes[b], a=str(a), b=str(b))
        except ValueError as exc:
            row["reasons"].append(str(exc))
            out.append(row)
            continue
        row.update(res.to_json())
        if res.correlation < cfg.min_correlation:
            row["reasons"].append(f"return correlation {res.correlation:.2f} < "
                                  f"{cfg.min_correlation:.2f}")
        if not res.cointegrated:
            row["reasons"].append(f"not cointegrated: ADF {res.adf_stat:.2f} >= 5% "
                                  f"critical {res.critical_values['5%']:.2f}")
        hl = res.half_life
        if hl is None:
            row["reasons"].append("spread does not mean-revert (AR(1) slope >= 0)")
        elif not cfg.min_half_life <= hl <= cfg.max_half_life:
            row["reasons"].append(f"half-life {hl:.1f} bars outside "
                                  f"[{cfg.min_half_life:g}, {cfg.max_half_life:g}]")
        row["eligible"] = not row["reasons"]
        out.append(row)
    out.sort(key=lambda r: (not r["eligible"], r.get("adf_stat", math.inf)))
    return out


@dataclass(frozen=True)
class PairSignal:
    decision: str                      # LONG_SPREAD, SHORT_SPREAD, EXIT, STOP, NO_TRADE
    z: float | None
    reasons: tuple[str, ...]
    inputs: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"decision": self.decision, "z": None if self.z is None else round(self.z, 4),
                "reasons": list(self.reasons), **self.inputs}


def spread_series(price_a: pd.Series, price_b: pd.Series, beta: float,
                  intercept: float = 0.0) -> pd.Series:
    df = pd.concat([price_a, price_b], axis=1, join="inner").dropna()
    return pd.Series(np.log(df.iloc[:, 0]) - beta * np.log(df.iloc[:, 1]) - intercept,
                     index=df.index)


def pair_signal(price_a: pd.Series, price_b: pd.Series, coint: CointResult, *,
                in_position: str | None = None, cfg: PairConfig | None = None) -> PairSignal:
    """Signal on the latest CLOSED bar. ``coint`` must come from the formation window (not
    from data that includes the bar being traded). LONG_SPREAD = long A, short beta x B."""
    cfg = cfg or PairConfig()
    s = spread_series(price_a, price_b, coint.beta, coint.intercept)
    if len(s) < cfg.z_window:
        return PairSignal("NO_TRADE", None, (f"only {len(s)} bars (need {cfg.z_window})",))
    win = s.iloc[-cfg.z_window:]
    sd = float(win.std(ddof=1))
    if not sd > 0:
        return PairSignal("NO_TRADE", None, ("spread has zero variance",))
    z = float((s.iloc[-1] - win.mean()) / sd)
    cost = 4 * (cfg.fee_rate + cfg.slippage_rate)            # two legs, in and out
    expected = (abs(z) - cfg.exit_z) * sd                    # log-spread units ~ return
    inputs = {"pair": f"{coint.a}/{coint.b}", "beta": coint.beta, "spread_std": sd,
              "expected_reversion": round(expected, 6), "round_trip_cost": cost,
              "half_life_bars": coint.half_life, "last_bar": str(s.index[-1])}
    if in_position:
        if abs(z) >= cfg.stop_z:
            return PairSignal("STOP", z, (f"|z| {abs(z):.2f} >= stop {cfg.stop_z}: the "
                                          "relationship may have broken",), inputs)
        if abs(z) <= cfg.exit_z:
            return PairSignal("EXIT", z, (f"|z| {abs(z):.2f} <= exit {cfg.exit_z}: "
                                          "spread reverted",), inputs)
        return PairSignal("NO_TRADE", z, ("holding: neither exit nor stop reached",), inputs)
    reasons: list[str] = []
    if not coint.cointegrated:
        reasons.append("pair not cointegrated on the formation window")
    hl = coint.half_life
    if hl is None or not cfg.min_half_life <= hl <= cfg.max_half_life:
        reasons.append(f"half-life {hl} outside [{cfg.min_half_life:g}, {cfg.max_half_life:g}]")
    if abs(z) < cfg.entry_z:
        reasons.append(f"|z| {abs(z):.2f} < entry {cfg.entry_z}")
    if abs(z) >= cfg.stop_z:
        reasons.append(f"|z| {abs(z):.2f} >= stop {cfg.stop_z}: too stretched to enter")
    if expected < cost * cfg.cost_margin:
        reasons.append(f"expected reversion {expected:.4f} < {cfg.cost_margin:g} x "
                       f"round-trip cost {cost:.4f}")
    if reasons:
        return PairSignal("NO_TRADE", z, tuple(reasons), inputs)
    side = "SHORT_SPREAD" if z > 0 else "LONG_SPREAD"
    why = (f"z {z:+.2f}: spread {'rich' if z > 0 else 'cheap'}; cointegrated "
           f"(ADF {coint.adf_stat:.2f} < {coint.critical_values['5%']:.2f}); half-life "
           f"{hl:.1f} bars; expected reversion {expected:.4f} > "
           f"{cfg.cost_margin:g} x cost {cost:.4f}")
    return PairSignal(side, z, (why,), inputs)
