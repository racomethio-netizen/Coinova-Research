"""Single pre-registered Phase D run (docs/research/2026-11-23-ml-phase-d-preregistration.md).

``python -m nexus_ai.phase_d --db URL --label L --features f2|f1 --out FILE``
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from nexus_backtest.data import load_frames
from nexus_backtest.metrics import json_safe
from nexus_db import make_engine, make_session_factory

from nexus_ai.dataset import build_dataset_v2
from nexus_ai.features import FEATURE_VERSION, FEATURE_VERSION_V2, build_features
from nexus_ai.research import ResearchConfig, walk_forward_v2

PAIRS = ["BTC/USDT", "ETH/USDT"]
LABELS = {"tp_mult": 2.0, "sl_mult": 1.0, "horizon": 48, "fee_rate": 0.001,
          "slippage_rate": 0.0005}


def run(db_url: str, label: str, feature_set: str, out: Path) -> dict[str, Any]:
    started = time.monotonic()
    engine = make_engine(db_url)
    data = load_frames(make_session_factory(engine), "binance", PAIRS, ["15m", "1h", "4h"])
    engine.dispose()
    ds, feats_v2 = build_dataset_v2(data, **LABELS)  # type: ignore[arg-type]
    f1_cols = list(build_features(data[PAIRS[0]]).columns)
    features = feats_v2 if feature_set == "f2" else [c for c in f1_cols if c in ds.columns]
    res = walk_forward_v2(ds, features, ResearchConfig())
    result = json_safe({
        "period": label, "feature_set": feature_set,
        "feature_version": FEATURE_VERSION_V2 if feature_set == "f2" else FEATURE_VERSION,
        "n_features": len(features), "rows": len(ds),
        "data_start": str(ds.index.min()), "data_end": str(ds.index.max()),
        "label_config": LABELS, **res,
        "seconds": round(time.monotonic() - started, 1),
        "run_at": datetime.now(UTC).isoformat()})
    out.write_text(json.dumps(result, indent=1))
    return dict(result)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--features", choices=["f1", "f2"], default="f2")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    pd.set_option("mode.copy_on_write", True)
    r = run(a.db, a.label, a.features, Path(a.out))
    s = r["system"]
    print(a.label, a.features, "PASS" if r["passed"] else "FAIL", s.get("trades"),
          s.get("expectancy"), s.get("profit_factor"), s.get("mean_auc"), r["failures"],
          r["seconds"], "s", flush=True)


if __name__ == "__main__":
    main()
