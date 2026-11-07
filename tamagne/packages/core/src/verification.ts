/**
 * Public verification summary: the ONLY shape the public lookup, QR page and
 * marketplace API may expose. It encodes the platform's business rules:
 *
 *  1. Fayda identity and business verification are separate categories.
 *  2. Phone verification is not identity verification.
 *  3. "Fayda verified" requires an ACTIVE FAYDA_IDENTITY badge issued through
 *     FAYDA_ESIGNET_OIDC, and only from the production environment. A manual
 *     identity review is reported separately and never as Fayda.
 *  4. A suspended or deactivated account shows no current verification.
 *  5. Expiry is evaluated at read time, so a lagging expiry job cannot show a
 *     lapsed badge as current.
 */

export const VERIFICATION_LEVELS = [
  "PHONE",
  "FAYDA_IDENTITY",
  "BUSINESS_DOCUMENTS",
  "ENHANCED_REVIEW",
  "IDENTITY_MANUAL_REVIEW",
] as const;
export type VerificationLevel = (typeof VERIFICATION_LEVELS)[number];

export type BadgeStatus = "PENDING" | "ACTIVE" | "SUSPENDED" | "EXPIRED" | "REVOKED";
export type VerificationMethod =
  | "PHONE_OTP"
  | "FAYDA_ESIGNET_OIDC"
  | "MANUAL_IDENTITY_REVIEW"
  | "MANUAL_DOCUMENT_REVIEW"
  | "ENHANCED_BUSINESS_REVIEW";
export type SellerAccountStatus = "ACTIVE" | "UNDER_REVIEW" | "SUSPENDED" | "DEACTIVATED";
export type IntegrationEnvironment = "SANDBOX" | "PRODUCTION";

/** Expected method per level (mirrors the DB constraint badge_method_matches_level). */
export const METHOD_FOR_LEVEL: Readonly<Record<VerificationLevel, VerificationMethod>> = {
  PHONE: "PHONE_OTP",
  FAYDA_IDENTITY: "FAYDA_ESIGNET_OIDC",
  BUSINESS_DOCUMENTS: "MANUAL_DOCUMENT_REVIEW",
  ENHANCED_REVIEW: "ENHANCED_BUSINESS_REVIEW",
  IDENTITY_MANUAL_REVIEW: "MANUAL_IDENTITY_REVIEW",
};

/** Public ladder number; the interim manual identity review has none. */
export const LEVEL_NUMBER: Readonly<Record<VerificationLevel, number | null>> = {
  PHONE: 1,
  FAYDA_IDENTITY: 2,
  BUSINESS_DOCUMENTS: 3,
  ENHANCED_REVIEW: 4,
  IDENTITY_MANUAL_REVIEW: null,
};

export interface BadgeInput {
  level: VerificationLevel;
  status: BadgeStatus;
  method: VerificationMethod;
  issuedAt: Date | null;
  expiresAt: Date | null;
  /** Environment of the backing identity verification (FAYDA_IDENTITY only). */
  identityEnvironment?: IntegrationEnvironment | null;
}

export type PublicStatus = "VERIFIED" | "NOT_VERIFIED" | "EXPIRED" | "REVOKED" | "SUSPENDED";

export interface PublicCategory {
  status: PublicStatus;
  verifiedAt: string | null;
  expiresAt: string | null;
}

export interface PublicVerificationSummary {
  accountStatus: SellerAccountStatus;
  phone: PublicCategory;
  faydaIdentity: PublicCategory;
  /** Interim manual identity check; never to be labelled as Fayda. */
  identityManualReview: PublicCategory;
  business: PublicCategory;
  enhancedReview: PublicCategory;
}

const NONE: PublicCategory = { status: "NOT_VERIFIED", verifiedAt: null, expiresAt: null };

function categoryFor(
  badges: readonly BadgeInput[],
  level: VerificationLevel,
  now: Date,
  accountActive: boolean,
  acceptBadge: (b: BadgeInput) => boolean,
): PublicCategory {
  const candidates = badges.filter((b) => b.level === level && b.method === METHOD_FOR_LEVEL[level] && acceptBadge(b));
  if (candidates.length === 0) return NONE;

  const current = candidates.find(
    (b) => b.status === "ACTIVE" && b.issuedAt && b.expiresAt && b.issuedAt <= now && b.expiresAt > now,
  );
  if (current) {
    if (!accountActive) return { status: "SUSPENDED", verifiedAt: null, expiresAt: null };
    return { status: "VERIFIED", verifiedAt: current.issuedAt!.toISOString(), expiresAt: current.expiresAt!.toISOString() };
  }
  // No current badge: report the most informative past state, without dates.
  if (candidates.some((b) => b.status === "SUSPENDED")) return { ...NONE, status: "SUSPENDED" };
  if (candidates.some((b) => b.status === "REVOKED")) return { ...NONE, status: "REVOKED" };
  if (candidates.some((b) => b.status === "EXPIRED" || (b.status === "ACTIVE" && b.expiresAt && b.expiresAt <= now))) {
    return { ...NONE, status: "EXPIRED" };
  }
  return NONE;
}

export interface SummaryOptions {
  now?: Date;
  /** True on the production deployment: only PRODUCTION Fayda results count. */
  production: boolean;
}

export function buildPublicVerificationSummary(
  accountStatus: SellerAccountStatus,
  badges: readonly BadgeInput[],
  { now = new Date(), production }: SummaryOptions,
): PublicVerificationSummary {
  const active = accountStatus === "ACTIVE";
  const any = () => true;
  return {
    accountStatus,
    phone: categoryFor(badges, "PHONE", now, active, any),
    faydaIdentity: categoryFor(badges, "FAYDA_IDENTITY", now, active, (b) =>
      production ? b.identityEnvironment === "PRODUCTION" : b.identityEnvironment !== null && b.identityEnvironment !== undefined,
    ),
    identityManualReview: categoryFor(badges, "IDENTITY_MANUAL_REVIEW", now, active, any),
    business: categoryFor(badges, "BUSINESS_DOCUMENTS", now, active, any),
    enhancedReview: categoryFor(badges, "ENHANCED_REVIEW", now, active, any),
  };
}
