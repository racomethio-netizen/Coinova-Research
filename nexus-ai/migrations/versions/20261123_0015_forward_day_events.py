"""forward test ledger: 'day' and 'divergence' event kinds (daily trend paper test)

Revision ID: 0015_forward_day_events
Revises: 0014_decision_logs
Create Date: 2026-11-23
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0015_forward_day_events"
down_revision: str | None = "0014_decision_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE forward_test_events DROP CONSTRAINT ck_forward_test_events_kind_valid")
    op.execute("ALTER TABLE forward_test_events ADD CONSTRAINT ck_forward_test_events_kind_valid "
               "CHECK (kind IN ('trade', 'gap', 'day', 'divergence'))")


def downgrade() -> None:
    # The ledger is append-only: rows of the new kinds cannot be deleted, so a downgrade
    # is refused while any exist (fails on the constraint) rather than losing history.
    op.execute("ALTER TABLE forward_test_events DROP CONSTRAINT ck_forward_test_events_kind_valid")
    op.execute("ALTER TABLE forward_test_events ADD CONSTRAINT ck_forward_test_events_kind_valid "
               "CHECK (kind IN ('trade', 'gap'))")
