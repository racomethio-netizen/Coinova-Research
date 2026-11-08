import { HttpStatus, Inject, Injectable } from "@nestjs/common";
import { buildPublicVerificationSummary, maskPhone, normalizePhone, normalizeTamagneId } from "@tamagne/core";
import type { Prisma } from "@tamagne/db";
import { ApiError } from "../common/http.js";
import { APP_CONFIG } from "../config/config.module.js";
import type { AppConfig } from "../config/env.js";
import { AuditService } from "../audit/audit.service.js";
import type { Viewer } from "../auth/session.service.js";
import { PrismaService } from "../database/prisma.service.js";

type Tx = Prisma.TransactionClient;
interface Meta {
  requestId: string;
  ipHash?: string;
}

@Injectable()
export class SellersAdminService {
  constructor(
    @Inject(PrismaService) private readonly prisma: PrismaService,
    @Inject(AuditService) private readonly audit: AuditService,
    @Inject(APP_CONFIG) private readonly config: AppConfig,
  ) {}

  /** Search by Tamagne ID, phone number (exact) or display name (contains). */
  async search(q: string) {
    const query = q.trim().slice(0, 120);
    if (query.length < 2) return [];
    const tid = normalizeTamagneId(query);
    const phone = normalizePhone(query, { allowTestRange: this.config.allowTestPhoneNumbers });
    const or: Prisma.SellerProfileWhereInput[] = [
      { displayName: { contains: query, mode: "insensitive" } },
      { displayNameAm: { contains: query, mode: "insensitive" } },
    ];
    if (tid) or.unshift({ tamagneId: tid });
    if (phone) or.unshift({ user: { phoneE164: phone } });
    const rows = await this.prisma.client.sellerProfile.findMany({
      where: { OR: or, deletedAt: null },
      take: 25,
      orderBy: { createdAt: "desc" },
      select: { tamagneId: true, displayName: true, displayNameAm: true, accountStatus: true, sellerType: true, createdAt: true },
    });
    return rows.map((r) => ({ ...r, createdAt: r.createdAt.toISOString() }));
  }

  private async byTamagneId(tamagneId: string) {
    const tid = normalizeTamagneId(tamagneId);
    const s = tid ? await this.prisma.client.sellerProfile.findUnique({ where: { tamagneId: tid } }) : null;
    if (!s) throw new ApiError(HttpStatus.NOT_FOUND, "seller_not_found", "Seller not found.");
    return s;
  }

