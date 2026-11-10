"""Nightly logical export of the signal ledger, its verification, and a restore drill (R-02, R-17).

An export is a directory `ledger-<UTC timestamp>/` with one JSON Lines file per
ledger table and a `manifest.json` (row counts, SHA-256 per file, chain head).
Values are written in the same canonical form the hash chain uses, so the chain
can be recomputed from the files alone, without the database.

* `export_ledger` reads one consistent snapshot (REPEATABLE READ) and verifies the
  chain while writing; the manifest records whether it was intact.
* `verify_export` re-checks files, hashes, the chain and references, and, given the
  previous manifest, that the earlier chain head is still present unchanged
  (a rewrite between two exports is detected even if the new chain is self-consistent).
* `restore_drill` loads an export into typed temporary tables and recomputes the
  chain from the restored rows, proving the export round-trips through Postgres.

Annotation authors are not exported (personal data is not needed to audit the ledger).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import IsolationLevel
from psycopg.rows import dict_row

from ..signals.hashing import HASHED_FIELDS, normalize, verify_chain

EXPORT_FORMAT = 1
MANIFEST = "manifest.json"

# table -> (columns, order by)
TABLES: dict[str, tuple[tuple[str, ...], str]] = {
    "signals": (("seq", *HASHED_FIELDS, "content_hash"), "seq"),
    "signal_events": (("id", "signal_id", "event_type", "occurred_at", "price", "created_at"), "id"),
    "signal_outcomes": (
        ("signal_id", "outcome", "entry_price", "exit_price", "net_return", "r_multiple", "closed_at", "resolver_version", "resolved_at"),
        "signal_id",
    ),
    "signal_annotations": (("id", "signal_id", "kind", "body", "created_at"), "id"),
}

# Typed temporary tables for the restore drill (same column types as the migrations).
_RESTORE_DDL = {
    "signals": """create temp table r_signals (
        seq bigint primary key, id uuid not null unique, evaluation_id uuid not null, pair_id bigint not null, timeframe text not null,
        strategy_id smallint not null, model_version_id uuid, direction text not null, as_of timestamptz not null,
        published_at timestamptz not null, entry_low numeric(30, 8) not null, entry_high numeric(30, 8) not null,
        stop_loss numeric(30, 8) not null, take_profits numeric(30, 8)[] not null, risk_reward numeric(10, 4) not null,
        confidence numeric(6, 4), confidence_band text not null, entry_expires_at timestamptz not null, horizon_bars integer not null,
        assumptions jsonb not null, explanation jsonb not null, visibility text not null, prev_hash text, content_hash text not null)""",
    "signal_events": """create temp table r_signal_events (id bigint primary key, signal_id uuid not null references r_signals (id),
        event_type text not null, occurred_at timestamptz not null, price numeric(30, 8), created_at timestamptz not null)""",
    "signal_outcomes": """create temp table r_signal_outcomes (signal_id uuid primary key references r_signals (id), outcome text not null,
        entry_price numeric(30, 8), exit_price numeric(30, 8), net_return numeric(14, 8), r_multiple numeric(14, 4),
        closed_at timestamptz not null, resolver_version text not null, resolved_at timestamptz not null)""",
    "signal_annotations": """create temp table r_signal_annotations (id bigint primary key, signal_id uuid not null references r_signals (id),
        kind text not null, body text not null, created_at timestamptz not null)""",
}
_JSONB = {"assumptions", "explanation"}


class LedgerExportError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def export_ledger(conn: psycopg.Connection, out_root: Path, now: datetime | None = None) -> tuple[Path, dict[str, Any]]:
    """Write a new export under `out_root`. The connection must be idle (no open transaction)."""
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        raise LedgerExportError("connection has an open transaction; export needs its own snapshot")
    stamp = (now or datetime.now(UTC)).astimezone(UTC)
    target = out_root / f"ledger-{stamp.strftime('%Y%m%dT%H%M%SZ')}"
    if target.exists():
        raise LedgerExportError(f"{target.name} already exists")
    tmp = out_root / f".{target.name}.partial"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)

    files: dict[str, dict[str, Any]] = {}
    chain_rows: list[dict[str, Any]] = []
    previous_level = conn.isolation_level
    conn.isolation_level = IsolationLevel.REPEATABLE_READ  # one consistent snapshot across all tables
    try:
        with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            for table, (cols, order) in TABLES.items():
                path = tmp / f"{table}.jsonl"
                count = 0
                cur.execute(f"select {', '.join(cols)} from public.{table} order by {order}")  # noqa: S608 - fixed identifiers
                with path.open("w", encoding="utf-8") as f:
                    for row in cur:
                        rec = {k: normalize(row[k]) for k in cols}
                        if table == "signals":
                            chain_rows.append(rec)
                        f.write(_dumps(rec) + "\n")
                        count += 1
                    f.flush()
                    os.fsync(f.fileno())
                files[table] = {"file": path.name, "rows": count, "sha256": _sha256(path)}
    finally:
        conn.isolation_level = previous_level

    problems = verify_chain(chain_rows)
    head = chain_rows[-1] if chain_rows else None
    manifest: dict[str, Any] = {
        "format": EXPORT_FORMAT,
        "exported_at": stamp.isoformat(timespec="seconds"),
        "files": files,
        "chain": {
            "length": len(chain_rows),
            "head_seq": head["seq"] if head else None,
            "head_hash": head["content_hash"] if head else None,
            "intact": not problems,
            "problems": problems[:20],
        },
    }
    (tmp / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.rename(target)  # the export appears complete or not at all
    return target, manifest


def load_manifest(path: Path) -> dict[str, Any]:
    p = path / MANIFEST if path.is_dir() else path
    data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    if data.get("format") != EXPORT_FORMAT:
        raise LedgerExportError(f"{p}: unsupported export format {data.get('format')!r}")
    return data


def verify_export(export_dir: Path, previous: Mapping[str, Any] | None = None) -> list[str]:
    """Return a list of problems (empty means the export is complete, intact and continuous)."""
    problems: list[str] = []
    try:
        manifest = load_manifest(export_dir)
    except (OSError, ValueError, LedgerExportError) as e:
        return [f"manifest unreadable: {e}"]

    rows: dict[str, list[dict[str, Any]]] = {}
    for table in TABLES:
        meta = manifest["files"].get(table)
        path = export_dir / f"{table}.jsonl"
        if meta is None or not path.exists():
            problems.append(f"{table}: file missing")
            continue
        if _sha256(path) != meta["sha256"]:
            problems.append(f"{table}: SHA-256 does not match the manifest")
        rows[table] = list(_read_jsonl(path))
        if len(rows[table]) != meta["rows"]:
            problems.append(f"{table}: {len(rows[table])} rows, manifest says {meta['rows']}")
    if problems:
        return problems

    signals = rows["signals"]
    problems += verify_chain(signals)
    seqs = [r["seq"] for r in signals]
    if seqs != sorted(seqs):
        problems.append("signals: not in seq order")
    ids = {r["id"] for r in signals}
    for table in ("signal_events", "signal_outcomes", "signal_annotations"):
        orphans = sum(1 for r in rows[table] if r["signal_id"] not in ids)
        if orphans:
            problems.append(f"{table}: {orphans} row(s) reference signals missing from the export")

    if previous is not None:
        prev_chain = previous["chain"]
        if prev_chain["head_seq"] is not None:
            match = next((r for r in signals if r["seq"] == prev_chain["head_seq"]), None)
            if match is None:
                problems.append(f"continuity: previous head seq {prev_chain['head_seq']} is missing")
            elif match["content_hash"] != prev_chain["head_hash"]:
                problems.append(f"continuity: signal seq {prev_chain['head_seq']} changed since the previous export")
        if len(signals) < prev_chain["length"]:
            problems.append(f"continuity: chain shrank from {prev_chain['length']} to {len(signals)} signals")
        for table in ("signal_events", "signal_outcomes", "signal_annotations"):
            before = previous["files"].get(table, {}).get("rows", 0)
            if len(rows[table]) < before:
                problems.append(f"continuity: {table} shrank from {before} to {len(rows[table])} rows")
    return problems


def latest_export(out_root: Path) -> Path | None:
    dirs = sorted(p for p in out_root.glob("ledger-*") if p.is_dir() and (p / MANIFEST).exists())
    return dirs[-1] if dirs else None


def prune_exports(out_root: Path, keep: int) -> list[Path]:
    dirs = sorted(p for p in out_root.glob("ledger-*") if p.is_dir())
    removed = dirs[:-keep] if keep > 0 else []
    for p in removed:
        shutil.rmtree(p)
    return removed


def _restore_value(table: str, col: str, v: Any) -> Any:
    # Strings are sent untyped, so Postgres parses them with the column's own type.
    if table == "signals" and col in _JSONB:
        return json.dumps(v)
    if table == "signals" and col == "take_profits":
        return "{" + ",".join(str(x) for x in v) + "}"
    return v


def restore_drill(conn: psycopg.Connection, export_dir: Path) -> dict[str, Any]:
    """Load the export into typed temporary tables, read it back and re-verify the chain.

    Nothing outside the session's temporary schema is touched; the tables are dropped afterwards
    (and rolled back with the transaction if the drill fails midway).
    """
    problems = verify_export(export_dir)
    if problems:
        return {"ok": False, "problems": problems, "restored": {}}
    restored: dict[str, int] = {}
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        for table, ddl in _RESTORE_DDL.items():
            cur.execute(ddl)  # fixed DDL
            cols = TABLES[table][0]
            sql = f"insert into r_{table} ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))})"  # noqa: S608
            data = [tuple(_restore_value(table, c, r[c]) for c in cols) for r in _read_jsonl(export_dir / f"{table}.jsonl")]
            cur.executemany(sql, data)
            cur.execute(f"select count(*) as n from r_{table}")  # noqa: S608
            row = cur.fetchone()
            restored[table] = int(row["n"]) if row else 0
        cur.execute(f"select {', '.join(TABLES['signals'][0])} from r_signals order by seq")  # noqa: S608
        back = cur.fetchall()
        problems = verify_chain([{k: normalize(v) for k, v in r.items()} for r in back])
        cur.execute("drop table r_signal_annotations, r_signal_outcomes, r_signal_events, r_signals")
    manifest = load_manifest(export_dir)
    for table, n in restored.items():
        if n != manifest["files"][table]["rows"]:
            problems.append(f"{table}: restored {n} rows, manifest says {manifest['files'][table]['rows']}")
    return {"ok": not problems, "problems": problems, "restored": restored}


def run_nightly(conn: psycopg.Connection, out_root: Path, keep: int, now: datetime | None = None) -> dict[str, Any]:
    """Export, verify against the previous export, prune old exports. Returns a heartbeat-safe summary."""
    out_root.mkdir(parents=True, exist_ok=True)
    previous_dir = latest_export(out_root)
    previous = load_manifest(previous_dir) if previous_dir else None
    out, manifest = export_ledger(conn, out_root, now)
    problems = verify_export(out, previous)
    pruned = prune_exports(out_root, keep)
    chain = manifest["chain"]
    return {
        "export": out.name,
        "previous": previous_dir.name if previous_dir else None,
        "chain_length": chain["length"],
        "head_seq": chain["head_seq"],
        "head_hash": chain["head_hash"],
        "intact": bool(chain["intact"]) and not problems,
        "problems": problems[:20],
        "pruned": len(pruned),
    }
