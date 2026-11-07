-- Phase 2: authentication constraints.

-- At most one live (not disabled) MFA factor per user.
CREATE UNIQUE INDEX mfa_one_live_factor ON public.mfa_factors ("userId") WHERE "disabledAt" IS NULL;

-- A session's MFA timestamp cannot precede the session.
ALTER TABLE public.sessions
  ADD CONSTRAINT sessions_mfa_after_creation CHECK ("mfaVerifiedAt" IS NULL OR "mfaVerifiedAt" >= "createdAt");

-- Lookup of the most recent consumed challenge for a phone (Level 1 badge evidence).
CREATE INDEX otp_consumed_by_phone ON public.phone_otp_challenges ("phoneE164", "consumedAt" DESC) WHERE "consumedAt" IS NOT NULL;