  async detail(tamagneId: string) {
    const s = await this.byTamagneId(tamagneId);
    const db = this.prisma.client;
    const [user, badges, business, reports, appeals, events, reviews, manual] = await Promise.all([
      db.user.findUniqueOrThrow({ where: { id: s.userId }, select: { phoneE164: true, status: true, createdAt: true, lastLoginAt: true } }),
      db.sellerBadge.findMany({ where: { sellerProfileId: s.id }, orderBy: { createdAt: "desc" }, include: { identityVerification: { select: { environment: true } } } }),
      db.business.findUnique({ where: { sellerProfileId: s.id }, select: { id: true, legalName: true, status: true, tinNumber: true, submittedAt: true, decidedAt: true } }),
      db.fraudReport.findMany({ where: { sellerProfileId: s.id }, orderBy: { createdAt: "desc" }, take: 20, select: { id: true, category: true, status: true, createdAt: true } }),
      db.appeal.findMany({ where: { sellerProfileId: s.id }, orderBy: { createdAt: "desc" }, take: 20, select: { id: true, subjectType: true, status: true, createdAt: true } }),
      db.verificationEvent.findMany({ where: { sellerProfileId: s.id }, orderBy: { createdAt: "desc" }, take: 30, select: { level: true, type: true, actorType: true, reason: true, createdAt: true } }),
      db.reviewRecord.findMany({ where: { sellerProfileId: s.id }, orderBy: { createdAt: "desc" }, take: 20, select: { id: true, targetType: true, decision: true, reason: true, riskLevel: true, status: true, reviewerId: true, secondReviewerId: true, createdAt: true } }),
      db.identityReviewRequest.findFirst({ where: { sellerProfileId: s.id }, orderBy: { createdAt: "desc" }, select: { id: true, status: true, documentType: true } }),
    ]);
    const summary = buildPublicVerificationSummary(
      s.accountStatus,
      badges.map((b) => ({ ...b, identityEnvironment: b.identityVerification?.environment ?? null })),
      { production: this.config.isProduction },
    );
    const iso = (d: Date | null) => d?.toISOString() ?? null;
    return {
      tamagneId: s.tamagneId,
      displayName: s.displayName,
      displayNameAm: s.displayNameAm,
      sellerType: s.sellerType,
      accountStatus: s.accountStatus,
      suspensionReason: s.suspensionReason,
      suspendedAt: iso(s.suspendedAt),
      isPublic: s.isPublic,
      region: s.region,
      city: s.city,
      createdAt: s.createdAt.toISOString(),
      // Staff see a masked number only.
      user: { phone: user.phoneE164 ? maskPhone(user.phoneE164) : null, status: user.status, createdAt: user.createdAt.toISOString(), lastLoginAt: iso(user.lastLoginAt) },
      verification: summary,
      badges: badges.map((b) => ({ id: b.id, level: b.level, method: b.method, status: b.status, issuedAt: iso(b.issuedAt), expiresAt: iso(b.expiresAt), revokedAt: iso(b.revokedAt), revocationReason: b.revocationReason, environment: b.identityVerification?.environment ?? null })),
      business: business ? { ...business, submittedAt: iso(business.submittedAt), decidedAt: iso(business.decidedAt) } : null,
      manualIdentityReview: manual,
      reports: reports.map((r) => ({ ...r, createdAt: r.createdAt.toISOString() })),
      appeals: appeals.map((a) => ({ ...a, createdAt: a.createdAt.toISOString() })),
      events: events.map((e) => ({ ...e, createdAt: e.createdAt.toISOString() })),
      reviews: reviews.map((r) => ({ ...r, createdAt: r.createdAt.toISOString() })),
    };
  }

  private async assertNotSelf(staff: Viewer, sellerUserId: string) {
    if (staff.userId === sellerUserId) throw new ApiError(HttpStatus.FORBIDDEN, "own_account", "You cannot act on your own seller account.");
  }

  /** Suspension takes effect immediately (public page shows "Suspended", no current badges). */
  async suspend(staff: Viewer, tamagneId: string, reason: string, meta: Meta) {
    const s = await this.byTamagneId(tamagneId);
    await this.assertNotSelf(staff, s.userId);
    if (s.accountStatus === "SUSPENDED") throw new ApiError(HttpStatus.CONFLICT, "already_suspended", "This seller is already suspended.");
    const now = new Date();
    await this.prisma.client.$transaction(async (tx) => {
      const review = await tx.reviewRecord.create({
        data: { sellerProfileId: s.id, targetType: "ACCOUNT_STATUS", targetId: s.id, reviewerId: staff.userId, decision: "SUSPEND", reason, riskLevel: "MEDIUM", status: "FINAL", finalizedAt: now },
      });
      await tx.sellerProfile.update({ where: { id: s.id }, data: { accountStatus: "SUSPENDED", suspendedAt: now, suspensionReason: reason } });
      await tx.verificationEvent.create({ data: { sellerProfileId: s.id, level: "PHONE", type: "SUSPENDED", actorType: "STAFF", actorUserId: staff.userId, reason: "account suspended" } });
      await this.notify(tx, s.userId, "account.suspended", { tamagneId: s.tamagneId });
      await this.audit.record({ action: "seller.suspended", actorType: "STAFF", actorUserId: staff.userId, targetType: "seller_profile", targetId: s.id, ...meta, metadata: { reviewId: review.id } }, tx);
    });
    return this.detail(s.tamagneId);
  }

