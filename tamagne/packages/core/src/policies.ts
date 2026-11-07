import type { VerificationLevel } from "./verification.js";

/**
 * Current versions of user-facing policies. Bump when the text changes; consent is
 * recorded against the version shown. Texts live in docs/legal (Phase 7) and
 * require legal review before launch.
 */
export const POLICY_VERSIONS = {
  TERMS_OF_SERVICE: "2026-11-draft",
  PRIVACY_POLICY: "2026-11-draft",
  FAYDA_IDENTITY_VERIFICATION: "2026-11-draft",
  BUSINESS_DOCUMENT_PROCESSING: "2026-11-draft",
  PUBLIC_PROFILE_DISPLAY: "2026-11-draft",
  MARKETING_COMMUNICATIONS: "2026-11-draft",
} as const;

export type ConsentPurpose = keyof typeof POLICY_VERSIONS;

/** Purposes a user may withdraw through the consent API (the rest require closing the account). */
export const WITHDRAWABLE_PURPOSES: readonly ConsentPurpose[] = [
  "PUBLIC_PROFILE_DISPLAY",
  "MARKETING_COMMUNICATIONS",
  "FAYDA_IDENTITY_VERIFICATION",
  "BUSINESS_DOCUMENT_PROCESSING",
];

/** How long each verification stays valid before it must be renewed. */
export const BADGE_VALIDITY_DAYS: Readonly<Record<VerificationLevel, number>> = {
  PHONE: 365,
  FAYDA_IDENTITY: 365,
  IDENTITY_MANUAL_REVIEW: 180,
  BUSINESS_DOCUMENTS: 365,
  ENHANCED_REVIEW: 365,
};

export function badgeExpiry(level: VerificationLevel, issuedAt: Date): Date {
  return new Date(issuedAt.getTime() + BADGE_VALIDITY_DAYS[level] * 86_400_000);
}
