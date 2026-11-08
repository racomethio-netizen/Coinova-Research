-- Fix: audit chain order under concurrency.
-- Previously "seq" came from the column default, evaluated BEFORE the trigger took the
-- chain lock. Two concurrent writers could then link in the opposite order to their seq,
-- and audit_log_verify_chain() (which walks by seq) reported a false break.
-- Now seq is drawn AFTER the lock, so seq order == chain order. Gaps in seq are harmless.
CREATE OR REPLACE FUNCTION public.audit_log_chain() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
DECLARE
  last_hash text;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('tamagne.audit_logs'));
  NEW.seq := nextval(pg_get_serial_sequence('public.audit_logs', 'seq'));
  SELECT hash INTO last_hash FROM public.audit_logs ORDER BY seq DESC LIMIT 1;
  NEW."prevHash" := last_hash;
  NEW.hash := public.audit_log_row_hash(last_hash, NEW);
  RETURN NEW;
END $$;
