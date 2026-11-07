-- TAMAGNE integrity rules that Prisma's schema language cannot express.
-- The application validates these too; the database is the last line of defense.
--
-- Production guard: on the production database run once (as owner):
--   ALTER DATABASE <db> SET tamagne.environment = 'production';
-- With that set, sandbox identity results can neither be stored nor back a badge.

-- ───────────────────────── helpers ─────────────────────────

CREATE FUNCTION public.tamagne_is_production() RETURNS boolean
LANGUAGE sql STABLE SET search_path = '' AS $$
  SELECT coalesce(current_setting('tamagne.environment', true), '') = 'production'
$$;

CREATE FUNCTION public.tamagne_reject_mutation() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  RAISE EXCEPTION 'table % is append-only (% rejected)', TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'insufficient_privilege';
END $$;

-- ───────────────────────── users & auth ─────────────────────────

ALTER TABLE public.users
  ADD CONSTRAINT users_phone_e164 CHECK ("phoneE164" IS NULL OR "phoneE164" ~ '^\+[1-9][0-9]{7,14}$'),
  ADD CONSTRAINT users_email_lower CHECK (email IS NULL OR email = lower(email)),
  ADD CONSTRAINT users_failed_login_nonneg CHECK ("failedLoginCount" >= 0),
  ADD CONSTRAINT users_deleted_is_scrubbed CHECK (
    status <> 'DELETED' OR ("phoneE164" IS NULL AND email IS NULL AND "passwordHash" IS NULL AND "deletedAt" IS NOT NULL)
  );

ALTER TABLE public.sessions
  ADD CONSTRAINT sessions_expiry_after_creation CHECK ("expiresAt" > "createdAt");

ALTER TABLE public.phone_otp_challenges
  ADD CONSTRAINT otp_phone_e164 CHECK ("phoneE164" ~ '^\+[1-9][0-9]{7,14}$'),
  ADD CONSTRAINT otp_attempts_bounds CHECK ("maxAttempts" BETWEEN 1 AND 10 AND attempts BETWEEN 0 AND "maxAttempts"),
  ADD CONSTRAINT otp_short_lived CHECK ("expiresAt" > "createdAt" AND "expiresAt" <= "createdAt" + interval '15 minutes');

ALTER TABLE public.staff_role_assignments
  ADD CONSTRAINT role_not_self_granted CHECK ("grantedById" IS NULL OR "grantedById" <> "userId"),
  ADD CONSTRAINT role_revocation_complete CHECK (("revokedAt" IS NULL) = ("revokedById" IS NULL));
CREATE UNIQUE INDEX staff_role_one_live_grant ON public.staff_role_assignments ("userId", role) WHERE "revokedAt" IS NULL;

-- Revoked grants are history: they cannot be edited or re-activated.
CREATE FUNCTION public.staff_role_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'staff role grants cannot be deleted; revoke instead' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD."revokedAt" IS NOT NULL THEN
    RAISE EXCEPTION 'revoked staff role grant is immutable' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW."userId" <> OLD."userId" OR NEW.role <> OLD.role OR NEW."grantedAt" <> OLD."grantedAt"
     OR NEW."grantedById" IS DISTINCT FROM OLD."grantedById" THEN
    RAISE EXCEPTION 'staff role grant fields are immutable' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER staff_role_guard BEFORE UPDATE OR DELETE ON public.staff_role_assignments
  FOR EACH ROW EXECUTE FUNCTION public.staff_role_guard();

CREATE FUNCTION public.tamagne_has_staff_role(p_user uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path = '' AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.staff_role_assignments a
    WHERE a."userId" = p_user AND a."revokedAt" IS NULL
  )
$$;

-- ───────────────────────── sellers & businesses ─────────────────────────

ALTER TABLE public.seller_profiles
  ADD CONSTRAINT seller_tamagne_id_format CHECK ("tamagneId" ~ '^TMG-[0-9A-HJKMNP-TV-Z]{10}$'),
  ADD CONSTRAINT seller_display_name_nonblank CHECK (length(btrim("displayName")) >= 2),
  ADD CONSTRAINT seller_suspension_explained CHECK (
    "accountStatus" <> 'SUSPENDED' OR ("suspendedAt" IS NOT NULL AND "suspensionReason" IS NOT NULL)
  );

