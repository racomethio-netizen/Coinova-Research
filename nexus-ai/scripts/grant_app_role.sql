-- Least-privilege grants for the application role `nexus_app` (idempotent), plus one
-- RLS policy per RLS-enabled table for nexus_app only (migration 0010).
-- Run as the schema owner AFTER creating the role (see NEXUS_DEPLOYMENT_GUIDE.md, step A4)
-- and again after any migration run made before the role existed. Mirrors migrations
-- 0003 (base grants), 0008 and 0009 (append-only research ledgers).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexus_app') THEN
    RAISE EXCEPTION 'create the nexus_app role first';
  END IF;
  EXECUTE 'GRANT USAGE ON SCHEMA public TO nexus_app';
  EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM nexus_app';
  EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO nexus_app';
  EXECUTE 'REVOKE UPDATE, DELETE ON audit_logs FROM nexus_app';
  EXECUTE 'REVOKE INSERT, DELETE ON exchanges, strategies FROM nexus_app';
  EXECUTE 'REVOKE UPDATE, DELETE ON carry_paper_events, forward_test_events FROM nexus_app';
  EXECUTE 'REVOKE ALL ON alembic_version FROM nexus_app';
  EXECUTE 'GRANT SELECT ON alembic_version TO nexus_app';
  EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO nexus_app';
END $$;

DO $$
DECLARE t record;
BEGIN
  FOR t IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public'
                   AND tablename = t.relname AND policyname = 'nexus_app_access') THEN
      EXECUTE format('CREATE POLICY nexus_app_access ON public.%I FOR ALL TO nexus_app '
                     'USING (true) WITH CHECK (true)', t.relname);
    END IF;
  END LOOP;
END $$;
