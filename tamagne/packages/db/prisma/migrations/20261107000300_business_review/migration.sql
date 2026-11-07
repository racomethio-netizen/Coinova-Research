-- Phase 4: business document review workflow.

ALTER TABLE public.businesses
  ADD COLUMN "submittedAt" TIMESTAMPTZ(6),
  ADD COLUMN "decidedAt" TIMESTAMPTZ(6),
  ADD COLUMN "decisionReason" VARCHAR(2000);

ALTER TABLE public.businesses
  ADD CONSTRAINT business_submitted_dated CHECK (status NOT IN ('SUBMITTED', 'VERIFIED', 'REJECTED') OR "submittedAt" IS NOT NULL),
  ADD CONSTRAINT business_decision_dated CHECK (status NOT IN ('VERIFIED', 'REJECTED') OR "decidedAt" IS NOT NULL),
  ADD CONSTRAINT business_rejection_explained CHECK (status <> 'REJECTED' OR "decisionReason" IS NOT NULL),
  ADD CONSTRAINT business_registration_format CHECK ("registrationNumber" IS NULL OR "registrationNumber" ~ '^[A-Za-z0-9/\-]{3,64}$');

-- Phase 4 supports one business per seller profile (multi-business is a later decision).
DROP INDEX public."businesses_sellerProfileId_idx";
CREATE UNIQUE INDEX "businesses_sellerProfileId_key" ON public.businesses ("sellerProfileId");

-- A purged document must have left object storage before review data can reference it.
ALTER TABLE public.business_documents
  ADD CONSTRAINT doc_purged_not_approved CHECK ("purgedAt" IS NULL OR "reviewStatus" <> 'APPROVED' OR "retainUntil" IS NOT NULL);

-- Review queue: oldest submissions first.
CREATE INDEX business_review_queue ON public.businesses ("submittedAt") WHERE status = 'SUBMITTED';