ALTER TABLE public.businesses
  ADD CONSTRAINT business_legal_name_nonblank CHECK (length(btrim("legalName")) >= 2),
  ADD CONSTRAINT business_tin_format CHECK ("tinNumber" IS NULL OR "tinNumber" ~ '^[0-9]{10}$');
-- A TIN / registration number can back only one verified business (duplicate-seller defence).
CREATE UNIQUE INDEX business_verified_tin ON public.businesses ("tinNumber") WHERE status = 'VERIFIED' AND "tinNumber" IS NOT NULL;
CREATE UNIQUE INDEX business_verified_registration ON public.businesses ("registrationNumber") WHERE status = 'VERIFIED' AND "registrationNumber" IS NOT NULL;

ALTER TABLE public.business_documents
  ADD CONSTRAINT doc_size_bounds CHECK ("sizeBytes" > 0 AND "sizeBytes" <= 10485760),
  ADD CONSTRAINT doc_mime_allowlist CHECK ("mimeType" IN ('application/pdf', 'image/jpeg', 'image/png')),
  ADD CONSTRAINT doc_sha256_hex CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  -- A document can only be approved after a clean malware scan.
  ADD CONSTRAINT doc_approved_requires_clean_scan CHECK ("reviewStatus" <> 'APPROVED' OR "scanStatus" = 'CLEAN'),
  ADD CONSTRAINT doc_rejection_explained CHECK (
    "reviewStatus" NOT IN ('REJECTED', 'NEEDS_RESUBMISSION') OR "rejectionReason" IS NOT NULL
  ),
  ADD CONSTRAINT doc_scan_timestamped CHECK ("scanStatus" = 'PENDING' OR "scannedAt" IS NOT NULL);

-- ───────────────────────── consent ─────────────────────────

ALTER TABLE public.consent_records
  ADD CONSTRAINT consent_policy_version_nonblank CHECK (length(btrim("policyVersion")) >= 1),
  ADD CONSTRAINT consent_withdrawal_consistent CHECK ((status = 'WITHDRAWN') = ("withdrawnAt" IS NOT NULL)),
  ADD CONSTRAINT consent_withdrawn_after_grant CHECK ("withdrawnAt" IS NULL OR "withdrawnAt" >= "grantedAt");
CREATE UNIQUE INDEX consent_one_live_grant ON public.consent_records ("userId", purpose) WHERE status = 'GRANTED';

-- Consent records are evidence: the only permitted change is GRANTED -> WITHDRAWN.
CREATE FUNCTION public.consent_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'consent records cannot be deleted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.status <> 'GRANTED' OR NEW.status <> 'WITHDRAWN'
     OR NEW."userId" <> OLD."userId" OR NEW.purpose <> OLD.purpose
     OR NEW."policyVersion" <> OLD."policyVersion" OR NEW."grantedAt" <> OLD."grantedAt"
     OR NEW.locale <> OLD.locale OR NEW."ipHash" IS DISTINCT FROM OLD."ipHash" THEN
    RAISE EXCEPTION 'consent record may only change from GRANTED to WITHDRAWN' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER consent_guard BEFORE UPDATE OR DELETE ON public.consent_records
  FOR EACH ROW EXECUTE FUNCTION public.consent_guard();

-- ───────────────────────── Fayda / OIDC ─────────────────────────

ALTER TABLE public.oidc_auth_requests
  ADD CONSTRAINT oidc_state_hash_hex CHECK ("stateHash" ~ '^[0-9a-f]{64}$'),
  ADD CONSTRAINT oidc_nonce_hash_hex CHECK ("nonceHash" ~ '^[0-9a-f]{64}$'),
  ADD CONSTRAINT oidc_short_lived CHECK ("expiresAt" > "createdAt" AND "expiresAt" <= "createdAt" + interval '15 minutes');

-- An authorization request needs a live, matching Fayda consent from the same user,
-- and sandbox requests are refused on a production database.
CREATE FUNCTION public.oidc_auth_request_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
DECLARE
  c public.consent_records%ROWTYPE;
  s_user uuid;
