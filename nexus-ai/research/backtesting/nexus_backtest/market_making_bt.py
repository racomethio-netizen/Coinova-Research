"""Market-making backtest (pre-registration:
docs/research/2026-11-26-market-making-preregistration.md).

Every minute, one post-only buy below and one sell above the previous close, skewed
against inventory, replaced the next minute. A fill needs the minute's range to trade
strictly THROUGH the order price. Paper book per symbol of 10,000 USDT. Perpetuals pay
actual funding; spot is measured as the excess over holding its starting coins.

Usage: ``python -m nexus_backtest.market_making_bt --data D --out F [--workers N]``
(``D`` holds ``fut/`` and ``spot/`` with Binance monthly 1m archives and funding).
"""

from __future__ import annotations

import argparse
import io
import json
import math
import zipfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nexus_backtest.batch3 import verify_checksums
from nexus_backtest.significance import holm, stationary_bootstrap_pvalue

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT")
START = pd.Timestamp("2024-01-01", tz="UTC")
END = pd.Timestamp("2026-11-01", tz="UTC")
CAPITAL, ORDER, LIMIT = 10_000.0, 500.0, 2_500.0
SKEW = 0.8                     # quotes lean by up to 0.8 x h against inventory
STOP = 0.01                    # daily loss stop, fraction of the book
SLIP = 0.0002                  # taker slippage when the stop closes inventory
PENETRATION = 0.0002           # robustness gate 8: must trade 0.02% through the order
VOL_WINDOW = 60
DAY_MS = 86_400_000
FEES = {"perp": (0.0002, 0.0005), "spot": (0.0010, 0.0010)}       # (maker, taker), VIP 0


@dataclass(frozen=True)
class Cfg:
    market: str                # "perp" or "spot"
    h: float                   # fixed half-spread, or the floor when vol_k is set
    vol_k: float | None = None
    fee_mult: float = 1.0
    penetration: float = 0.0


VARIANTS: dict[str, Cfg] = {
    "perp:h0.05%": Cfg("perp", 0.0005), "perp:h0.10%": Cfg("perp", 0.0010),
    "perp:h0.20%": Cfg("perp", 0.0020), "perp:vol2x_min0.03%": Cfg("perp", 0.0003, 2.0),
    "spot:h0.15%": Cfg("spot", 0.0015), "spot:h0.20%": Cfg("spot", 0.0020),
    "spot:h0.30%": Cfg("spot", 0.0030), "spot:vol2x_min0.12%": Cfg("spot", 0.0012, 2.0)}
SETTINGS: dict[str, tuple[float, float]] = {           # (fee multiple, penetration)
    "base": (1.0, 0.0), "fee2x": (2.0, 0.0), "strict": (1.0, PENETRATION)}


# ------------------------------------------------------------------------------ data
def _csv(raw: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(raw), header=None)
    if not str(df.iloc[0, 0]).strip().isdigit():          # header row present
        df = pd.read_csv(io.BytesIO(raw))
        df.columns = range(df.shape[1])
    return df


