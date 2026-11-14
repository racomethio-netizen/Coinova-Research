from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import structlog
from nexus_common.decimals import ceil_to_step, floor_to_step, is_multiple_of, to_decimal
from nexus_common.logging import REDACTED, configure_logging, redact_processor
from nexus_common.timeutil import ensure_utc, from_ms, to_ms


class TestToDecimal:
    def test_float_goes_through_repr(self) -> None:
        assert to_decimal(0.1) == Decimal("0.1")
        assert str(to_decimal(0.1)) == "0.1"

    def test_strings_and_ints(self) -> None:
        assert to_decimal("123.4500") == Decimal("123.45")
        assert to_decimal(7) == Decimal(7)

    @pytest.mark.parametrize("bad", ["abc", "", "NaN", "Infinity", float("nan"), float("inf")])
    def test_rejects_non_finite_or_garbage(self, bad: object) -> None:
        with pytest.raises(ValueError):
            to_decimal(bad)

    @pytest.mark.parametrize("bad", [True, None, [1]])
    def test_rejects_wrong_types(self, bad: object) -> None:
        with pytest.raises(TypeError):
            to_decimal(bad)


class TestSteps:
    @pytest.mark.parametrize(
        ("value", "step", "floor", "ceil"),
        [
            ("1.23456", "0.001", "1.234", "1.235"),
            ("1.234", "0.001", "1.234", "1.234"),
            ("0.00001", "0.0001", "0.0000", "0.0001"),
            ("65432.17", "0.5", "65432.0", "65432.5"),
            ("100", "10", "100", "100"),
        ],
    )
    def test_floor_ceil(self, value: str, step: str, floor: str, ceil: str) -> None:
        assert floor_to_step(Decimal(value), Decimal(step)) == Decimal(floor)
        assert ceil_to_step(Decimal(value), Decimal(step)) == Decimal(ceil)

    def test_multiple(self) -> None:
        assert is_multiple_of(Decimal("0.003"), Decimal("0.001"))
        assert not is_multiple_of(Decimal("0.0035"), Decimal("0.001"))

    def test_invalid_step(self) -> None:
        with pytest.raises(ValueError):
            floor_to_step(Decimal(1), Decimal(0))


class TestTime:
    def test_roundtrip(self) -> None:
        dt = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        assert from_ms(to_ms(dt)) == dt

    def test_naive_rejected(self) -> None:
        with pytest.raises(ValueError):
            ensure_utc(datetime(2026, 1, 1))  # noqa: DTZ001


class TestRedaction:
    def test_redacts_sensitive_keys_recursively(self) -> None:
        event = {
            "event": "x",
            "api_key": "AKIA123",
            "nested": {"secret": "s3", "Password": "p", "ok": 1, "list": [{"token": "t"}]},
            "headers": "Authorization: Bearer abc.def",
        }
        out = redact_processor(None, "info", event)
        assert out["api_key"] == REDACTED
        assert out["nested"]["secret"] == REDACTED
        assert out["nested"]["Password"] == REDACTED
        assert out["nested"]["ok"] == 1
        assert out["nested"]["list"][0]["token"] == REDACTED
        assert "abc.def" not in out["headers"]

    def test_end_to_end_json_log(self, capsys: pytest.CaptureFixture[str]) -> None:
        configure_logging("INFO", json=True)
        structlog.get_logger("t").info("connect", apiKey="LEAKME", exchange="binance")
        line = capsys.readouterr().out.strip().splitlines()[-1]
        data = json.loads(line)
        assert data["apiKey"] == REDACTED and data["exchange"] == "binance"
        assert "LEAKME" not in line
        logging.getLogger().handlers.clear()
        structlog.reset_defaults()