BEGIN
  IF public.tamagne_is_production() AND NEW.environment <> 'PRODUCTION' THEN
    RAISE EXCEPTION 'sandbox identity requests are not allowed on a production database' USING ERRCODE = 'check_violation';
  END IF;
  SELECT * INTO c FROM public.consent_records WHERE id = NEW."consentRecordId";
  IF c.status IS DISTINCT FROM 'GRANTED' OR c.purpose <> 'FAYDA_IDENTITY_VERIFICATION' OR c."userId" <> NEW."userId" THEN
    RAISE EXCEPTION 'Fayda authorization requires a granted FAYDA_IDENTITY_VERIFICATION consent of the same user' USING ERRCODE = 'check_violation';
  END IF;
  SELECT "userId" INTO s_user FROM public.seller_profiles WHERE id = NEW."sellerProfileId";
  IF s_user IS DISTINCT FROM NEW."userId" THEN
    RAISE EXCEPTION 'seller profile does not belong to the requesting user' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER oidc_auth_request_guard BEFORE INSERT ON public.oidc_auth_requests
  FOR EACH ROW EXECUTE FUNCTION public.oidc_auth_request_guard();

ALTER TABLE public.identity_verifications
  ADD CONSTRAINT iv_subject_hash_hex CHECK ("subjectHash" IS NULL OR "subjectHash" ~ '^[0-9a-f]{64}$'),
  -- VERIFIED only with a signature-verified token and the validated claims recorded.
  ADD CONSTRAINT iv_verified_requires_validation CHECK (
    status <> 'VERIFIED' OR (
      "signatureVerified" AND "subjectHash" IS NOT NULL AND issuer IS NOT NULL
      AND audience IS NOT NULL AND "verifiedAt" IS NOT NULL AND "authRequestId" IS NOT NULL
    )
  ),
  ADD CONSTRAINT iv_failed_has_code CHECK (status <> 'FAILED' OR "failureCode" IS NOT NULL),
  ADD CONSTRAINT iv_revoked_has_time CHECK (status <> 'REVOKED' OR "revokedAt" IS NOT NULL),
  ADD CONSTRAINT iv_expiry_after_verification CHECK ("expiresAt" IS NULL OR "verifiedAt" IS NULL OR "expiresAt" > "verifiedAt");
-- One national identity can back only one seller account at a time (duplicate-seller defence).
CREATE UNIQUE INDEX iv_one_verified_subject ON public.identity_verifications (environment, "subjectHash") WHERE status = 'VERIFIED';
-- A single authorization response can be redeemed once.
CREATE UNIQUE INDEX iv_one_result_per_auth_request ON public.identity_verifications ("authRequestId") WHERE "authRequestId" IS NOT NULL;
CREATE UNIQUE INDEX iv_one_use_per_jti ON public.identity_verifications (issuer, "idTokenJti") WHERE "idTokenJti" IS NOT NULL;

CREATE FUNCTION public.identity_verification_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF public.tamagne_is_production() AND NEW.environment <> 'PRODUCTION' THEN
    RAISE EXCEPTION 'sandbox identity results are not allowed on a production database' USING ERRCODE = 'check_violation';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IN ('REVOKED', 'EXPIRED', 'FAILED') AND NEW.status <> OLD.status THEN
    RAISE EXCEPTION 'identity verification in terminal status % cannot change', OLD.status USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER identity_verification_guard BEFORE INSERT OR UPDATE ON public.identity_verifications
  FOR EACH ROW EXECUTE FUNCTION public.identity_verification_guard();

-- ───────────────────────── reviews ─────────────────────────

ALTER TABLE public.review_records
  ADD CONSTRAINT review_reason_nonblank CHECK (length(btrim(reason)) >= 3),
  ADD CONSTRAINT review_distinct_second_reviewer CHECK ("secondReviewerId" IS NULL OR "secondReviewerId" <> "reviewerId"),
  ADD CONSTRAINT review_high_risk_needs_second CHECK ("riskLevel" <> 'HIGH' OR "requiresSecondReview"),
  ADD CONSTRAINT review_pending_only_when_required CHECK (status <> 'PENDING_SECOND_REVIEW' OR "requiresSecondReview"),
  ADD CONSTRAINT review_final_complete CHECK (
    status <> 'FINAL' OR (
      "finalizedAt" IS NOT NULL AND (
        NOT "requiresSecondReview" OR (
          "secondReviewerId" IS NOT NULL AND "secondDecision" IS NOT NULL
          AND "secondReason" IS NOT NULL AND "secondReviewedAt" IS NOT NULL
        )
      )
    )
  );

