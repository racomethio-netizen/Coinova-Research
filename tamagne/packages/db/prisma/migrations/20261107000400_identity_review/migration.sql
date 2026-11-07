-- CreateEnum
CREATE TYPE "IdentityDocumentType" AS ENUM ('FAYDA_ID_CARD', 'KEBELE_ID', 'PASSPORT', 'DRIVING_LICENSE');

-- CreateEnum
CREATE TYPE "IdentityReviewStatus" AS ENUM ('SUBMITTED', 'APPROVED', 'REJECTED', 'WITHDRAWN');

-- AlterEnum
ALTER TYPE "ConsentPurpose" ADD VALUE 'IDENTITY_DOCUMENT_REVIEW';

-- CreateTable
CREATE TABLE "identity_review_requests" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "sellerProfileId" UUID NOT NULL,
    "uploadedById" UUID NOT NULL,
    "documentType" "IdentityDocumentType" NOT NULL,
    "storageKey" VARCHAR(256) NOT NULL,
    "mimeType" VARCHAR(100) NOT NULL,
    "sizeBytes" INTEGER NOT NULL,
    "sha256" CHAR(64) NOT NULL,
    "scanStatus" "MalwareScanStatus" NOT NULL DEFAULT 'PENDING',
    "scanEngine" VARCHAR(64),
    "scannedAt" TIMESTAMPTZ(6),
    "status" "IdentityReviewStatus" NOT NULL DEFAULT 'SUBMITTED',
    "decisionReason" VARCHAR(2000),
    "submittedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "decidedAt" TIMESTAMPTZ(6),
    "retainUntil" TIMESTAMPTZ(6),
    "purgedAt" TIMESTAMPTZ(6),
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "identity_review_requests_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "identity_review_requests_storageKey_key" ON "identity_review_requests"("storageKey");

-- CreateIndex
CREATE INDEX "identity_review_requests_sellerProfileId_idx" ON "identity_review_requests"("sellerProfileId");

-- CreateIndex
CREATE INDEX "identity_review_requests_status_submittedAt_idx" ON "identity_review_requests"("status", "submittedAt");

-- AddForeignKey
ALTER TABLE "identity_review_requests" ADD CONSTRAINT "identity_review_requests_sellerProfileId_fkey" FOREIGN KEY ("sellerProfileId") REFERENCES "seller_profiles"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "identity_review_requests" ADD CONSTRAINT "identity_review_requests_uploadedById_fkey" FOREIGN KEY ("uploadedById") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE CASCADE;


-- ── Integrity rules for manual identity review ──
ALTER TABLE public.identity_review_requests
  ADD CONSTRAINT irr_size_bounds CHECK ("sizeBytes" > 0 AND "sizeBytes" <= 10485760),
  ADD CONSTRAINT irr_mime_allowlist CHECK ("mimeType" IN ('application/pdf', 'image/jpeg', 'image/png')),
  ADD CONSTRAINT irr_sha256_hex CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  ADD CONSTRAINT irr_approved_requires_clean_scan CHECK (status <> 'APPROVED' OR "scanStatus" = 'CLEAN'),
  ADD CONSTRAINT irr_decision_dated CHECK (status NOT IN ('APPROVED', 'REJECTED') OR "decidedAt" IS NOT NULL),
  ADD CONSTRAINT irr_rejection_explained CHECK (status <> 'REJECTED' OR "decisionReason" IS NOT NULL);

-- One open manual review per seller.
CREATE UNIQUE INDEX irr_one_open_per_seller ON public.identity_review_requests ("sellerProfileId") WHERE status = 'SUBMITTED' AND "purgedAt" IS NULL;

-- Decisions are final; only purge metadata may change afterwards.
CREATE FUNCTION public.identity_review_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'identity review requests cannot be deleted; purge the file instead' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.status IN ('APPROVED', 'REJECTED', 'WITHDRAWN') AND (
       NEW.status <> OLD.status OR NEW."decisionReason" IS DISTINCT FROM OLD."decisionReason"
       OR NEW."decidedAt" IS DISTINCT FROM OLD."decidedAt") THEN
    RAISE EXCEPTION 'identity review decision is final' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW."sellerProfileId" <> OLD."sellerProfileId" OR NEW."storageKey" <> OLD."storageKey" OR NEW.sha256 <> OLD.sha256 THEN
    RAISE EXCEPTION 'identity review file and owner are immutable' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER identity_review_guard BEFORE UPDATE OR DELETE ON public.identity_review_requests
  FOR EACH ROW EXECUTE FUNCTION public.identity_review_guard();
