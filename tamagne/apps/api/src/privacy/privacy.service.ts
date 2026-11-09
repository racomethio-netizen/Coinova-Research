import { HttpStatus, Inject, Injectable, Logger } from "@nestjs/common";
import { ApiError } from "../common/http.js";
import { APP_CONFIG } from "../config/config.module.js";
import type { AppConfig } from "../config/env.js";
import { AuditService } from "../audit/audit.service.js";
import type { Viewer } from "../auth/session.service.js";
import { PrismaService } from "../database/prisma.service.js";
import { CryptoService } from "../security/crypto.service.js";
import { OBJECT_STORE, type EncryptedObjectStore } from "../storage/storage.js";

interface Meta {
  requestId: string;
  ipHash?: string;
}

const DAY = 86_400_000;

/** Proposed retention periods (docs/legal/RETENTION.md). Legal review pending. */
export const RETENTION = {
  otpChallengeDays: 30,
  sessionDaysAfterEnd: 90,
  oidcRequestDays: 30,
  notificationDays: 365,
  reporterContactDaysAfterResolution: 365,
};

/**
 * Data-subject rights under Proclamation No. 1321/2024: access (self-service export),
 * correction (handled by staff), deletion (grace period, then scrubbing and file purge).
 * Records the law or fraud prevention requires us to keep (consent evidence, audit log,
 * reports about a seller, payments) are retained without contact data.
 */
@Injectable()
export class PrivacyService {
  private readonly logger = new Logger("privacy");
  constructor(
    @Inject(PrismaService) private readonly prisma: PrismaService,
    @Inject(AuditService) private readonly audit: AuditService,
    @Inject(CryptoService) private readonly crypto: CryptoService,
    @Inject(OBJECT_STORE) private readonly store: EncryptedObjectStore,
    @Inject(APP_CONFIG) private readonly config: AppConfig,
  ) {}

  // ───────────── Access ─────────────

