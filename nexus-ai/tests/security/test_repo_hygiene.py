"""Static security checks over the tracked source tree."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,  # noqa: S607
                         check=True).stdout
    return [ROOT / f for f in out.splitlines() if (ROOT / f).is_file()]


def source(globs: tuple[str, ...] = (".py", ".ts", ".tsx", ".sh", ".yml", ".yaml", ".toml",
                                     ".ini", ".md", ".conf")) -> list[Path]:
    return [p for p in tracked_files() if p.suffix in globs and "node_modules" not in p.parts]


SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "telegram bot token": re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b"),
    "aws key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "hardcoded secret assignment": re.compile(
        r"""(?i)\b(api_secret|secret_key|password)\s*=\s*["'][A-Za-z0-9+/=_-]{24,}["']"""),
}


def test_no_secrets_committed() -> None:
    hits = []
    for path in source():
        if "tests" in path.parts:
            continue
        text = path.read_text(errors="ignore")
        for name, pat in SECRET_PATTERNS.items():
            if pat.search(text):
                hits.append(f"{path.relative_to(ROOT)}: {name}")
    assert hits == []


def test_no_env_files_tracked() -> None:
    bad = [p for p in tracked_files() if p.name.startswith(".env") and p.name != ".env.example"]
    assert bad == []


@pytest.mark.parametrize(("pattern", "why"), [
    (r"verify\s*=\s*False|CERT_NONE|check_hostname\s*=\s*False", "TLS verification disabled"),
    (r"shell\s*=\s*True", "shell injection risk"),
    (r"\beval\(|\bexec\(", "dynamic code execution"),
    (r"yaml\.load\(", "unsafe YAML load"),
])
def test_dangerous_python_patterns(pattern: str, why: str) -> None:
    hits = [str(p.relative_to(ROOT)) for p in source((".py",))
            if "tests" not in p.parts and re.search(pattern, p.read_text())]
    assert hits == [], why


def test_pickle_only_loaded_after_hash_check() -> None:
    users = [p for p in source((".py",)) if "pickle.loads" in p.read_text() and "tests" not in p.parts]
    assert [p.name for p in users] == ["model.py"]
    text = users[0].read_text()
    assert text.index("hash mismatch") < text.index("pickle.loads")


def test_live_broker_only_built_by_gate() -> None:
    allowed = {"brokers.py", "live_gate.py"}
    hits = [p.name for p in source((".py",))
            if "tests" not in p.parts and "LiveBroker(" in p.read_text() and p.name not in allowed]
    assert hits == []


MONEY_MODULES = ["services/risk-engine/nexus_risk/sizing.py",
                 "services/execution-engine/nexus_execution/accounting.py",
                 "services/execution-engine/nexus_execution/order_manager.py",
                 "services/execution-engine/nexus_execution/paper.py"]


@pytest.mark.parametrize("module", MONEY_MODULES)
def test_no_float_in_money_code(module: str) -> None:
    text = (ROOT / module).read_text()
    assert not re.search(r"\bfloat\(|: float\b", text), f"float arithmetic in {module}"


def test_dashboard_exposes_no_secrets() -> None:
    dash = [p for p in source((".ts", ".tsx")) if "apps/dashboard" in str(p)]
    assert dash
    for p in dash:
        text = p.read_text()
        assert "NEXT_PUBLIC_" not in text, p
        assert "dangerouslySetInnerHTML" not in text, p
        assert not re.search(r"localStorage\.setItem\([^)]*(token|session|secret)", text, re.I), p


def test_compose_binds_localhost_only_and_drops_capabilities() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()
    for line in compose.splitlines():
        if "ports:" in line:
            assert "127.0.0.1:" in line, line
    assert compose.count('cap_drop: ["ALL"]') >= 1 and "<<: *python-service" in compose


def test_no_withdrawal_or_margin_calls() -> None:
    forbidden = re.compile(r"\.(withdraw|transfer|borrow|set_leverage|set_margin_mode|"
                           r"create_margin|fetch_positions)\(")
    hits = [str(p.relative_to(ROOT)) for p in source((".py",))
            if "tests" not in p.parts and forbidden.search(p.read_text())]
    assert hits == []
