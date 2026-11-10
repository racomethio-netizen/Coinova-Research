"""PostgreSQL access as the least-privilege role `nexus_engine` (ADR-002)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from .config import Settings


class DatabaseNotConfiguredError(RuntimeError):
    pass


@contextmanager
def connect(settings: Settings) -> Iterator[psycopg.Connection]:
    if settings.database_url is None:
        raise DatabaseNotConfiguredError("DATABASE_URL is not set")
    with psycopg.connect(settings.database_url.get_secret_value(), connect_timeout=10, application_name="nexus-engine") as conn:
        yield conn
