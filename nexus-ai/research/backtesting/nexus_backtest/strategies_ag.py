"""Single pre-registered run of strategy modules A-G
(docs/research/2026-11-23-strategies-a-g-preregistration.md).

``python -m nexus_backtest.strategies_ag --db URL --label primary --out FILE`` runs the
walk-forward validation of every A-G strategy on the BTC/ETH candles in that database.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_db import make_engine, make_session_factory
from nexus_strategies.library_v3 import V3_STRATEGIES
from nexus_strategies.strategies import allowed_regimes

from nexus_backtest.cli import regime_breakdown
from nexus_backtest.data import load_frames
from nexus_backtest.metrics import json_safe
from nexus_backtest.signals import CostModel
from nexus_backtest.walkforward import walk_forward

PAIRS = ["BTC/USDT", "ETH/USDT"]


def run(db_url: str, label: str, out: Path, only: list[str] | None = None) -> list[dict[str, Any]]:
    engine = make_engine(db_url)
    data = load_frames(make_session_factory(engine), "binance", PAIRS, ["5m", "15m", "1h", "4h"])
    engine.dispose()
    costs = CostModel(0.0010, 0.0005)          # 10 bps fee + 5 bps slippage per side
    results = []
    for code, cls in V3_STRATEGIES.items():
        if only and code not in only:
            continue
        started = time.monotonic()
        rep = walk_forward(data, cls, costs=costs)
        edges = regime_breakdown(rep.oos)
        results.append(json_safe({
            "strategy": code, "period": label, "passed": rep.passed, "failures": rep.failures,
            "oos": rep.stats, "regime_edges": edges,
            "allowed_regimes": allowed_regimes({code: edges})[code],
            "windows": [{"test_start": str(w["test_start"]), "params": w["params"],
                         "test": w["test"]} for w in rep.windows],
            "data": {s: {tf: [str(f[tf].index[0]), str(f[tf].index[-1]), len(f[tf])]
                         for tf in f} for s, f in data.items()},
            "seconds": round(time.monotonic() - started, 1),
            "validated_at": datetime.now(UTC).isoformat()}))
        out.write_text(json.dumps(results, indent=1))
        r = results[-1]
        print(code, "PASS" if r["passed"] else "FAIL", r["oos"].get("n"), r["oos"].get("expectancy"),
              r["oos"].get("profit_factor"), r["seconds"], "s", flush=True)
    return results


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--only", nargs="*")
    a = p.parse_args()
    run(a.db, a.label, Path(a.out), a.only)


if __name__ == "__main__":
    main()
