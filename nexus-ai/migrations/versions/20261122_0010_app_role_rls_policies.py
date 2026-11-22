"""RLS policies that let the application role through

Tables with row-level security enabled and no policies deny every role that does not own
them. That is intended for Supabase's public API roles (anon, authenticated), but it also
locked out ``nexus_app``, the least-privilege role the services are meant to use. This adds
one permissive policy per RLS-enabled table FOR ``nexus_app`` ONLY; what the role may do
is still limited by its grants (append-only ledgers and audit log, no DDL). Skipped if the
role does not exist; ``scripts/grant_app_role.sql`` applies the same when it is created
later.

Revision ID: 0010_app_role_rls
Revises: 0009_forward_tests
Create Date: 2026-11-22
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010_app_role_rls"
down_revision: str | None = "0009_forward_tests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICIES = """
DO $$
DECLARE t record;
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexus_app') THEN
    FOR t IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity LOOP
      IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public'
                     AND tablename = t.relname AND policyname = 'nexus_app_access') THEN
        EXECUTE format('CREATE POLICY nexus_app_access ON public.%I FOR ALL TO nexus_app '
                       'USING (true) WITH CHECK (true)', t.relname);
      END IF;
    END LOOP;
  END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(POLICIES)


def downgrade() -> None:
    op.execute("""
    DO $$
    DECLARE t record;
    BEGIN
      FOR t IN SELECT tablename FROM pg_policies
               WHERE schemaname = 'public' AND policyname = 'nexus_app_access' LOOP
        EXECUTE format('DROP POLICY nexus_app_access ON public.%I', t.tablename);
      END LOOP;
    END $$;
    """)
