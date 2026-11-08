-- AlterTable
ALTER TABLE "phone_otp_challenges" ALTER COLUMN "phoneE164" DROP NOT NULL;

-- CreateIndex
CREATE INDEX "data_subject_requests_userId_idx" ON "data_subject_requests"("userId");


-- Phase 7: privacy workflows.
-- OTP challenges that evidence a phone badge are kept after account deletion, but their
-- phone number is scrubbed (NULL passes the existing E.164 CHECK).

-- At most one open deletion request per user.
CREATE UNIQUE INDEX dsr_one_open_deletion ON public.data_subject_requests ("userId")
  WHERE type = 'DELETION' AND status IN ('RECEIVED', 'IN_PROGRESS');
