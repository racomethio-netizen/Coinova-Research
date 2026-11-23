"""Engine/session factory. One engine per process; sessions are short-lived."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool


def make_engine(url: str, *, pool_size: int = 5, max_overflow: int = 5,
                pooled: bool = True) -> Engine:
    """``pooled=False`` opens a connection only while it is used (NullPool): for workers
    that run every few minutes or hourly, so they hold no idle connection. Production
    uses Supabase's session-mode pooler, which allows 15 client connections in total
    across every engine of the app, so pools are kept small."""
    args = {"options": "-c timezone=UTC -c statement_timeout=15000"}
    if not pooled:
        return create_engine(url, poolclass=NullPool, connect_args=args)
    return create_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        connect_args=args,
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False, autoflush=False)


@contextmanager
def transaction(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Unit of work: commit on success, roll back on any exception."""
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def ping(engine: Engine) -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