-- Reviewers must hold a live staff role, must not review their own seller account,
-- and a FINAL review is immutable.
CREATE FUNCTION public.review_record_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
DECLARE
  owner_id uuid;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'review records cannot be deleted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF OLD.status = 'FINAL' THEN
      RAISE EXCEPTION 'final review records are immutable' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW."reviewerId" <> OLD."reviewerId" OR NEW.decision <> OLD.decision OR NEW.reason <> OLD.reason
       OR NEW."targetType" <> OLD."targetType" OR NEW."targetId" <> OLD."targetId"
       OR NEW."sellerProfileId" <> OLD."sellerProfileId" OR NEW."riskLevel" <> OLD."riskLevel"
       OR NEW."requiresSecondReview" <> OLD."requiresSecondReview" THEN
      RAISE EXCEPTION 'first-review fields are immutable' USING ERRCODE = 'insufficient_privilege';
    END IF;
  END IF;
  SELECT "userId" INTO owner_id FROM public.seller_profiles WHERE id = NEW."sellerProfileId";
  IF NEW."reviewerId" = owner_id OR NEW."secondReviewerId" = owner_id THEN
    RAISE EXCEPTION 'reviewers cannot review their own seller account' USING ERRCODE = 'check_violation';
  END IF;
  IF NOT public.tamagne_has_staff_role(NEW."reviewerId")
     OR (NEW."secondReviewerId" IS NOT NULL AND NOT public.tamagne_has_staff_role(NEW."secondReviewerId")) THEN
    RAISE EXCEPTION 'reviewer does not hold an active staff role' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER review_record_guard BEFORE INSERT OR UPDATE OR DELETE ON public.review_records
  FOR EACH ROW EXECUTE FUNCTION public.review_record_guard();

-- ───────────────────────── badges ─────────────────────────

ALTER TABLE public.seller_badges
  ADD CONSTRAINT badge_method_matches_level CHECK (
    (level = 'PHONE'                  AND method = 'PHONE_OTP'                AND "phoneOtpChallengeId" IS NOT NULL) OR
    (level = 'FAYDA_IDENTITY'         AND method = 'FAYDA_ESIGNET_OIDC'       AND "identityVerificationId" IS NOT NULL) OR
    (level = 'IDENTITY_MANUAL_REVIEW' AND method = 'MANUAL_IDENTITY_REVIEW'   AND "reviewRecordId" IS NOT NULL) OR
    (level = 'BUSINESS_DOCUMENTS'     AND method = 'MANUAL_DOCUMENT_REVIEW'   AND "reviewRecordId" IS NOT NULL) OR
    (level = 'ENHANCED_REVIEW'        AND method = 'ENHANCED_BUSINESS_REVIEW' AND "reviewRecordId" IS NOT NULL)
  ),
  ADD CONSTRAINT badge_active_dated CHECK (
    status NOT IN ('ACTIVE', 'SUSPENDED', 'EXPIRED') OR ("issuedAt" IS NOT NULL AND "expiresAt" IS NOT NULL AND "expiresAt" > "issuedAt")
  ),
  ADD CONSTRAINT badge_revocation_complete CHECK (
    (status = 'REVOKED') = ("revokedAt" IS NOT NULL) AND (status <> 'REVOKED' OR "revocationReason" IS NOT NULL)
  );
CREATE UNIQUE INDEX badge_one_live_per_level ON public.seller_badges ("sellerProfileId", level)
  WHERE status IN ('PENDING', 'ACTIVE', 'SUSPENDED');