def load_klines(d: Path, sym: str) -> pd.DataFrame:
    parts = []
    for z in sorted(d.glob(f"{sym}-1m-*.zip")):
        with zipfile.ZipFile(z) as zf:
            df = _csv(zf.read(zf.namelist()[0])).iloc[:, :5].astype(float)
        parts.append(df)
    if not parts:
        raise FileNotFoundError(f"no 1m archives for {sym} in {d}")
    k = pd.concat(parts)
    t = k[0].to_numpy().astype("int64")
    t = np.where(t > 10**14, t // 1000, t)                   # spot 2025+: microseconds
    out = pd.DataFrame({"t": t, "open": k[1].to_numpy(), "high": k[2].to_numpy(),
                        "low": k[3].to_numpy(), "close": k[4].to_numpy()})
    out = out.drop_duplicates("t").sort_values("t")
    lo, hi = int(START.timestamp() * 1000), int(END.timestamp() * 1000)
    return out[(out["t"] >= lo) & (out["t"] < hi)].reset_index(drop=True)


def load_funding(d: Path, sym: str) -> tuple[np.ndarray, np.ndarray]:
    rows = []
    for z in sorted(d.glob(f"{sym}-fundingRate-*.zip")):
        with zipfile.ZipFile(z) as zf:
            df = _csv(zf.read(zf.namelist()[0]))
        rows.append(df.iloc[:, [0, 2]].astype(float))
    if not rows:
        return np.array([], dtype="int64"), np.array([])
    f = pd.concat(rows).drop_duplicates(0).sort_values(0)
    t = (f[0].to_numpy().astype("int64") // 1000) * 1000      # whole seconds
    return t, f[2].to_numpy(dtype=float)


def tick_size(k: pd.DataFrame) -> float:
    """Smallest price step seen in the data, as a power of ten."""
    px = np.unique(k[["open", "high", "low", "close"]].to_numpy().ravel())
    step = np.diff(px)
    step = step[step > 1e-12]
    return float(10 ** round(math.log10(step.min())))


def rolling_vol(close: np.ndarray) -> np.ndarray:
    """At bar i: std of the 60 one-minute log returns ending at close[i-1]."""
    r = pd.Series(np.log(close)).diff()
    return np.asarray(r.rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std().shift(1),
                      dtype=float)


# -------------------------------------------------------------------------- simulate
def simulate(k: pd.DataFrame, vol: np.ndarray, funding: tuple[np.ndarray, np.ndarray],
             tick: float, cfg: Cfg) -> dict[str, Any]:
    maker, taker = (x * cfg.fee_mult for x in FEES[cfg.market])
    spot = cfg.market == "spot"
    t = k["t"].tolist()
    o, hi, lo, c = (k[x].tolist() for x in ("open", "high", "low", "close"))
    v = vol.tolist()
    ft, fr = funding[0].tolist(), funding[1].tolist()
    n, nf = len(t), len(ft)
    first = VOL_WINDOW + 1
    if n <= first:
        raise ValueError("not enough bars")
    pen = cfg.penetration
    if spot:                   # the target coins are bought at the start (taker)
        px0 = o[first] * (1 + SLIP)
        tq = 2_500.0 / px0
        cash = CAPITAL - tq * px0 * (1 + taker)
    else:
        tq, cash = 0.0, CAPITAL
    hold_cash, qty = cash, tq                  # benchmark: hold tq coins, never trade
    fees = funding_total = 0.0
    buys = sells = stops = quoted = 0
    inv_sum = inv_max = 0.0
    fp = 0
    while fp < nf and ft[fp] < t[first]:
        fp += 1
    day = t[first] // DAY_MS
    day_start = 0.0
    stopped = pending_flat = False
    daily: dict[int, float] = {}
    measure = 0.0
    for i in range(first, n):
        ti = t[i]
        d = ti // DAY_MS
        if d != day:
            daily[day] = measure
            day, day_start, stopped = d, measure, False
        while fp < nf and ft[fp] <= ti:                    # funding at this bar's open
            if qty and not spot:
                f = qty * o[i] * fr[fp]
                cash -= f
                funding_total -= f
            fp += 1
        if pending_flat:                                   # daily stop: close deviation
            dq = tq - qty
            if dq:
                px = o[i] * (1 + SLIP) if dq > 0 else o[i] * (1 - SLIP)
                fee = abs(dq) * px * taker
                cash -= dq * px + fee
                fees += fee
                qty = tq
            pending_flat = False
        if not stopped:
            ref = c[i - 1]
            h = cfg.h if cfg.vol_k is None or v[i] != v[i] else max(cfg.h, cfg.vol_k * v[i])
            dev = (qty - tq) * ref
            x = dev / LIMIT
            x = 1.0 if x > 1.0 else (-1.0 if x < -1.0 else x)
            skew = -SKEW * h * x
            bid = math.floor(ref * (1 - h + skew) / tick) * tick
            ask = math.ceil(ref * (1 + h + skew) / tick) * tick
            sz = ORDER / ref
            quoted += 1
            can_buy = dev + ORDER <= LIMIT + 1e-9
            can_sell = dev - ORDER >= -LIMIT - 1e-9 and (not spot or qty - sz >= -1e-12)
            if can_buy and lo[i] < bid * (1 - pen):
                fee = sz * bid * maker
                cash -= sz * bid + fee
                fees += fee
                qty += sz
                buys += 1
            if can_sell and hi[i] > ask * (1 + pen):
                fee = sz * ask * maker
                cash += sz * ask - fee
                fees += fee
                qty -= sz
                sells += 1
        ci = c[i]
        measure = cash + qty * ci - hold_cash - tq * ci
        inv = abs(qty - tq) * ci
        inv_sum += inv
        if inv > inv_max:
            inv_max = inv
        if not stopped and measure - day_start <= -STOP * CAPITAL:
            stopped, pending_flat = True, True
            stops += 1
    daily[day] = measure
    days = len(daily)
    return {"daily_measure": daily, "buys": buys, "sells": sells, "stops": stops,
            "fees": fees, "funding": funding_total, "final": measure,
            "fills_per_day": (buys + sells) / max(days, 1),
            "avg_inventory": inv_sum / max(n - first, 1), "max_inventory": inv_max,
            "quoted_share": quoted / max(n - first, 1), "days": days}


def daily_pnl(daily_measure: dict[int, float]) -> pd.Series:
    s = pd.Series(daily_measure).sort_index()
    s.index = pd.to_datetime(s.index.to_numpy() * DAY_MS, unit="ms", utc=True)
    return s.diff().fillna(s.iloc[0])


# ---------------------------------------------------------------------------- report
def pooled_stats(per_symbol: dict[str, pd.Series], capital: float) -> dict[str, Any]:
    pnl = pd.concat(per_symbol, axis=1).fillna(0.0).sum(axis=1).sort_index()
    r = pnl / capital
    eq = capital + pnl.cumsum()
    sd = float(r.std(ddof=1))
    return {"net_pnl": float(pnl.sum()), "net_return": float(pnl.sum() / capital),
            "sharpe": float(r.mean() / sd * math.sqrt(365)) if sd > 0 else None,
            "max_drawdown": float((eq / eq.cummax() - 1).min()),
            "by_year": {str(y): float(g.sum()) for y, g in pnl.groupby(pnl.index.year)},
            "days": len(pnl)}


def _job(args: tuple[str, str, str]) -> list[dict[str, Any]]:
    market, sym, root = args
    d = Path(root) / ("fut" if market == "perp" else "spot")
    k = load_klines(d, sym)
    vol = rolling_vol(k["close"].to_numpy())
    fund = load_funding(d, sym) if market == "perp" else (np.array([], "int64"), np.array([]))
    tick = tick_size(k)
    out = []
    for name, cfg in VARIANTS.items():
        if cfg.market != market:
            continue
        for setting, (fm, pen) in SETTINGS.items():
            res = simulate(k, vol, fund, tick, replace(cfg, fee_mult=fm, penetration=pen))
            res.update(variant=name, setting=setting, symbol=sym, tick=tick, bars=len(k))
            out.append(res)
    return out


def report(results: list[dict[str, Any]]) -> dict[str, Any]:
    cap = CAPITAL * len(SYMBOLS)
    out: dict[str, Any] = {}
    pvals: dict[str, float | None] = {}
    for name in VARIANTS:
        rows = {s: [r for r in results if r["variant"] == name and r["setting"] == s]
                for s in SETTINGS}
        series = {s: {r["symbol"]: daily_pnl(r["daily_measure"]) for r in rs}
                  for s, rs in rows.items()}
        st = {s: pooled_stats(series[s], cap) for s in SETTINGS}
        base = series["base"]
        per_symbol = {sym: float(x.sum()) for sym, x in base.items()}
        best = max(per_symbol, key=lambda k_: per_symbol[k_])
        without = sum(v for k_, v in per_symbol.items() if k_ != best)
        pooled = pd.concat(base, axis=1).fillna(0.0).sum(axis=1).sort_index() / cap
        p = stationary_bootstrap_pvalue(pooled.to_numpy(), simulations=5000, block=10)
        pvals[name] = p
        b = rows["base"]
        out[name] = {
            **st["base"], "fee2x": st["fee2x"], "strict_fill": st["strict"], "p_value": p,
            "per_symbol": per_symbol, "best_symbol": best, "without_best": without,
            "activity": {"fills_per_day": float(np.mean([r["fills_per_day"] for r in b])),
                         "fills_total": int(sum(r["buys"] + r["sells"] for r in b)),
                         "avg_inventory_usdt": float(np.mean([r["avg_inventory"] for r in b])),
                         "max_inventory_usdt": float(max(r["max_inventory"] for r in b)),
                         "stopped_days": int(sum(r["stops"] for r in b)),
                         "fees": float(sum(r["fees"] for r in b)),
                         "funding": float(sum(r["funding"] for r in b)),
                         "ticks": {r["symbol"]: r["tick"] for r in b}}}
    sig = holm(pvals)
    for name, v in out.items():
        g = {"net_positive": v["net_pnl"] > 0,
             "significant_holm": bool(sig[name]),
             "sharpe_ge_1": (v["sharpe"] or 0) >= 1.0,
             "drawdown_le_10pct": v["max_drawdown"] >= -0.10,
             "positive_2_of_3_years": sum(1 for x in v["by_year"].values() if x > 0) >= 2,
             "positive_at_2x_fees": v["fee2x"]["net_pnl"] > 0,
             "positive_without_best": v["without_best"] > 0,
             "positive_strict_fill": v["strict_fill"]["net_pnl"] > 0}
        v["gates"], v["passed"] = g, all(g.values())
    return {"variants": out,
            "summary": {"passed": [k_ for k_, v in out.items() if v["passed"]],
                        "tested": len(out)}}


def plain_terms(rep: dict[str, Any]) -> dict[str, Any]:
    """Arithmetic on the report's own fields, for people: P&L before fees, loss per day per
    10,000 USDT book, and how long a real book would have lasted (the backtest itself keeps
    trading past zero, so returns below -100% mean the book would have been emptied)."""
    for v in rep["variants"].values():
        days = max(v["days"], 1)
        per_book_day = v["net_pnl"] / len(SYMBOLS) / days
        v["plain"] = {
            "pnl_before_fees": v["net_pnl"] + v["activity"]["fees"],
            "pnl_per_day_per_book": per_book_day,
            "days_until_book_empty": (CAPITAL / -per_book_day) if per_book_day < 0 else None,
            "fills_per_day_per_coin": v["activity"]["fills_per_day"]}
    return rep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--commit", default="")
    a = ap.parse_args()
    checks = {m: verify_checksums(a.data / m) for m in ("fut", "spot")}
    for m, chk in checks.items():
        if chk["mismatched"] or chk["no_checksum"]:
            raise SystemExit(f"{m}: checksum problems {chk}")
    jobs = [(m, s, str(a.data)) for m in ("perp", "spot") for s in SYMBOLS]
    with ProcessPoolExecutor(a.workers) as ex:
        results = [r for rs in ex.map(_job, jobs) for r in rs]
    rep = plain_terms(report(results))
    rep.update(generated_at=datetime.now(UTC).isoformat(), harness_commit=a.commit,
               preregistration="docs/research/2026-11-26-market-making-preregistration.md",
               kind="historical backtest (no orders placed)", paper=False,
               window=[str(START.date()), "2026-10-31"], symbols=list(SYMBOLS),
               data={m: f"Binance 1m klines, {c['verified']} archives, checksums verified"
                     for m, c in checks.items()})
    a.out.write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({k_: (v["passed"], {g: ok for g, ok in v["gates"].items() if not ok})
                      for k_, v in rep["variants"].items()}, default=str))


if __name__ == "__main__":
    main()