  /** Everything we hold about the signed-in user, in a portable form. */
  async exportData(viewer: Viewer, meta: Meta) {
    const db = this.prisma.client;
    const user = await db.user.findUniqueOrThrow({ where: { id: viewer.userId } });
    const seller = await db.sellerProfile.findUnique({ where: { userId: user.id } });
    const sid = seller?.id ?? "00000000-0000-0000-0000-000000000000";
    const [consents, badges, business, idVerifications, idReviews, appeals, reportsFiled, notifications, sessions, requests, qr] = await Promise.all([
      db.consentRecord.findMany({ where: { userId: user.id }, orderBy: { grantedAt: "asc" } }),
      db.sellerBadge.findMany({ where: { sellerProfileId: sid }, orderBy: { createdAt: "asc" } }),
      db.business.findUnique({ where: { sellerProfileId: sid }, include: { documents: true } }),
      db.identityVerification.findMany({ where: { sellerProfileId: sid }, orderBy: { createdAt: "asc" } }),
      db.identityReviewRequest.findMany({ where: { sellerProfileId: sid }, orderBy: { createdAt: "asc" } }),
      db.appeal.findMany({ where: { submittedById: user.id }, orderBy: { createdAt: "asc" } }),
      db.fraudReport.findMany({ where: { reporterUserId: user.id }, orderBy: { createdAt: "asc" } }),
      db.notification.findMany({ where: { userId: user.id }, orderBy: { createdAt: "asc" } }),
      db.session.findMany({ where: { userId: user.id }, orderBy: { createdAt: "asc" } }),
      db.dataSubjectRequest.findMany({ where: { userId: user.id }, orderBy: { createdAt: "asc" } }),
      db.qrToken.findMany({ where: { sellerProfileId: sid }, orderBy: { createdAt: "asc" } }),
    ]);
    const iso = (d: Date | null | undefined) => d?.toISOString() ?? null;
    const data = {
      generatedAt: new Date().toISOString(),
      notice:
        "This is the personal data TAMAGNE holds about you. Uploaded document files can be provided on request. " +
        "Reports other people made about your seller account are not included, to protect reporters; you can ask about them through an appeal.",
      account: { id: user.id, phone: user.phoneE164, email: user.email, status: user.status, preferredLocale: user.preferredLocale, createdAt: iso(user.createdAt), lastLoginAt: iso(user.lastLoginAt) },
      sellerProfile: seller
        ? { tamagneId: seller.tamagneId, displayName: seller.displayName, displayNameAm: seller.displayNameAm, sellerType: seller.sellerType, accountStatus: seller.accountStatus, isPublic: seller.isPublic, region: seller.region, city: seller.city, createdAt: iso(seller.createdAt) }
        : null,
      consents: consents.map((c) => ({ purpose: c.purpose, policyVersion: c.policyVersion, status: c.status, locale: c.locale, grantedAt: iso(c.grantedAt), withdrawnAt: iso(c.withdrawnAt) })),
      verificationBadges: badges.map((b) => ({ level: b.level, method: b.method, status: b.status, issuedAt: iso(b.issuedAt), expiresAt: iso(b.expiresAt), revokedAt: iso(b.revokedAt), revocationReason: b.revocationReason })),
      faydaResults: idVerifications.map((v) => ({
        status: v.status,
        environment: v.environment,
        verifiedAt: iso(v.verifiedAt),
        expiresAt: iso(v.expiresAt),
        failureCode: v.failureCode,
        verifiedName: v.verifiedNameEnc ? this.crypto.cipher.decrypt(Buffer.from(v.verifiedNameEnc), `identity_verifications.verifiedNameEnc:${v.id}`) : null,
      })),
      manualIdentityReviews: idReviews.map((r) => ({ documentType: r.documentType, status: r.status, decisionReason: r.decisionReason, submittedAt: iso(r.submittedAt), decidedAt: iso(r.decidedAt), fileDeletedAt: iso(r.purgedAt) })),
      business: business
        ? {
            legalName: business.legalName,
            tradeName: business.tradeName,
            legalForm: business.legalForm,
            registrationNumber: business.registrationNumber,
            tinNumber: business.tinNumber,
            region: business.region,
            city: business.city,
            status: business.status,
            decisionReason: business.decisionReason,
            documents: business.documents.map((d) => ({ type: d.type, mimeType: d.mimeType, sizeBytes: d.sizeBytes, scanStatus: d.scanStatus, reviewStatus: d.reviewStatus, uploadedAt: iso(d.createdAt), retainUntil: iso(d.retainUntil), fileDeletedAt: iso(d.purgedAt) })),
          }
        : null,
      qrCodes: qr.map((q) => ({ status: q.status, createdAt: iso(q.createdAt), revokedAt: iso(q.revokedAt) })),
      appeals: appeals.map((a) => ({ subjectType: a.subjectType, reason: a.reason, status: a.status, decisionNote: a.decisionNote, createdAt: iso(a.createdAt), decidedAt: iso(a.decidedAt) })),
      reportsYouFiled: reportsFiled.map((r) => ({ category: r.category, description: r.description, status: r.status, createdAt: iso(r.createdAt) })),
      notifications: notifications.map((n) => ({ templateKey: n.templateKey, createdAt: iso(n.createdAt), readAt: iso(n.readAt) })),
      sessions: sessions.map((x) => ({ createdAt: iso(x.createdAt), lastSeenAt: iso(x.lastSeenAt), expiresAt: iso(x.expiresAt), revokedAt: iso(x.revokedAt) })),
      privacyRequests: requests.map((r) => ({ type: r.type, status: r.status, createdAt: iso(r.createdAt), completedAt: iso(r.completedAt), outcome: r.outcome })),
    };
    const now = new Date();
    await db.dataSubjectRequest.create({
      data: { userId: user.id, type: "ACCESS", status: "COMPLETED", dueAt: new Date(now.getTime() + DAY), completedAt: now, outcome: "Self-service export downloaded" },
    });
    await this.audit.record({ action: "privacy.export", actorType: "USER", actorUserId: user.id, targetType: "user", targetId: user.id, ...meta });
    return data;
  }

  // ───────────── Requests ─────────────

  async mine(viewer: Viewer) {
    const rows = await this.prisma.client.dataSubjectRequest.findMany({ where: { userId: viewer.userId }, orderBy: { createdAt: "desc" }, take: 50 });
    return rows.map((r) => ({ id: r.id, type: r.type, status: r.status, details: r.details, outcome: r.outcome, dueAt: r.dueAt.toISOString(), createdAt: r.createdAt.toISOString(), completedAt: r.completedAt?.toISOString() ?? null }));
  }

  async requestCorrection(viewer: Viewer, details: string, meta: Meta) {
    await this.prisma.client.$transaction(async (tx) => {
      const r = await tx.dataSubjectRequest.create({
        data: { userId: viewer.userId, type: "CORRECTION", details, dueAt: new Date(Date.now() + this.config.privacy.dsrResponseDays * DAY) },
      });
      await this.audit.record({ action: "privacy.correction_requested", actorType: "USER", actorUserId: viewer.userId, targetType: "data_subject_request", targetId: r.id, ...meta }, tx);
    });
    return this.mine(viewer);
  }