-- Issuance guard. A badge becomes ACTIVE only with valid evidence for its level:
--  * PHONE: a consumed OTP challenge.
--  * FAYDA_IDENTITY: a VERIFIED, signature-verified identity result for this seller
--    (and, on production, from the PRODUCTION environment). Nothing else — not a
--    manual review, not a payment — can produce a Fayda badge.
--  * Review-based levels: a FINAL review of the matching target type that approved
--    (both reviewers approved when a second review was required).
-- REVOKED and EXPIRED are terminal; renewal creates a new row.
CREATE FUNCTION public.seller_badge_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
DECLARE
  iv public.identity_verifications%ROWTYPE;
  rr public.review_records%ROWTYPE;
  seller_status text;
  expected_target text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'badges cannot be deleted; revoke instead' USING ERRCODE = 'insufficient_privilege';
  END IF;

  IF TG_OP = 'UPDATE' THEN
    IF OLD.status IN ('REVOKED', 'EXPIRED') THEN
      RAISE EXCEPTION 'badge in terminal status % is immutable', OLD.status USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.level <> OLD.level OR NEW.method <> OLD.method OR NEW."sellerProfileId" <> OLD."sellerProfileId"
       OR NEW."phoneOtpChallengeId" IS DISTINCT FROM OLD."phoneOtpChallengeId"
       OR NEW."identityVerificationId" IS DISTINCT FROM OLD."identityVerificationId"
       OR NEW."reviewRecordId" IS DISTINCT FROM OLD."reviewRecordId" THEN
      RAISE EXCEPTION 'badge level, method and evidence are immutable' USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  IF NEW.status <> 'ACTIVE' OR (TG_OP = 'UPDATE' AND OLD.status = 'ACTIVE') THEN
    RETURN NEW;
  END IF;

  SELECT "accountStatus"::text INTO seller_status FROM public.seller_profiles WHERE id = NEW."sellerProfileId";
  IF seller_status IS DISTINCT FROM 'ACTIVE' THEN
    RAISE EXCEPTION 'badges can only be activated for ACTIVE seller accounts' USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.level = 'PHONE' THEN
    IF NOT EXISTS (SELECT 1 FROM public.phone_otp_challenges o WHERE o.id = NEW."phoneOtpChallengeId" AND o."consumedAt" IS NOT NULL) THEN
      RAISE EXCEPTION 'phone badge requires a consumed OTP challenge' USING ERRCODE = 'check_violation';
    END IF;

  ELSIF NEW.level = 'FAYDA_IDENTITY' THEN
    SELECT * INTO iv FROM public.identity_verifications WHERE id = NEW."identityVerificationId";
    IF iv.id IS NULL OR iv.status <> 'VERIFIED' OR NOT iv."signatureVerified"
       OR iv."sellerProfileId" <> NEW."sellerProfileId" THEN
      RAISE EXCEPTION 'Fayda badge requires a VERIFIED, signature-validated Fayda result for this seller' USING ERRCODE = 'check_violation';
    END IF;
    IF public.tamagne_is_production() AND iv.environment <> 'PRODUCTION' THEN
      RAISE EXCEPTION 'Fayda badge on production requires a PRODUCTION Fayda result' USING ERRCODE = 'check_violation';
    END IF;
    IF iv."expiresAt" IS NOT NULL AND NEW."expiresAt" > iv."expiresAt" THEN
      RAISE EXCEPTION 'Fayda badge cannot outlive its identity verification' USING ERRCODE = 'check_violation';
    END IF;

  ELSE
    expected_target := CASE NEW.level
      WHEN 'IDENTITY_MANUAL_REVIEW' THEN 'IDENTITY_MANUAL'
      WHEN 'BUSINESS_DOCUMENTS' THEN 'BUSINESS'
      WHEN 'ENHANCED_REVIEW' THEN 'ENHANCED_REVIEW'
    END;
    SELECT * INTO rr FROM public.review_records WHERE id = NEW."reviewRecordId";
    IF rr.id IS NULL OR rr.status <> 'FINAL' OR rr.decision <> 'APPROVE'
       OR rr."targetType"::text <> expected_target OR rr."sellerProfileId" <> NEW."sellerProfileId"
       OR (rr."requiresSecondReview" AND rr."secondDecision" IS DISTINCT FROM 'APPROVE') THEN
      RAISE EXCEPTION '% badge requires a final, approving % review for this seller', NEW.level, expected_target USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  RETURN NEW;
