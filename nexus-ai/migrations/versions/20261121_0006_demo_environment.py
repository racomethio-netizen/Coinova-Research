"""allow the 'demo' environment for exchange credentials (Binance Demo Trading)

Binance replaced its futures testnet with Demo Trading (demo-fapi.binance.com). Demo keys
use test funds only and are stored like testnet keys, marked 'demo'.

Revision ID: 0006_demo_environment
Revises: 0005_seed_new_strategies
Create Date: 2026-11-21
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_demo_environment"
down_revision: str | None = "0005_seed_new_strategies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "environment IN ('production', 'sandbox')"
NEW = "environment IN ('production', 'sandbox', 'demo')"


# Raw SQL: Alembic's naming convention would prefix the constraint name a second time.
def _check(expr: str) -> None:
    op.execute("ALTER TABLE exchange_credentials "
               "DROP CONSTRAINT ck_exchange_credentials_environment_valid")
    op.execute("ALTER TABLE exchange_credentials ADD CONSTRAINT "
               f"ck_exchange_credentials_environment_valid CHECK ({expr})")


def upgrade() -> None:
    _check(NEW)


def downgrade() -> None:
    op.execute("UPDATE exchange_credentials SET revoked_at = COALESCE(revoked_at, now()), "
               "environment = 'sandbox' WHERE environment = 'demo'")
    _check(OLD)
