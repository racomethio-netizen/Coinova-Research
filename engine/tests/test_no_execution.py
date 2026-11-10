"""Static guarantee (docs/nexus/SECURITY_RISKS.md R-12): the engine contains no code
that could authenticate to an exchange, place orders, or move funds."""

import re
from pathlib import Path

import pytest

from nexus_engine.execution.disabled import DisabledExecutionAdapter, LiveExecutionDisabledError

SRC = Path(__file__).resolve().parents[1] / "src"
FORBIDDEN = {
    "exchange API key header": re.compile(r"X-MBX-APIKEY", re.I),
    "order endpoint": re.compile(r"/api/v\d/order|/fapi/|/sapi/|/dapi/"),
    "withdrawal": re.compile(r"withdraw", re.I),
    "request signing": re.compile(r"hmac\.new\(|signature\s*=", re.I),
    "general exchange SDK": re.compile(r"\bccxt\b|python-binance|binance\.client", re.I),
}


def test_no_trading_or_signing_code_exists() -> None:
    hits = []
    for f in SRC.rglob("*.py"):
        text = f.read_text()
        for name, pattern in FORBIDDEN.items():
            if pattern.search(text):
                hits.append(f"{f.relative_to(SRC)}: {name}")
    assert hits == []


def test_execution_adapter_refuses() -> None:
    with pytest.raises(LiveExecutionDisabledError):
        DisabledExecutionAdapter().place_order("BTCUSDT", "buy", 1)