END $$;
CREATE TRIGGER seller_badge_guard BEFORE INSERT OR UPDATE OR DELETE ON public.seller_badges
  FOR EACH ROW EXECUTE FUNCTION public.seller_badge_guard();

-- ───────────────────────── append-only history ─────────────────────────

CREATE TRIGGER verification_events_append_only BEFORE UPDATE OR DELETE ON public.verification_events
  FOR EACH ROW EXECUTE FUNCTION public.tamagne_reject_mutation();
CREATE TRIGGER verification_events_no_truncate BEFORE TRUNCATE ON public.verification_events
  FOR EACH STATEMENT EXECUTE FUNCTION public.tamagne_reject_mutation();

-- Audit log: append-only and hash-chained. Each row's hash covers the previous
-- row's hash plus its own content, so edits made by bypassing the trigger
-- (e.g. by a superuser) are detectable with audit_log_verify_chain().
CREATE FUNCTION public.audit_log_row_hash(prev text, r public.audit_logs) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path = '' AS $$
  SELECT encode(sha256(convert_to(concat_ws('|',
    coalesce(prev, 'GENESIS'), r.id::text, r.seq::text,
    to_char(r."occurredAt" AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    r."actorType"::text, coalesce(r."actorUserId"::text, ''), coalesce(r."actorApiClientId"::text, ''),
    r.action, coalesce(r."targetType", ''), coalesce(r."targetId", ''), coalesce(r."requestId", ''),
    coalesce(r."ipHash", ''), r.metadata::text
  ), 'UTF8')), 'hex')
$$;

CREATE FUNCTION public.audit_log_chain() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
DECLARE
  last_hash text;
BEGIN
  -- Serialize writers so the chain is linear.
  PERFORM pg_advisory_xact_lock(hashtext('tamagne.audit_logs'));
  SELECT hash INTO last_hash FROM public.audit_logs ORDER BY seq DESC LIMIT 1;
  NEW."prevHash" := last_hash;
  NEW.hash := public.audit_log_row_hash(last_hash, NEW);
  RETURN NEW;
END $$;
CREATE TRIGGER audit_log_chain BEFORE INSERT ON public.audit_logs
  FOR EACH ROW EXECUTE FUNCTION public.audit_log_chain();
CREATE TRIGGER audit_logs_append_only BEFORE UPDATE OR DELETE ON public.audit_logs
  FOR EACH ROW EXECUTE FUNCTION public.tamagne_reject_mutation();
CREATE TRIGGER audit_logs_no_truncate BEFORE TRUNCATE ON public.audit_logs
  FOR EACH STATEMENT EXECUTE FUNCTION public.tamagne_reject_mutation();

-- Returns the seq of the first row whose chain link or hash is wrong, or NULL if intact.
CREATE FUNCTION public.audit_log_verify_chain() RETURNS bigint
LANGUAGE plpgsql STABLE SET search_path = '' AS $$
DECLARE
  r public.audit_logs;
  prev text := NULL;
BEGIN
  FOR r IN SELECT * FROM public.audit_logs ORDER BY seq LOOP
    IF r."prevHash" IS DISTINCT FROM prev OR r.hash IS DISTINCT FROM public.audit_log_row_hash(prev, r) THEN
      RETURN r.seq;
    END IF;
    prev := r.hash;
  END LOOP;
  RETURN NULL;
END $$;

-- ───────────────────────── QR tokens ─────────────────────────

ALTER TABLE public.qr_tokens
  ADD CONSTRAINT qr_token_format CHECK (token ~ '^[A-Za-z0-9_-]{43}$'),
  ADD CONSTRAINT qr_revocation_consistent CHECK ((status = 'REVOKED') = ("revokedAt" IS NOT NULL));
CREATE UNIQUE INDEX qr_one_active_per_seller ON public.qr_tokens ("sellerProfileId") WHERE status = 'ACTIVE';

CREATE FUNCTION public.qr_token_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'QR tokens cannot be deleted; revoke instead' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.status = 'REVOKED' THEN
    RAISE EXCEPTION 'revoked QR tokens cannot be reactivated or edited' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.token <> OLD.token OR NEW."sellerProfileId" <> OLD."sellerProfileId" THEN
    RAISE EXCEPTION 'QR token value and owner are immutable' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER qr_token_guard BEFORE UPDATE OR DELETE ON public.qr_tokens
  FOR EACH ROW EXECUTE FUNCTION public.qr_token_guard();

-- ───────────────────────── reports & appeals ─────────────────────────

ALTER TABLE public.fraud_reports
  ADD CONSTRAINT fraud_description_min CHECK (length(btrim(description)) >= 20),
  ADD CONSTRAINT fraud_resolution_complete CHECK (
    status NOT IN ('ACTION_TAKEN', 'DISMISSED') OR ("resolvedAt" IS NOT NULL AND "resolutionNote" IS NOT NULL)
  );

ALTER TABLE public.appeals
  ADD CONSTRAINT appeal_reason_min CHECK (length(btrim(reason)) >= 10),
  ADD CONSTRAINT appeal_not_self_decided CHECK ("decidedById" IS NULL OR "decidedById" <> "submittedById"),
  ADD CONSTRAINT appeal_decision_complete CHECK (
    status NOT IN ('UPHELD', 'OVERTURNED') OR ("decidedById" IS NOT NULL AND "decidedAt" IS NOT NULL AND "decisionNote" IS NOT NULL)
  );
CREATE UNIQUE INDEX appeal_one_open_per_subject ON public.appeals ("sellerProfileId", "subjectType", coalesce("subjectId", '00000000-0000-0000-0000-000000000000'::uuid))
  WHERE status IN ('SUBMITTED', 'UNDER_REVIEW');

-- ───────────────────────── billing ─────────────────────────

ALTER TABLE public.plans
  ADD CONSTRAINT plan_code_format CHECK (code ~ '^[a-z0-9_]{2,64}$');

ALTER TABLE public.plan_prices
  ADD CONSTRAINT price_nonneg CHECK (amount >= 0),
  ADD CONSTRAINT price_currency_iso CHECK (currency ~ '^[A-Z]{3}$'),
  ADD CONSTRAINT price_window CHECK ("validTo" IS NULL OR "validTo" > "validFrom");
CREATE UNIQUE INDEX price_one_current ON public.plan_prices ("planId", interval, currency) WHERE "validTo" IS NULL;

ALTER TABLE public.subscriptions
  ADD CONSTRAINT subscription_period CHECK ("currentPeriodEnd" IS NULL OR "currentPeriodEnd" > "currentPeriodStart");

ALTER TABLE public.payments
  ADD CONSTRAINT payment_amount_positive CHECK (amount > 0),
  ADD CONSTRAINT payment_currency_iso CHECK (currency ~ '^[A-Z]{3}$'),
  ADD CONSTRAINT payment_success_dated CHECK (status NOT IN ('SUCCEEDED', 'REFUNDED') OR "paidAt" IS NOT NULL);

-- ───────────────────────── API clients, privacy ─────────────────────────

ALTER TABLE public.api_clients
  ADD CONSTRAINT api_rate_limit_bounds CHECK ("rateLimitPerMinute" BETWEEN 1 AND 100000),
  ADD CONSTRAINT api_key_hash_hex CHECK ("keyHash" ~ '^[0-9a-f]{64}$'),
  ADD CONSTRAINT api_scopes_known CHECK (scopes <@ ARRAY['verify:read', 'sellers:read', 'reports:write']::text[]),
  ADD CONSTRAINT api_revocation_consistent CHECK ((status = 'REVOKED') = ("revokedAt" IS NOT NULL));

ALTER TABLE public.data_subject_requests
  ADD CONSTRAINT dsr_completion_dated CHECK (status NOT IN ('COMPLETED', 'REJECTED') OR ("completedAt" IS NOT NULL AND outcome IS NOT NULL)),
  ADD CONSTRAINT dsr_due_after_creation CHECK ("dueAt" > "createdAt");

-- ───────────────────────── function privileges ─────────────────────────

REVOKE EXECUTE ON FUNCTION public.audit_log_verify_chain() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.tamagne_has_staff_role(uuid) FROM PUBLIC;
