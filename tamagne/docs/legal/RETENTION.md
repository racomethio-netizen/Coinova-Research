# Retention schedule

_Proposed periods, pending legal review. Implemented in code: `apps/api/src/privacy/privacy.service.ts`
(`RETENTION`, `runRetentionJob`, `executeDeletion`), `review.service.ts`, `identity-review.service.ts`._

Run daily: `node apps/api/dist/jobs/run-job.js retention` and `… run-job.js deletions`.

| Data | Kept for | Then | Why |
|------|----------|------|-----|
| Sign-in (OTP) challenges | 30 days | deleted, **unless** it evidences a phone badge (kept; phone number scrubbed when the account is deleted) | abuse investigation; badge evidence |
| Sessions | until 90 days after expiry or sign-out | deleted | security investigation |
| Fayda authorization requests | 30 days | deleted, **unless** they back a verified result | audit of the verification |
| Fayda result (subject hash, name) | while the account exists | name and hash erased on account deletion; result marked revoked | duplicate-identity prevention |
| Manual identity document images | 30 days after approval; 90 days after rejection | file deleted | minimise sensitive data; appeal window |
| Business documents | 1 year after the badge expires; 90 days after rejection | file deleted | re-verification, disputes; appeal window |
| Business details | while the account exists | scrubbed on deletion | verification |
| In-app notifications | 1 year | deleted | — |
| Fraud reports about a seller | [5 years — confirm] | **kept** (no deletion on the seller's request) | fraud prevention, legal claims |
| Reporter contact details | 1 year after the report is resolved | erased | follow-up only |
| Appeals | while the account exists | text replaced on deletion | due process |
| Consent records | [duration of relationship + limitation period — confirm] | kept, marked withdrawn | proof of consent |
| Audit log | [5 years — confirm] | append-only; contains IDs, never contact data | accountability, security |
| Payments | [as required by Ethiopian tax law — confirm] | kept without contact data | legal obligation |

**Account deletion** (`/[lang]/dashboard/privacy` → "Delete my account"): the account is locked,
signed out everywhere, hidden from buyers and its QR code revoked immediately. After the grace
period (`DELETION_GRACE_DAYS`, default 14) the deletion job erases phone, email, names, business
identifiers, document and ID files, notifications and identity data, revokes all badges, and keeps
only the records above without contact details. Support can cancel during the grace period after
confirming the person's identity.
