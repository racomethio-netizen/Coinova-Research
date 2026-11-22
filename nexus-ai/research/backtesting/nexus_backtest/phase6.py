"""Single pre-registered run of the Phase 6 strategies (see the pre-registration doc).

``python -m nexus_backtest.phase6 --cache DIR --out FILE`` with NEXUS_DATABASE_URL pointing
at the research database (spot candles). Funding and taker-buy columns come from Binance
USD-M archives (data.binance.vision) already cached in DIR.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from nexus_config import get_settings
from nexus_db import make_engine, make_session_factory
from nexus_strategies.library_v2 import CANDLE_STRATEGIES, RESEARCH_STRATEGIES
from nexus_strategies.strategies import allowed_regimes

from nexus_backtest.cli import regime_breakdown
from nexus_backtest.data import load_frames
from nexus_backtest.futures_data import parse_funding_zip
from nexus_backtest.metrics import json_safe
from nexus_backtest.signals import CostModel
from nexus_backtest.walkforward import walk_forward


def taker_ratio_1h(zips: list[Path]) -> pd.Series:
    """Hourly taker-buy share of volume from 5m perpetual klines (open-time index)."""
    rows = []
    for z in zips:
        with zipfile.ZipFile(z) as zf:
            text = zf.read(zf.namelist()[0]).decode()
        for r in csv.reader(io.StringIO(text)):
            if r and r[0][:1].isdigit():
                rows.append((int(r[0]), float(r[5]), float(r[9])))
    df = pd.DataFrame(rows, columns=["t", "vol", "taker"]).drop_duplicates("t")
    df.index = pd.to_datetime(df["t"], unit="ms", utc=True)
    h = df[["vol", "taker"]].resample("1h").sum()
    return (h["taker"] / h["vol"].where(h["vol"] > 0)).rename("taker_buy_ratio")


def funding_24h(zips: list[Path]) -> pd.Series:
    """Mean of the last 3 settlements known at each settlement time (8h cadence)."""
    f = pd.concat([parse_funding_zip(z.read_bytes()) for z in zips]).sort_index()
    f = f[~f.index.duplicated()].astype(float)
    return f.rolling(3, min_periods=3).mean().rename("funding_24h")


def run(cache_funding: Path, cache_klines: Path, out: Path) -> list[dict[str, Any]]:
    settings = get_settings()
    engine = make_engine(settings.database_url.get_secret_value())
    data = load_frames(make_session_factory(engine), "binance", settings.trading_pairs,
                       ["5m", "15m", "1h", "4h"])
    engine.dispose()
    for frames in data.values():
        frames["1h"] = frames["1h"].copy()
    for sym, frames in data.items():
        native = sym.replace("/", "")
        h1 = frames["1h"]
        fund = funding_24h(sorted(cache_funding.glob(f"{native}-fundingRate-*.zip")))
        h1["funding_24h"] = fund.reindex(h1.index.union(fund.index)).ffill().reindex(h1.index)
        h1["taker_buy_ratio"] = taker_ratio_1h(
            sorted(cache_klines.glob(f"{native}-5m-*.zip"))).reindex(h1.index)
    costs = CostModel(float(settings.paper_fee_bps) / 10_000,
                      float(settings.paper_slippage_bps) / 10_000)
    results = []
    for code, cls in {**CANDLE_STRATEGIES, **RESEARCH_STRATEGIES}.items():
        rep = walk_forward(data, cls, costs=costs)
        edges = regime_breakdown(rep.oos)
        results.append(json_safe({
            "strategy": code, "passed": rep.passed, "failures": rep.failures,
            "oos": rep.stats, "regime_edges": edges,
            "allowed_regimes": allowed_regimes({code: edges})[code],
            "windows": [{"test_start": str(w["test_start"]), "params": w["params"],
                         "test": w["test"]} for w in rep.windows],
            "validated_at": datetime.now(UTC).isoformat(),
            "data_coverage": {s: {"funding_24h": int(f["1h"]["funding_24h"].notna().sum()),
                                  "taker_buy_ratio": int(f["1h"]["taker_buy_ratio"].notna().sum()),
                                  "bars_1h": len(f["1h"])} for s, f in data.items()}}))
    out.write_text(json.dumps(results, indent=1))
    return results


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--funding-cache", required=True)
    p.add_argument("--klines-cache", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    for r in run(Path(a.funding_cache), Path(a.klines_cache), Path(a.out)):
        print(r["strategy"], "PASS" if r["passed"] else "FAIL", r["oos"].get("n"),
              r["oos"].get("expectancy"), r["oos"].get("profit_factor"), r["failures"])


if __name__ == "__main__":
    main()