  /**
   * Deletion: the account is locked and hidden at once (sessions ended, profile hidden,
   * QR revoked); data is erased after the grace period by the deletion job.
   */
  async requestDeletion(viewer: Viewer, meta: Meta) {
    if (viewer.isStaff) throw new ApiError(HttpStatus.CONFLICT, "staff_account", "Staff accounts must have their roles removed before deletion.");
    const db = this.prisma.client;
    const now = new Date();
    // With a zero grace period the request is due a moment later (dueAt must follow createdAt).
    const dueAt = new Date(now.getTime() + Math.max(this.config.privacy.deletionGraceDays * DAY, 1000));
    try {
      await db.$transaction(async (tx) => {
        const r = await tx.dataSubjectRequest.create({ data: { userId: viewer.userId, type: "DELETION", status: "RECEIVED", dueAt } });
        await tx.user.update({ where: { id: viewer.userId }, data: { status: "PENDING_DELETION" } });
        await tx.session.updateMany({ where: { userId: viewer.userId, revokedAt: null }, data: { revokedAt: now } });
        const seller = await tx.sellerProfile.findUnique({ where: { userId: viewer.userId } });
        if (seller) {
          await tx.sellerProfile.update({ where: { id: seller.id }, data: { isPublic: false } });
          await tx.qrToken.updateMany({ where: { sellerProfileId: seller.id, status: "ACTIVE" }, data: { status: "REVOKED", revokedAt: now, revokedReason: "account deletion requested" } });
        }
        await tx.consentRecord.updateMany({ where: { userId: viewer.userId, purpose: { in: ["PUBLIC_PROFILE_DISPLAY", "MARKETING_COMMUNICATIONS"] }, status: "GRANTED" }, data: { status: "WITHDRAWN", withdrawnAt: now, withdrawalReason: "account deletion requested" } });
        await this.audit.record({ action: "privacy.deletion_requested", actorType: "USER", actorUserId: viewer.userId, targetType: "data_subject_request", targetId: r.id, ...meta }, tx);
      });
    } catch (e) {
      if ((e as { code?: string })?.code === "P2002") throw new ApiError(HttpStatus.CONFLICT, "deletion_pending", "A deletion request is already in progress.");
      throw e;
    }
    return { scheduledFor: dueAt.toISOString() };
  }

  // ───────────── Staff ─────────────

  async list(filter: { status?: "RECEIVED" | "IN_PROGRESS" | "COMPLETED" | "REJECTED" }) {
    const rows = await this.prisma.client.dataSubjectRequest.findMany({
      where: filter.status ? { status: filter.status } : { status: { in: ["RECEIVED", "IN_PROGRESS"] } },
      orderBy: { dueAt: "asc" },
      take: 200,
      include: { user: { select: { status: true, sellerProfile: { select: { tamagneId: true, displayName: true } } } } },
    });
    return rows.map((r) => ({
      id: r.id,
      type: r.type,
      status: r.status,
      details: r.details,
      outcome: r.outcome,
      dueAt: r.dueAt.toISOString(),
      overdue: r.dueAt.getTime() < Date.now() && (r.status === "RECEIVED" || r.status === "IN_PROGRESS"),
      createdAt: r.createdAt.toISOString(),
      userStatus: r.user.status,
      seller: r.user.sellerProfile,
    }));
  }

