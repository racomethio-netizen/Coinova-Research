"""Structured JSON logging with secret redaction.

Redaction is defense in depth: code must not log secrets in the first place, but if
a dict containing e.g. ``apiKey`` or ``secret`` is logged, its value is replaced.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

REDACTED = "[REDACTED]"

_SENSITIVE_KEY = re.compile(
    r"(pass(word|phrase)?|secret|token|api[_-]?key|apikey|private[_-]?key|authorization|"
    r"cookie|signature|credential|encryption[_-]?key|session)",
    re.IGNORECASE,
)
# Values that look like secrets even under innocent keys (e.g. bearer tokens, PEM).
_SENSITIVE_VALUE = re.compile(r"(-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer\s+\S+)")


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return value
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if isinstance(k, str) and _SENSITIVE_KEY.search(k) else _redact(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return type(value)(_redact(v, depth + 1) for v in value)
    if isinstance(value, str) and _SENSITIVE_VALUE.search(value):
        return _SENSITIVE_VALUE.sub(REDACTED, value)
    return value


def redact_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict.keys()):
        if key == "event":
            event_dict[key] = _redact(event_dict[key])
        elif _SENSITIVE_KEY.search(key):
            event_dict[key] = REDACTED
        else:
            event_dict[key] = _redact(event_dict[key])
    return event_dict


def configure_logging(level: str = "INFO", *, json: bool = True) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level, force=True)
    renderer: Any = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            redact_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
