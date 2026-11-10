"""Experiment registry: every run is recorded with what is needed to reproduce it."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg

from .. import __version__

ENGINE_DIR = Path(__file__).resolve().parents[3]


def reproducibility() -> dict[str, str | None]:
    git_sha = os.environ.get("GIT_SHA")
    if not git_sha:
        try:
            git_sha = (
                subprocess.run(
                    ["git", "rev-parse", "HEAD"],  # noqa: S607 - git resolved from PATH (dev and CI only)
                    cwd=ENGINE_DIR,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=True,
                ).stdout.strip()
                or None
            )
        except (OSError, subprocess.SubprocessError):
            git_sha = None
    lock = ENGINE_DIR / "uv.lock"
    return {
        "git_sha": git_sha,
        "lockfile_hash": hashlib.sha256(lock.read_bytes()).hexdigest() if lock.exists() else None,
        "engine_version": __version__,
    }


def start_experiment(conn: psycopg.Connection, kind: str, strategy_id: int | None, config: dict[str, Any], dataset_hash: str, seed: int) -> str:
    r = reproducibility()
    row = conn.execute(
        "insert into public.experiments (kind, strategy_id, config, dataset_hash, git_sha, lockfile_hash, engine_version, seed) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s) returning id",
        (kind, strategy_id, json.dumps(config, default=str), dataset_hash, r["git_sha"], r["lockfile_hash"], r["engine_version"], seed),
    ).fetchone()
    conn.commit()
    assert row is not None
    return str(row[0])


def finish_experiment(conn: psycopg.Connection, experiment_id: str, metrics: dict[str, Any] | None, error: str | None = None) -> None:
    conn.execute(
        "update public.experiments set status = %s, metrics = %s, error = %s, finished_at = now() where id = %s",
        ("failed" if error else "succeeded", json.dumps(metrics, default=str) if metrics is not None else None, error, experiment_id),
    )
    conn.commit()


def save_validation_report(
    conn: psycopg.Connection, experiment_id: str, strategy_id: int, start: datetime, end: datetime, report: dict[str, Any], assumptions: dict[str, Any]
) -> str:
    row = conn.execute(
        "insert into public.validation_reports (experiment_id, strategy_id, period_start, period_end, n_trades, folds, metrics, calibration, "
        "baselines, assumptions, lockbox) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
        (
            experiment_id, strategy_id, start, end, int(report["samples"]), json.dumps(report["folds"]),
            json.dumps({"out_of_sample": report["out_of_sample"], "approvable": report["approvable"]}),
            json.dumps(report["out_of_sample"].get("reliability")),
            json.dumps([{k: f.get(k) for k in ("test_start", "rules_only", "all_candidates")} for f in report["folds"]]),
            json.dumps(assumptions, default=str),
            json.dumps(report["lockbox"]) if report["lockbox"]["evaluated"] else None,
        ),
    ).fetchone()  # fmt: skip
    conn.commit()
    assert row is not None
    return str(row[0])
