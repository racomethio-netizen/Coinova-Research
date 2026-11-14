"""Alembic environment. URL from NEXUS_DATABASE_URL; models from nexus_db."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from nexus_db import Base
from sqlalchemy import create_engine, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    url = os.environ.get("NEXUS_DATABASE_URL")
    if not url:
        raise RuntimeError("NEXUS_DATABASE_URL is not set")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True,
                      compare_type=True, compare_server_default=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()
        # Re-apply least-privilege grants after every upgrade so new tables are covered.
        if context.get_x_argument(as_dictionary=True).get("skip_grants") is None:
            from importlib import import_module

            grants = import_module("migrations.versions.20261114_0003_app_role_grants").GRANTS
            if connection.in_transaction():
                connection.commit()
            connection.exec_driver_sql(grants)
            connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
