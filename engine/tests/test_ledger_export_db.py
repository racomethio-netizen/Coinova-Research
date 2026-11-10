"""Ledger export, verification, continuity and restore drill against real Postgres (R-02, R-17)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from test_signals_db import market, service  # noqa: F401 - pytest fixture reuse

from nexus_engine.ops.ledger import (
    MANIFEST,
    LedgerExportError,
    export_ledger,
    latest_export,
    load_manifest,
    prune_exports,
    restore_drill,
    verify_export,
)

pytestmark = pytest.mark.db


@pytest.fixture
def ledger(engine_conn: psycopg.Connection, superuser: psycopg.Connection, market):  # noqa: F811
    """One published, resolved signal with an annotation."""
    pair, as_of, add_future = market
    svc = service(engine_conn, as_of)
    assert svc.evaluate(pair, "1h", as_of) is not None
    add_future()
    assert svc.resolve_open() == 1
    engine_conn.commit()
    superuser.execute("insert into public.signal_annotations (signal_id, kind, body) select id, 'note', 'Synthetic test note.' from public.signals")
    return engine_conn


def test_export_verify_and_restore(ledger: psycopg.Connection, tmp_path: Path) -> None:
    out, manifest = export_ledger(ledger, tmp_path, now=datetime(2026, 11, 10, 2, 0, tzinfo=UTC))
    assert out.name == "ledger-20261110T020000Z" and not list(tmp_path.glob(".*partial"))
    assert manifest["chain"]["length"] == 1 and manifest["chain"]["intact"]
    assert manifest["files"]["signal_outcomes"]["rows"] == 1 and manifest["files"]["signal_annotations"]["rows"] == 1
    assert manifest["files"]["signal_events"]["rows"] >= 1
    head = ledger.execute("select seq, content_hash from public.signals").fetchone()
    assert head == (manifest["chain"]["head_seq"], manifest["chain"]["head_hash"])
    assert "author_id" not in (out / "signal_annotations.jsonl").read_text()
    assert verify_export(out) == []

    drill = restore_drill(ledger, out)
    assert drill["ok"], drill["problems"]
    assert drill["restored"] == {t: m["rows"] for t, m in manifest["files"].items()}
    assert ledger.execute("select to_regclass('pg_temp.r_signals')").fetchone() == (None,)  # dropped after the drill


def test_modified_files_are_detected(ledger: psycopg.Connection, tmp_path: Path) -> None:
    out, _ = export_ledger(ledger, tmp_path)
    path = out / "signals.jsonl"
    rec = json.loads(path.read_text())
    rec["stop_loss"] = "1.00000000"
    path.write_text(json.dumps(rec) + "\n")
    assert any("SHA-256" in p for p in verify_export(out))

    # Even with the manifest checksum updated to match, the hash chain exposes the edit.
    import hashlib

    m = load_manifest(out)
    m["files"]["signals"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (out / MANIFEST).write_text(json.dumps(m))
    problems = verify_export(out)
    assert any("does not match its hash" in p for p in problems)
    assert not restore_drill(ledger, out)["ok"]


def test_continuity_against_the_previous_export(ledger: psycopg.Connection, tmp_path: Path) -> None:
    first, m1 = export_ledger(ledger, tmp_path, now=datetime(2026, 11, 9, 2, tzinfo=UTC))
    second, _ = export_ledger(ledger, tmp_path, now=datetime(2026, 11, 10, 2, tzinfo=UTC))
    assert verify_export(second, previous=m1) == []
    assert latest_export(tmp_path) == second

    rewritten = {**m1, "chain": {**m1["chain"], "head_hash": "0" * 64}}
    assert any("changed since the previous export" in p for p in verify_export(second, previous=rewritten))
    longer = {**m1, "chain": {**m1["chain"], "length": 5}}
    assert any("chain shrank" in p for p in verify_export(second, previous=longer))
    more_outcomes = {**m1, "files": {**m1["files"], "signal_outcomes": {**m1["files"]["signal_outcomes"], "rows": 9}}}
    assert any("signal_outcomes shrank" in p for p in verify_export(second, previous=more_outcomes))
    assert first.exists()


def test_tampered_database_is_reported_in_the_manifest(ledger: psycopg.Connection, superuser: psycopg.Connection, tmp_path: Path) -> None:
    superuser.execute("alter table public.signals disable trigger signals_chain_guard")
    try:
        superuser.execute("update public.signals set stop_loss = stop_loss - 1")
    finally:
        superuser.execute("alter table public.signals enable trigger signals_chain_guard")
    out, manifest = export_ledger(ledger, tmp_path)
    assert manifest["chain"]["intact"] is False and manifest["chain"]["problems"]
    assert verify_export(out)


def test_export_needs_an_idle_connection_and_pruning_keeps_the_newest(ledger: psycopg.Connection, tmp_path: Path) -> None:
    ledger.execute("select 1")
    with pytest.raises(LedgerExportError):
        export_ledger(ledger, tmp_path)
    ledger.rollback()
    t = datetime(2026, 11, 1, 2, tzinfo=UTC)
    for i in range(4):
        export_ledger(ledger, tmp_path, now=t + timedelta(days=i))
    with pytest.raises(LedgerExportError):
        export_ledger(ledger, tmp_path, now=t)  # never overwrites an export
    removed = prune_exports(tmp_path, keep=2)
    assert [p.name for p in removed] == ["ledger-20261101T020000Z", "ledger-20261102T020000Z"]
    assert sorted(p.name for p in tmp_path.glob("ledger-*")) == ["ledger-20261103T020000Z", "ledger-20261104T020000Z"]