  /** Staff progress correction requests, or cancel a pending deletion (account restored). */
  async update(staff: Viewer, id: string, input: { status: "IN_PROGRESS" | "COMPLETED" | "REJECTED"; outcome?: string }, meta: Meta) {
    const db = this.prisma.client;
    const r = await db.dataSubjectRequest.findUnique({ where: { id } });
    if (!r) throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "Request not found.");
    if (r.userId === staff.userId) throw new ApiError(HttpStatus.FORBIDDEN, "own_account", "You cannot handle your own request.");
    if (r.status === "COMPLETED" || r.status === "REJECTED") throw new ApiError(HttpStatus.CONFLICT, "already_closed", "This request is closed.");
    if (input.status !== "IN_PROGRESS" && !input.outcome) throw new ApiError(HttpStatus.UNPROCESSABLE_ENTITY, "outcome_required", "Describe the outcome.");
    if (r.type === "DELETION" && input.status === "COMPLETED") {
      throw new ApiError(HttpStatus.CONFLICT, "use_deletion_job", "Deletions are carried out by the deletion job after the grace period.");
    }
    await db.$transaction(async (tx) => {
      await tx.dataSubjectRequest.update({
        where: { id },
        data: { status: input.status, handledById: staff.userId, ...(input.status !== "IN_PROGRESS" ? { outcome: input.outcome, completedAt: new Date() } : {}) },
      });
      if (r.type === "DELETION" && input.status === "REJECTED") {
        // Cancelling a deletion restores sign-in; public visibility stays off until the seller turns it on again.
        await tx.user.updateMany({ where: { id: r.userId, status: "PENDING_DELETION" }, data: { status: "ACTIVE" } });
      }
      await this.audit.record({ action: `privacy.request_${input.status.toLowerCase()}`, actorType: "STAFF", actorUserId: staff.userId, targetType: "data_subject_request", targetId: id, ...meta, metadata: { type: r.type } }, tx);
    });
    return this.list({});
  }

  // ───────────── Deletion and retention jobs ─────────────

  /** Carries out deletions whose grace period has ended. Idempotent. */
  async runDeletionJob(now = new Date()) {
    const due = await this.prisma.client.dataSubjectRequest.findMany({ where: { type: "DELETION", status: { in: ["RECEIVED", "IN_PROGRESS"] }, dueAt: { lte: now } }, take: 200 });
    let deleted = 0;
    for (const r of due) {
      await this.executeDeletion(r.id, r.userId);
      deleted++;
    }
    await this.audit.recordSafe({ action: "job.deletions", actorType: "SYSTEM", metadata: { deleted } });
    return { deleted };
  }

  private async executeDeletion(requestId: string, userId: string) {
    const db = this.prisma.client;
    const now = new Date();
    const user = await db.user.findUniqueOrThrow({ where: { id: userId } });
    const seller = await db.sellerProfile.findUnique({ where: { userId } });

    // 1. Files first (outside the transaction; deleting a missing object is a no-op).
    const files: { key: string }[] = [];
    if (seller) {
      const biz = await db.business.findUnique({ where: { sellerProfileId: seller.id }, select: { id: true } });
      if (biz) files.push(...(await db.businessDocument.findMany({ where: { businessId: biz.id, purgedAt: null }, select: { storageKey: true } })).map((d) => ({ key: d.storageKey })));
      files.push(...(await db.identityReviewRequest.findMany({ where: { sellerProfileId: seller.id, purgedAt: null }, select: { storageKey: true } })).map((d) => ({ key: d.storageKey })));
    }
    for (const f of files) {
      try {
        await this.store.delete(f.key);
      } catch {
        this.logger.error("object delete failed during account deletion; will retry on next run");
        return; // leave the request open so the job retries
      }
    }

    // 2. Scrub and close in one transaction.
    await db.$transaction(async (tx) => {
      if (seller) {
        const live = await tx.sellerBadge.findMany({ where: { sellerProfileId: seller.id, status: { in: ["ACTIVE", "PENDING", "SUSPENDED"] } } });
        for (const b of live) await tx.sellerBadge.update({ where: { id: b.id }, data: { status: "REVOKED", revokedAt: now, revocationReason: "account deleted" } });
        await tx.qrToken.updateMany({ where: { sellerProfileId: seller.id, status: "ACTIVE" }, data: { status: "REVOKED", revokedAt: now, revokedReason: "account deleted" } });
        await tx.identityVerification.updateMany({ where: { sellerProfileId: seller.id, status: "VERIFIED" }, data: { status: "REVOKED", revokedAt: now } });
        await tx.identityVerification.updateMany({ where: { sellerProfileId: seller.id }, data: { subjectHash: null, verifiedNameEnc: null } });
        const biz = await tx.business.findUnique({ where: { sellerProfileId: seller.id } });
        if (biz) {
          await tx.businessDocument.updateMany({ where: { businessId: biz.id, purgedAt: null }, data: { purgedAt: now } });
          await tx.business.update({ where: { id: biz.id }, data: { legalName: "Deleted business", tradeName: null, registrationNumber: null, tinNumber: null, region: null, city: null, decisionReason: null } });
        }
        await tx.identityReviewRequest.updateMany({ where: { sellerProfileId: seller.id, status: "SUBMITTED" }, data: { status: "WITHDRAWN" } });
        await tx.identityReviewRequest.updateMany({ where: { sellerProfileId: seller.id, purgedAt: null }, data: { purgedAt: now } });
        await tx.sellerProfile.update({
          where: { id: seller.id },
          data: { displayName: "Deleted seller", displayNameAm: null, region: null, city: null, isPublic: false, accountStatus: "DEACTIVATED", suspendedAt: null, suspensionReason: null, deletedAt: now },
        });
      }
      if (user.phoneE164) await tx.phoneOtpChallenge.updateMany({ where: { phoneE164: user.phoneE164 }, data: { phoneE164: null } });
      await tx.consentRecord.updateMany({ where: { userId, status: "GRANTED" }, data: { status: "WITHDRAWN", withdrawnAt: now, withdrawalReason: "account deleted" } });
      await tx.appeal.updateMany({ where: { submittedById: userId }, data: { reason: "[deleted at the user's request]" } });
      await tx.fraudReport.updateMany({ where: { reporterUserId: userId }, data: { reporterUserId: null, reporterContactEnc: null } });
      await tx.notification.deleteMany({ where: { userId } });
      await tx.mfaFactor.updateMany({ where: { userId, disabledAt: null }, data: { disabledAt: now } });
      await tx.session.updateMany({ where: { userId, revokedAt: null }, data: { revokedAt: now } });
      await tx.user.update({ where: { id: userId }, data: { status: "DELETED", phoneE164: null, email: null, passwordHash: null, deletedAt: now } });
      await tx.dataSubjectRequest.update({
        where: { id: requestId },
        data: {
          status: "COMPLETED",
          completedAt: now,
          outcome: "Account deleted. Kept without contact data: consent records, audit log, reports about the seller account, payment records (legal obligations / fraud prevention).",
        },
      });
      await this.audit.record({ action: "privacy.account_deleted", actorType: "SYSTEM", targetType: "user", targetId: userId, metadata: { requestId, files: files.length } }, tx);
    });
  }

  /** Retention schedule (docs/legal/RETENTION.md): purges what is no longer needed. Idempotent. */
  async runRetentionJob(now = new Date()) {
    const db = this.prisma.client;
    const result = { businessFiles: 0, identityFiles: 0, otpChallenges: 0, sessions: 0, oidcRequests: 0, notifications: 0, reporterContacts: 0 };

    const docs = await db.businessDocument.findMany({ where: { purgedAt: null, retainUntil: { lte: now } }, select: { id: true, storageKey: true }, take: 1000 });
    for (const d of docs) {
      await this.store.delete(d.storageKey);
      await db.businessDocument.update({ where: { id: d.id }, data: { purgedAt: now } });
      result.businessFiles++;
    }
    const ids = await db.identityReviewRequest.findMany({ where: { purgedAt: null, retainUntil: { lte: now } }, select: { id: true, storageKey: true }, take: 1000 });
    for (const d of ids) {
      await this.store.delete(d.storageKey);
      await db.identityReviewRequest.update({ where: { id: d.id }, data: { purgedAt: now } });
      result.identityFiles++;
    }
    // OTP challenges that back a phone badge are evidence and are kept.
    result.otpChallenges = (await db.phoneOtpChallenge.deleteMany({ where: { createdAt: { lt: new Date(now.getTime() - RETENTION.otpChallengeDays * DAY) }, badges: { none: {} } } })).count;
    const sessionCutoff = new Date(now.getTime() - RETENTION.sessionDaysAfterEnd * DAY);
    result.sessions = (await db.session.deleteMany({ where: { OR: [{ expiresAt: { lt: sessionCutoff } }, { revokedAt: { lt: sessionCutoff } }] } })).count;
    // Authorization requests backing a VERIFIED result are evidence and are kept.
    result.oidcRequests = (
      await db.oidcAuthRequest.deleteMany({ where: { createdAt: { lt: new Date(now.getTime() - RETENTION.oidcRequestDays * DAY) }, verifications: { none: { status: "VERIFIED" } } } })
    ).count;
    result.notifications = (await db.notification.deleteMany({ where: { createdAt: { lt: new Date(now.getTime() - RETENTION.notificationDays * DAY) } } })).count;
    result.reporterContacts = (
      await db.fraudReport.updateMany({
        where: { reporterContactEnc: { not: null }, resolvedAt: { lt: new Date(now.getTime() - RETENTION.reporterContactDaysAfterResolution * DAY) } },
        data: { reporterContactEnc: null },
      })
    ).count;
    await this.audit.recordSafe({ action: "job.retention", actorType: "SYSTEM", metadata: result });
    return result;
  }
}
