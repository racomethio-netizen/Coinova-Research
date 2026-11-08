import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { PrismaClient } from "../src/index.js";
import {
  createAuthRequest,
  createSeller,
  createStaff,
  createUser,
  createVerifiedIdentity,
  fakePhone,
  generateQrToken,
  grantConsent,
  hex64,
  makeClient,
  oneYear,
  rejects,
} from "./helpers.js";

let db: PrismaClient;

beforeAll(() => {
  db = makeClient();
});
afterAll(async () => {
  await db.$disconnect();
});

describe("schema", () => {
  it("has every required model table and both migrations applied", async () => {
    const rows = await db.$queryRaw<{ table_name: string }[]>`
      select table_name from information_schema.tables where table_schema = 'public'`;
    const tables = new Set(rows.map((r) => r.table_name));
    for (const t of [
      "users", "seller_profiles", "businesses", "business_documents", "identity_verifications",
      "verification_events", "consent_records", "seller_badges", "qr_tokens", "fraud_reports",
      "appeals", "review_records", "audit_logs", "subscriptions", "payments", "api_clients", "notifications",
    ]) {
      expect(tables.has(t), t).toBe(true);
    }
    const migrations = await db.$queryRaw<{ n: bigint }[]>`
      select count(*) as n from _prisma_migrations where finished_at is not null and rolled_back_at is null`;
    expect(Number(migrations[0]!.n)).toBeGreaterThanOrEqual(2);
  });

  it("primary keys are UUIDs, not identity numbers", async () => {
    const { seller } = await createSeller(db);
    expect(seller.id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(seller.tamagneId).toMatch(/^TMG-[0-9A-HJKMNP-TV-Z]{10}$/);
  });
});

describe("users", () => {
  it("rejects malformed phone numbers and upper-case emails", async () => {
    await rejects(db.user.create({ data: { phoneE164: "0911223344" } }), "users_phone_e164");
    await rejects(db.user.create({ data: { phoneE164: fakePhone(), email: "Seller@Example.test" } }), "users_email_lower");
  });

  it("rejects duplicate phone numbers", async () => {
    const phone = fakePhone();
    await db.user.create({ data: { phoneE164: phone } });
    await rejects(db.user.create({ data: { phoneE164: phone } }), /Unique constraint|unique/i);
  });

  it("a DELETED user must be scrubbed of contact data and credentials", async () => {
    const u = await createUser(db);
    await rejects(db.user.update({ where: { id: u.id }, data: { status: "DELETED", deletedAt: new Date() } }), "users_deleted_is_scrubbed");
    const done = await db.user.update({
      where: { id: u.id },
      data: { status: "DELETED", deletedAt: new Date(), phoneE164: null, email: null, passwordHash: null },
    });
    expect(done.status).toBe("DELETED");
  });

  it("OTP challenges are short-lived and attempt-bounded", async () => {
    await rejects(
      db.phoneOtpChallenge.create({
        data: { phoneE164: fakePhone(), purpose: "LOGIN", codeHash: hex64(), expiresAt: new Date(Date.now() + 3_600_000) },
      }),
      "otp_short_lived",
    );
    await rejects(
      db.phoneOtpChallenge.create({
        data: { phoneE164: fakePhone(), purpose: "LOGIN", codeHash: hex64(), attempts: 6, expiresAt: new Date(Date.now() + 300_000) },
      }),
      "otp_attempts_bounds",
    );
  });
});

describe("staff roles", () => {
  it("one live grant per role; no self-grant; grants are never deleted or re-activated", async () => {
    const admin = await createStaff(db, "SUPER_ADMIN");
    const u = await createUser(db);
    await rejects(db.staffRoleAssignment.create({ data: { userId: u.id, role: "SUPPORT_OFFICER", grantedById: u.id } }), "role_not_self_granted");
    const g = await db.staffRoleAssignment.create({ data: { userId: u.id, role: "SUPPORT_OFFICER", grantedById: admin.id } });
    await rejects(db.staffRoleAssignment.create({ data: { userId: u.id, role: "SUPPORT_OFFICER", grantedById: admin.id } }), /Unique constraint|unique/i);
    await rejects(db.staffRoleAssignment.delete({ where: { id: g.id } }), "cannot be deleted");
    await db.staffRoleAssignment.update({ where: { id: g.id }, data: { revokedAt: new Date(), revokedById: admin.id } });
    await rejects(db.staffRoleAssignment.update({ where: { id: g.id }, data: { revokedAt: null, revokedById: null } }), "immutable");
  });
});

describe("Fayda identity verification and badge", () => {
  it("a Fayda badge cannot be issued without a validated Fayda result", async () => {
    const { seller } = await createSeller(db);
    // No identity verification at all → constraint requires evidence.
    await rejects(
      db.sellerBadge.create({ data: { sellerProfileId: seller.id, level: "FAYDA_IDENTITY", method: "FAYDA_ESIGNET_OIDC", status: "ACTIVE", ...oneYear() } }),
      // The issuance trigger runs before CHECK constraints; either layer may reject.
      /badge_method_matches_level|requires a VERIFIED/,
    );
    // A manual review method cannot produce a Fayda badge.
    const staff = await createStaff(db);
    const review = await db.reviewRecord.create({
      data: { sellerProfileId: seller.id, targetType: "IDENTITY_MANUAL", targetId: seller.id, reviewerId: staff.id, decision: "APPROVE", reason: "Documents match", finalizedAt: new Date() },
    });
    await rejects(
      db.sellerBadge.create({ data: { sellerProfileId: seller.id, level: "FAYDA_IDENTITY", method: "MANUAL_IDENTITY_REVIEW", reviewRecordId: review.id, status: "ACTIVE", ...oneYear() } }),
      /badge_method_matches_level|requires a VERIFIED/,
    );
  });

  it("an INITIATED or FAILED identity result cannot back an active badge", async () => {
    const { user, seller } = await createSeller(db);
    const req = await createAuthRequest(db, user.id, seller.id);
    const failed = await db.identityVerification.create({
      data: { sellerProfileId: seller.id, authRequestId: req.id, environment: "SANDBOX", status: "FAILED", failureCode: "invalid_signature" },
    });
    await rejects(
      db.sellerBadge.create({ data: { sellerProfileId: seller.id, level: "FAYDA_IDENTITY", method: "FAYDA_ESIGNET_OIDC", identityVerificationId: failed.id, status: "ACTIVE", ...oneYear() } }),
      "requires a VERIFIED, signature-validated Fayda result",
    );
    await rejects(db.identityVerification.update({ where: { id: failed.id }, data: { status: "VERIFIED" } }), /iv_verified_requires_validation|terminal/);
  });

  it("VERIFIED requires a verified signature and validated claims", async () => {
    const { user, seller } = await createSeller(db);
    const req = await createAuthRequest(db, user.id, seller.id);
    await rejects(
      db.identityVerification.create({
        data: {
          sellerProfileId: seller.id, authRequestId: req.id, environment: "SANDBOX", status: "VERIFIED",
          subjectHash: hex64(), issuer: "https://esignet.example.test", audience: "client", verifiedAt: new Date(), signatureVerified: false,
        },
      }),
      "iv_verified_requires_validation",
    );
  });

  it("issues a Fayda badge only from a VERIFIED result of the same seller", async () => {
    const a = await createSeller(db);
    const b = await createSeller(db);
    const iv = await createVerifiedIdentity(db, a.user.id, a.seller.id);
    await rejects(
      db.sellerBadge.create({ data: { sellerProfileId: b.seller.id, level: "FAYDA_IDENTITY", method: "FAYDA_ESIGNET_OIDC", identityVerificationId: iv.id, status: "ACTIVE", ...oneYear() } }),
      "requires a VERIFIED",
    );
    await rejects(
      db.sellerBadge.create({
        data: {
          sellerProfileId: a.seller.id, level: "FAYDA_IDENTITY", method: "FAYDA_ESIGNET_OIDC", identityVerificationId: iv.id, status: "ACTIVE",
          issuedAt: new Date(), expiresAt: new Date(Date.now() + 3 * 365 * 86_400_000),
        },
      }),
      "cannot outlive",
    );
    const badge = await db.sellerBadge.create({
      data: { sellerProfileId: a.seller.id, level: "FAYDA_IDENTITY", method: "FAYDA_ESIGNET_OIDC", identityVerificationId: iv.id, status: "ACTIVE", ...oneYear() },
    });
    expect(badge.status).toBe("ACTIVE");
  });

  it("one national identity cannot verify two seller accounts (duplicate seller)", async () => {
    const subjectHash = hex64();
    const a = await createSeller(db);
    const b = await createSeller(db);
    await createVerifiedIdentity(db, a.user.id, a.seller.id, { subjectHash });
    await rejects(createVerifiedIdentity(db, b.user.id, b.seller.id, { subjectHash }), /Unique constraint|unique/i);
  });

  it("an authorization response can be redeemed only once", async () => {
    const { user, seller } = await createSeller(db);
    const iv = await createVerifiedIdentity(db, user.id, seller.id);
    await rejects(
      db.identityVerification.create({ data: { sellerProfileId: seller.id, authRequestId: iv.authRequestId, environment: "SANDBOX", status: "INITIATED" } }),
      /Unique constraint|unique/i,
    );
  });

  it("on a production database, sandbox requests and results are refused", async () => {
    const { user, seller } = await createSeller(db);
    const consent = await grantConsent(db, user.id);
    await rejects(
      db.$transaction(async (tx) => {
        await tx.$executeRaw`select set_config('tamagne.environment', 'production', true)`;
        await tx.oidcAuthRequest.create({
          data: {
            userId: user.id, sellerProfileId: seller.id, environment: "SANDBOX", stateHash: hex64(), nonceHash: hex64(),
            codeVerifierEnc: Buffer.from("x"), redirectUri: "https://tamagne.example/cb", consentRecordId: consent.id,
            expiresAt: new Date(Date.now() + 300_000),
          },
        });
      }),
      "not allowed on a production database",
    );
    // A sandbox-verified identity recorded earlier cannot back a badge on production.
    const iv = await createVerifiedIdentity(db, user.id, seller.id, { environment: "SANDBOX" });
    await rejects(
      db.$transaction(async (tx) => {
        await tx.$executeRaw`select set_config('tamagne.environment', 'production', true)`;
        await tx.sellerBadge.create({
          data: { sellerProfileId: seller.id, level: "FAYDA_IDENTITY", method: "FAYDA_ESIGNET_OIDC", identityVerificationId: iv.id, status: "ACTIVE", ...oneYear() },
        });
      }),
      /not allowed on a production database|PRODUCTION Fayda result/,
    );
  });

  it("OIDC requests need a granted Fayda consent of the same user", async () => {
    const { user, seller } = await createSeller(db);
    const other = await createUser(db);
    const otherConsent = await grantConsent(db, other.id);
    const base = {
      userId: user.id, sellerProfileId: seller.id, environment: "SANDBOX" as const, codeVerifierEnc: Buffer.from("x"),
      redirectUri: "http://localhost/cb", expiresAt: new Date(Date.now() + 300_000),
    };
    await rejects(db.oidcAuthRequest.create({ data: { ...base, stateHash: hex64(), nonceHash: hex64(), consentRecordId: otherConsent.id } }), "consent");
    const own = await grantConsent(db, user.id);
    await db.consentRecord.update({ where: { id: own.id }, data: { status: "WITHDRAWN", withdrawnAt: new Date() } });
    await rejects(db.oidcAuthRequest.create({ data: { ...base, stateHash: hex64(), nonceHash: hex64(), consentRecordId: own.id } }), "consent");
    await rejects(
      db.oidcAuthRequest.create({
        data: { ...base, stateHash: hex64(), nonceHash: hex64(), consentRecordId: (await grantConsent(db, user.id)).id, expiresAt: new Date(Date.now() + 3_600_000) },
      }),
      "oidc_short_lived",
    );
  });
});

describe("consent records", () => {
  it("only GRANTED → WITHDRAWN is allowed; records are never deleted", async () => {
    const u = await createUser(db);
    const c = await grantConsent(db, u.id, "PUBLIC_PROFILE_DISPLAY");
    await rejects(grantConsent(db, u.id, "PUBLIC_PROFILE_DISPLAY"), /Unique constraint|unique/i);
    await rejects(db.consentRecord.update({ where: { id: c.id }, data: { policyVersion: "tampered" } }), "GRANTED to WITHDRAWN");
    await rejects(db.consentRecord.update({ where: { id: c.id }, data: { status: "WITHDRAWN" } }), "consent_withdrawal_consistent");
    await db.consentRecord.update({ where: { id: c.id }, data: { status: "WITHDRAWN", withdrawnAt: new Date(), withdrawalReason: "test" } });
    await rejects(db.consentRecord.update({ where: { id: c.id }, data: { status: "GRANTED", withdrawnAt: null } }), "GRANTED to WITHDRAWN");
    await rejects(db.consentRecord.delete({ where: { id: c.id } }), "cannot be deleted");
    // Re-granting creates a new record with its own timestamp and policy version.
    const again = await grantConsent(db, u.id, "PUBLIC_PROFILE_DISPLAY");
    expect(again.id).not.toBe(c.id);
  });
});

describe("reviews and review-based badges", () => {
  it("reviewers need an active staff role and cannot review themselves", async () => {
    const { user, seller } = await createSeller(db);
    const plain = await createUser(db);
    const review = { sellerProfileId: seller.id, targetType: "BUSINESS" as const, targetId: seller.id, decision: "APPROVE" as const, reason: "ok fine", finalizedAt: new Date() };
    await rejects(db.reviewRecord.create({ data: { ...review, reviewerId: plain.id } }), "active staff role");
    await db.staffRoleAssignment.create({ data: { userId: user.id, role: "VERIFICATION_OFFICER" } });
    await rejects(db.reviewRecord.create({ data: { ...review, reviewerId: user.id } }), "own seller account");
  });

  it("high-risk decisions need a different second reviewer before they are final", async () => {
    const { seller } = await createSeller(db);
    const r1 = await createStaff(db);
    const r2 = await createStaff(db);
    const base = { sellerProfileId: seller.id, targetType: "ACCOUNT_STATUS" as const, targetId: seller.id, reviewerId: r1.id, decision: "REACTIVATE" as const, reason: "override" };
    await rejects(db.reviewRecord.create({ data: { ...base, riskLevel: "HIGH", finalizedAt: new Date() } }), "review_high_risk_needs_second");
    await rejects(db.reviewRecord.create({ data: { ...base, riskLevel: "HIGH", requiresSecondReview: true, finalizedAt: new Date() } }), "review_final_complete");
    const pending = await db.reviewRecord.create({ data: { ...base, riskLevel: "HIGH", requiresSecondReview: true, status: "PENDING_SECOND_REVIEW" } });
    await rejects(
      db.reviewRecord.update({ where: { id: pending.id }, data: { secondReviewerId: r1.id, secondDecision: "REACTIVATE", secondReason: "agree", secondReviewedAt: new Date(), status: "FINAL", finalizedAt: new Date() } }),
      "review_distinct_second_reviewer",
    );
    await rejects(db.reviewRecord.update({ where: { id: pending.id }, data: { decision: "REJECT" } }), "immutable");
    const final = await db.reviewRecord.update({
      where: { id: pending.id },
      data: { secondReviewerId: r2.id, secondDecision: "REACTIVATE", secondReason: "agree", secondReviewedAt: new Date(), status: "FINAL", finalizedAt: new Date() },
    });
    expect(final.status).toBe("FINAL");
    await rejects(db.reviewRecord.update({ where: { id: final.id }, data: { secondReason: "changed" } }), "immutable");
  });

  it("a business badge requires a final approving BUSINESS review of the same seller", async () => {
    const { seller } = await createSeller(db);
    const staff = await createStaff(db);
    const reject = await db.reviewRecord.create({
      data: { sellerProfileId: seller.id, targetType: "BUSINESS", targetId: seller.id, reviewerId: staff.id, decision: "REJECT", reason: "license expired", finalizedAt: new Date() },
    });
    const badge = { sellerProfileId: seller.id, level: "BUSINESS_DOCUMENTS" as const, method: "MANUAL_DOCUMENT_REVIEW" as const, status: "ACTIVE" as const, ...oneYear() };
    await rejects(db.sellerBadge.create({ data: { ...badge, reviewRecordId: reject.id } }), "final, approving BUSINESS review");
    const wrongTarget = await db.reviewRecord.create({
      data: { sellerProfileId: seller.id, targetType: "FRAUD_REPORT", targetId: seller.id, reviewerId: staff.id, decision: "APPROVE", reason: "n/a test", finalizedAt: new Date() },
    });
    await rejects(db.sellerBadge.create({ data: { ...badge, reviewRecordId: wrongTarget.id } }), "BUSINESS review");
    const approve = await db.reviewRecord.create({
      data: { sellerProfileId: seller.id, targetType: "BUSINESS", targetId: seller.id, reviewerId: staff.id, decision: "APPROVE", reason: "documents valid", finalizedAt: new Date() },
    });
    const ok = await db.sellerBadge.create({ data: { ...badge, reviewRecordId: approve.id } });
    expect(ok.status).toBe("ACTIVE");
  });
});

describe("badge lifecycle", () => {
  async function phoneBadge(sellerProfileId: string, phone: string) {
    const otp = await db.phoneOtpChallenge.create({
      data: { phoneE164: phone, purpose: "REGISTRATION", codeHash: hex64(), expiresAt: new Date(Date.now() + 300_000), consumedAt: new Date() },
    });
    return db.sellerBadge.create({ data: { sellerProfileId, level: "PHONE", method: "PHONE_OTP", phoneOtpChallengeId: otp.id, status: "ACTIVE", ...oneYear() } });
  }

  it("phone badge requires a consumed OTP", async () => {
    const { user, seller } = await createSeller(db);
    const otp = await db.phoneOtpChallenge.create({
      data: { phoneE164: user.phoneE164!, purpose: "REGISTRATION", codeHash: hex64(), expiresAt: new Date(Date.now() + 300_000) },
    });
    await rejects(
      db.sellerBadge.create({ data: { sellerProfileId: seller.id, level: "PHONE", method: "PHONE_OTP", phoneOtpChallengeId: otp.id, status: "ACTIVE", ...oneYear() } }),
      "consumed OTP",
    );
  });

  it("only one live badge per level; revocation needs a reason and is terminal; no deletes", async () => {
    const { user, seller } = await createSeller(db);
    const b = await phoneBadge(seller.id, user.phoneE164!);
    await rejects(phoneBadge(seller.id, user.phoneE164!), /Unique constraint|unique/i);
    await rejects(db.sellerBadge.update({ where: { id: b.id }, data: { status: "REVOKED", revokedAt: new Date() } }), "badge_revocation_complete");
    await db.sellerBadge.update({ where: { id: b.id }, data: { status: "REVOKED", revokedAt: new Date(), revocationReason: "phone number changed" } });
    await rejects(db.sellerBadge.update({ where: { id: b.id }, data: { status: "ACTIVE", revokedAt: null, revocationReason: null } }), "terminal");
    await rejects(db.sellerBadge.delete({ where: { id: b.id } }), "cannot be deleted");
    // Renewal is a new row.
    expect((await phoneBadge(seller.id, user.phoneE164!)).status).toBe("ACTIVE");
  });

  it("badges cannot be activated for a seller under review or suspended", async () => {
    const { user, seller } = await createSeller(db, { accountStatus: "UNDER_REVIEW" });
    await rejects(phoneBadge(seller.id, user.phoneE164!), "ACTIVE seller accounts");
    await rejects(
      db.sellerProfile.update({ where: { id: seller.id }, data: { accountStatus: "SUSPENDED" } }),
      "seller_suspension_explained",
    );
  });
});

describe("QR tokens", () => {
  it("one active token per seller; revocation is permanent; tokens are strict 256-bit base64url", async () => {
    const { seller } = await createSeller(db);
    await rejects(db.qrToken.create({ data: { sellerProfileId: seller.id, token: "guessable-123" } }), "qr_token_format");
    const t1 = await db.qrToken.create({ data: { sellerProfileId: seller.id, token: generateQrToken() } });
    await rejects(db.qrToken.create({ data: { sellerProfileId: seller.id, token: generateQrToken() } }), /Unique constraint|unique/i);
    await db.qrToken.update({ where: { id: t1.id }, data: { status: "REVOKED", revokedAt: new Date(), revokedReason: "rotated" } });
    await rejects(db.qrToken.update({ where: { id: t1.id }, data: { status: "ACTIVE", revokedAt: null } }), "cannot be reactivated");
    await rejects(db.qrToken.delete({ where: { id: t1.id } }), "cannot be deleted");
    const t2 = await db.qrToken.create({ data: { sellerProfileId: seller.id, token: generateQrToken() } });
    expect(t2.status).toBe("ACTIVE");
  });
});

describe("business documents", () => {
  it("only allow-listed, bounded files; approval only after a clean malware scan", async () => {
    const { user, seller } = await createSeller(db);
    const business = await db.business.create({ data: { sellerProfileId: seller.id, legalName: "Fixture Trading PLC", legalForm: "PRIVATE_LIMITED_COMPANY" } });
    const doc = { businessId: business.id, uploadedById: user.id, type: "BUSINESS_LICENSE" as const, sizeBytes: 1024, sha256: hex64() };
    await rejects(db.businessDocument.create({ data: { ...doc, storageKey: `t/${hex64()}`, mimeType: "application/x-msdownload" } }), "doc_mime_allowlist");
    await rejects(db.businessDocument.create({ data: { ...doc, storageKey: `t/${hex64()}`, mimeType: "application/pdf", sizeBytes: 20_000_000 } }), "doc_size_bounds");
    const d = await db.businessDocument.create({ data: { ...doc, storageKey: `t/${hex64()}`, mimeType: "application/pdf" } });
    await rejects(db.businessDocument.update({ where: { id: d.id }, data: { reviewStatus: "APPROVED" } }), "doc_approved_requires_clean_scan");
    await rejects(
      db.businessDocument.update({ where: { id: d.id }, data: { scanStatus: "INFECTED", scannedAt: new Date(), reviewStatus: "APPROVED" } }),
      "doc_approved_requires_clean_scan",
    );
    await rejects(db.businessDocument.update({ where: { id: d.id }, data: { reviewStatus: "REJECTED" } }), "doc_rejection_explained");
    const ok = await db.businessDocument.update({ where: { id: d.id }, data: { scanStatus: "CLEAN", scannedAt: new Date(), reviewStatus: "APPROVED" } });
    expect(ok.reviewStatus).toBe("APPROVED");
  });

  it("a TIN can back only one verified business", async () => {
    const tin = String(Math.floor(1e9 + Math.random() * 8e9));
    const a = await createSeller(db);
    const b = await createSeller(db);
    await db.business.create({
      data: { sellerProfileId: a.seller.id, legalName: "Fixture A", legalForm: "SOLE_PROPRIETORSHIP", tinNumber: tin, status: "VERIFIED", submittedAt: new Date(), decidedAt: new Date() },
    });
    await db.business.create({ data: { sellerProfileId: b.seller.id, legalName: "Fixture B", legalForm: "SOLE_PROPRIETORSHIP", tinNumber: tin, status: "SUBMITTED", submittedAt: new Date() } });
    const c = await createSeller(db);
    await rejects(
      db.business.create({
        data: { sellerProfileId: c.seller.id, legalName: "Fixture C", legalForm: "SOLE_PROPRIETORSHIP", tinNumber: tin, status: "VERIFIED", submittedAt: new Date(), decidedAt: new Date() },
      }),
      /business_verified_tin|Unique constraint/i,
    );
    // One business per seller (Phase 4 rule).
    await rejects(db.business.create({ data: { sellerProfileId: b.seller.id, legalName: "Fixture B2", legalForm: "OTHER" } }), /Unique constraint/i);
  });
});

describe("history tables", () => {
  it("verification events are append-only", async () => {
    const { seller } = await createSeller(db);
    const e = await db.verificationEvent.create({ data: { sellerProfileId: seller.id, level: "PHONE", type: "REQUESTED", actorType: "SYSTEM" } });
    await rejects(db.verificationEvent.update({ where: { id: e.id }, data: { reason: "edited" } }), "append-only");
    await rejects(db.verificationEvent.delete({ where: { id: e.id } }), "append-only");
    await rejects(db.$executeRawUnsafe("truncate verification_events"), "append-only");
  });

  it("audit log is append-only and hash-chained; tampering is detected", async () => {
    const actor = await createUser(db);
    const a = await db.auditLog.create({ data: { actorType: "USER", actorUserId: actor.id, action: "test.first", metadata: { n: 1 } } });
    const b = await db.auditLog.create({ data: { actorType: "SYSTEM", action: "test.second" } });
    expect(a.hash).toMatch(/^[0-9a-f]{64}$/);
    expect(b.prevHash).toBe(a.hash);
    await rejects(db.auditLog.update({ where: { id: a.id }, data: { action: "test.edited" } }), "append-only");
    await rejects(db.auditLog.delete({ where: { id: a.id } }), "append-only");

    const intact = await db.$queryRaw<{ broken: bigint | null }[]>`select public.audit_log_verify_chain() as broken`;
    expect(intact[0]!.broken).toBeNull();

    // Simulate a privileged attacker who bypasses the trigger; the chain exposes it.
    await db.$transaction(async (tx) => {
      await tx.$executeRawUnsafe("alter table audit_logs disable trigger audit_logs_append_only");
      await tx.$executeRaw`update audit_logs set action = 'test.tampered' where id = ${a.id}::uuid`;
      const broken = await tx.$queryRaw<{ broken: bigint | null }[]>`select public.audit_log_verify_chain() as broken`;
      expect(broken[0]!.broken).toBe(a.seq);
      throw new Error("rollback");
    }).catch((e: unknown) => {
      if (!(e instanceof Error) || e.message !== "rollback") throw e;
    });
    const after = await db.$queryRaw<{ broken: bigint | null }[]>`select public.audit_log_verify_chain() as broken`;
    expect(after[0]!.broken).toBeNull();
  });
});

describe("billing", () => {
  it("prices are non-negative, ISO-coded and one current price per plan/interval/currency", async () => {
    const plan = await db.plan.create({ data: { code: `test_${hex64().slice(0, 12)}`, kind: "PREMIUM_SELLER", nameEn: "Test Plan", nameAm: "የሙከራ እቅድ" } });
    await rejects(db.planPrice.create({ data: { planId: plan.id, amount: "-1", interval: "MONTH" } }), "price_nonneg");
    await rejects(db.planPrice.create({ data: { planId: plan.id, amount: "10", currency: "etb", interval: "MONTH" } }), "price_currency_iso");
    const p = await db.planPrice.create({ data: { planId: plan.id, amount: "199.00", interval: "MONTH" } });
    expect(p.amount.toString()).toBe("199");
    await rejects(db.planPrice.create({ data: { planId: plan.id, amount: "249.00", interval: "MONTH" } }), /Unique constraint|unique/i);
  });

  it("payments have no path to verification tables", async () => {
    const fks = await db.$queryRaw<{ ref: string }[]>`
      select confrelid::regclass::text as ref from pg_constraint
      where conrelid = 'public.payments'::regclass and contype = 'f'`;
    expect(fks.map((f) => f.ref).sort()).toEqual(["subscriptions", "users"]);
  });
});

describe("appeals and fraud reports", () => {
  it("appeals cannot be decided by their submitter and need a decision note", async () => {
    const { user, seller } = await createSeller(db);
    const staff = await createStaff(db);
    const ap = await db.appeal.create({ data: { sellerProfileId: seller.id, submittedById: user.id, subjectType: "ACCOUNT_SUSPENSION", reason: "I was suspended in error" } });
    await rejects(
      db.appeal.create({ data: { sellerProfileId: seller.id, submittedById: user.id, subjectType: "ACCOUNT_SUSPENSION", reason: "Second open appeal" } }),
      /Unique constraint|unique/i,
    );
    await rejects(db.appeal.update({ where: { id: ap.id }, data: { status: "OVERTURNED", decidedById: user.id, decidedAt: new Date(), decisionNote: "self" } }), "appeal_not_self_decided");
    await rejects(db.appeal.update({ where: { id: ap.id }, data: { status: "OVERTURNED", decidedById: staff.id, decidedAt: new Date() } }), "appeal_decision_complete");
    const done = await db.appeal.update({ where: { id: ap.id }, data: { status: "OVERTURNED", decidedById: staff.id, decidedAt: new Date(), decisionNote: "Evidence reviewed" } });
    expect(done.status).toBe("OVERTURNED");
  });

  it("fraud reports need a meaningful description and a resolution note when closed", async () => {
    const { seller } = await createSeller(db);
    await rejects(db.fraudReport.create({ data: { sellerProfileId: seller.id, category: "NON_DELIVERY", description: "bad" } }), "fraud_description_min");
    const r = await db.fraudReport.create({ data: { sellerProfileId: seller.id, category: "NON_DELIVERY", description: "Paid via transfer, item never delivered (fixture)." } });
    await rejects(db.fraudReport.update({ where: { id: r.id }, data: { status: "DISMISSED" } }), "fraud_resolution_complete");
  });
});

describe("audit chain under concurrency", () => {
  it("stays intact when a transaction audits twice while another audits in between", async () => {
    const actor = await createUser(db);
    for (let round = 0; round < 3; round++) {
      const slow = db.$transaction(async (tx) => {
        await tx.auditLog.create({ data: { actorType: "USER", actorUserId: actor.id, action: "test.slow.first" } });
        await tx.$executeRaw`select pg_sleep(0.15)`; // holds the chain lock
        await tx.auditLog.create({ data: { actorType: "USER", actorUserId: actor.id, action: "test.slow.second" } });
      });
      await new Promise((r) => setTimeout(r, 50));
      // Takes its sequence number while the slow transaction still holds the lock.
      const fast = db.auditLog.create({ data: { actorType: "SYSTEM", action: "test.fast" } });
      await Promise.all([slow, fast]);
    }
    const res = await db.$queryRaw<{ broken: bigint | null }[]>`select public.audit_log_verify_chain() as broken`;
    expect(res[0]!.broken).toBeNull();
  });
});