  /**
   * Reactivation is a high-risk override: it is always recorded as HIGH risk and only takes
   * effect when a different reviewer approves it (POST /admin/reviews/:id/second-review).
   */
  async requestReactivation(staff: Viewer, tamagneId: string, reason: string, meta: Meta, tx?: Tx) {
    const s = await this.byTamagneId(tamagneId);
    await this.assertNotSelf(staff, s.userId);
    if (s.accountStatus !== "SUSPENDED") throw new ApiError(HttpStatus.CONFLICT, "not_suspended", "This seller is not suspended.");
    const db = tx ?? this.prisma.client;
    const pending = await db.reviewRecord.findFirst({ where: { targetType: "ACCOUNT_STATUS", targetId: s.id, status: "PENDING_SECOND_REVIEW" } });
    if (pending) throw new ApiError(HttpStatus.CONFLICT, "awaiting_second_review", "A reactivation is already waiting for a second reviewer.");
    const review = await db.reviewRecord.create({
      data: { sellerProfileId: s.id, targetType: "ACCOUNT_STATUS", targetId: s.id, reviewerId: staff.userId, decision: "REACTIVATE", reason, riskLevel: "HIGH", requiresSecondReview: true, status: "PENDING_SECOND_REVIEW" },
    });
    await this.audit.record({ action: "seller.reactivation_requested", actorType: "STAFF", actorUserId: staff.userId, targetType: "seller_profile", targetId: s.id, ...meta, metadata: { reviewId: review.id } }, tx);
    return { status: "PENDING_SECOND_REVIEW" as const, reviewId: review.id };
  }

  /** Called from the second-review flow once both reviewers agree. */
  async applyReactivation(tx: Tx, sellerProfileId: string, staffUserId: string) {
    const s = await tx.sellerProfile.findUniqueOrThrow({ where: { id: sellerProfileId } });
    await tx.sellerProfile.update({ where: { id: s.id }, data: { accountStatus: "ACTIVE", suspendedAt: null, suspensionReason: null } });
    await tx.verificationEvent.create({ data: { sellerProfileId: s.id, level: "PHONE", type: "REINSTATED", actorType: "STAFF", actorUserId: staffUserId, reason: "account reactivated" } });
    await this.notify(tx, s.userId, "account.reactivated", { tamagneId: s.tamagneId });
  }

  async revokeBadge(staff: Viewer, badgeId: string, reason: string, meta: Meta) {
    const db = this.prisma.client;
    const b = await db.sellerBadge.findUnique({ where: { id: badgeId }, include: { sellerProfile: { select: { userId: true, tamagneId: true } } } });
    if (!b) throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "Badge not found.");
    await this.assertNotSelf(staff, b.sellerProfile.userId);
    if (b.status === "REVOKED" || b.status === "EXPIRED") throw new ApiError(HttpStatus.CONFLICT, "already_closed", "This badge is no longer active.");
    await db.$transaction(async (tx) => {
      await tx.sellerBadge.update({ where: { id: b.id }, data: { status: "REVOKED", revokedAt: new Date(), revokedById: staff.userId, revocationReason: reason } });
      if (b.level === "BUSINESS_DOCUMENTS") {
        await tx.business.updateMany({ where: { sellerProfileId: b.sellerProfileId, status: "VERIFIED" }, data: { status: "SUSPENDED" } });
      }
      await tx.verificationEvent.create({ data: { sellerProfileId: b.sellerProfileId, badgeId: b.id, level: b.level, type: "REVOKED", actorType: "STAFF", actorUserId: staff.userId, reason } });
      await this.notify(tx, b.sellerProfile.userId, "badge.revoked", { level: b.level });
      await this.audit.record({ action: "badge.revoked", actorType: "STAFF", actorUserId: staff.userId, targetType: "seller_badge", targetId: b.id, ...meta, metadata: { level: b.level } }, tx);
    });
    return this.detail(b.sellerProfile.tamagneId);
  }

  private async notify(tx: Tx, userId: string, templateKey: string, payload: Record<string, string>) {
    const u = await tx.user.findUniqueOrThrow({ where: { id: userId }, select: { preferredLocale: true } });
    await tx.notification.create({ data: { userId, channel: "IN_APP", templateKey, locale: u.preferredLocale, payload } });
  }
}
