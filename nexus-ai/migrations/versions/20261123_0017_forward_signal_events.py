"""forward test ledger: 'signal' event kind (global strategy library forward test)

Revision ID: 0017_forward_signal_events
Revises: 0016_spot_engine_strategies
Create Date: 2026-11-23
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0017_forward_signal_events"
down_revision: str | None = "0016_spot_engine_strategies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE forward_test_events DROP CONSTRAINT ck_forward_test_events_kind_valid")
    op.execute("ALTER TABLE forward_test_events ADD CONSTRAINT ck_forward_test_events_kind_valid "
               "CHECK (kind IN ('trade', 'gap', 'day', 'divergence', 'signal'))")


def downgrade() -> None:
    # Append-only ledger: refused (constraint violation) while 'signal' rows exist.
    op.execute("ALTER TABLE forward_test_events DROP CONSTRAINT ck_forward_test_events_kind_valid")
    op.execute("ALTER TABLE forward_test_events ADD CONSTRAINT ck_forward_test_events_kind_valid "
               "CHECK (kind IN ('trade', 'gap', 'day', 'divergence'))")
