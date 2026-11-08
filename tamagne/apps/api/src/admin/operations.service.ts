import { HttpStatus, Inject, Injectable } from "@nestjs/common";
import { maskPhone, normalizePhone } from "@tamagne/core";
import { Prisma } from "@tamagne/db";
import { ApiError } from "../common/http.js";
import { APP_CONFIG } from "../config/config.module.js";
import type { AppConfig } from "../config/env.js";
import { AuditService } from "../audit/audit.service.js";
import type { StaffRole, Viewer } from "../auth/session.service.js";
import { PrismaService } from "../database/prisma.service.js";

interface Meta {
  requestId: string;
  ipHash?: string;
}

const DAY = 86_400_000;

/** Admin operations that are not tied to a single review queue. */
@Injectable()
export class OperationsService {
  constructor(
    @Inject(PrismaService) private readonly prisma: PrismaService,
    @Inject(AuditService) private readonly audit: AuditService,
    @Inject(APP_CONFIG) private readonly config: AppConfig,
  ) {}

  // ───────────── Overview ─────────────

  async overview() {
    const db = this.prisma.client;
    const soon = new Date(Date.now() + 30 * DAY);
    const [businesses, identity, secondReviews, reports, appeals, sellers, expiring, lapsed] = await Promise.all([
      db.business.count({ where: { status: "SUBMITTED" } }),
      db.identityReviewRequest.count({ where: { status: "SUBMITTED", purgedAt: null } }),
      db.reviewRecord.count({ where: { status: "PENDING_SECOND_REVIEW" } }),
      db.fraudReport.count({ where: { status: { in: ["SUBMITTED", "TRIAGED", "INVESTIGATING"] } } }),
      db.appeal.count({ where: { status: { in: ["SUBMITTED", "UNDER_REVIEW"] } } }),
      db.sellerProfile.groupBy({ by: ["accountStatus"], _count: { _all: true } }),
      db.sellerBadge.count({ where: { status: "ACTIVE", expiresAt: { gt: new Date(), lte: soon } } }),
      db.sellerBadge.count({ where: { status: "ACTIVE", expiresAt: { lte: new Date() } } }),
    ]);
    return {
      queues: { businesses, identity, secondReviews, reports, appeals },
      sellers: Object.fromEntries(sellers.map((s) => [s.accountStatus, s._count._all])),
      badges: { expiringIn30Days: expiring, lapsedNotYetExpired: lapsed },
    };
  }

  async secondReviews() {
    const rows = await this.prisma.client.reviewRecord.findMany({
      where: { status: "PENDING_SECOND_REVIEW" },
      orderBy: { createdAt: "asc" },
      include: { sellerProfile: { select: { tamagneId: true, displayName: true } } },
    });
    return rows.map((r) => ({
      id: r.id,
      targetType: r.targetType,
      targetId: r.targetId,
      decision: r.decision,
      reason: r.reason,
      riskLevel: r.riskLevel,
      reviewerId: r.reviewerId,
      createdAt: r.createdAt.toISOString(),
      seller: r.sellerProfile,
    }));
  }

  // ───────────── Expiry ─────────────

  async expiring(days: number) {
    const now = new Date();
    const rows = await this.prisma.client.sellerBadge.findMany({
      where: { status: "ACTIVE", expiresAt: { lte: new Date(now.getTime() + days * DAY) } },
      orderBy: { expiresAt: "asc" },
      take: 500,
      include: { sellerProfile: { select: { tamagneId: true, displayName: true } } },
    });
    return rows.map((b) => ({
      badgeId: b.id,
      level: b.level,
      expiresAt: b.expiresAt!.toISOString(),
      lapsed: b.expiresAt! <= now,
      seller: b.sellerProfile,
    }));
  }

  /**
   * Expiry job: marks lapsed badges and identity results EXPIRED (history kept) and sends
   * one reminder per badge 30 days before expiry. Safe to run repeatedly.
   */
  async runExpiryJob(now = new Date()) {
    const db = this.prisma.client;
    const lapsed = await db.sellerBadge.findMany({
      where: { status: "ACTIVE", expiresAt: { lte: now } },
      include: { sellerProfile: { select: { userId: true, user: { select: { preferredLocale: true } } } } },
      take: 5000,
    });
    for (const b of lapsed) {
      await db.$transaction(async (tx) => {
        const changed = await tx.sellerBadge.updateMany({ where: { id: b.id, status: "ACTIVE" }, data: { status: "EXPIRED" } });
        if (changed.count !== 1) return;
        await tx.verificationEvent.create({ data: { sellerProfileId: b.sellerProfileId, badgeId: b.id, level: b.level, type: "EXPIRED", actorType: "SYSTEM" } });
        await tx.notification.create({ data: { userId: b.sellerProfile.userId, channel: "IN_APP", templateKey: "badge.expired", locale: b.sellerProfile.user.preferredLocale, payload: { level: b.level, badgeId: b.id } } });
      });
    }
    const ivs = await db.identityVerification.updateMany({ where: { status: "VERIFIED", expiresAt: { lte: now } }, data: { status: "EXPIRED" } });

    const upcoming = await db.sellerBadge.findMany({
      where: { status: "ACTIVE", expiresAt: { gt: now, lte: new Date(now.getTime() + 30 * DAY) } },
      include: { sellerProfile: { select: { userId: true, user: { select: { preferredLocale: true } } } } },
      take: 5000,
    });
    let reminders = 0;
    for (const b of upcoming) {
      const already = await db.notification.count({ where: { userId: b.sellerProfile.userId, templateKey: "badge.expiring", payload: { path: ["badgeId"], equals: b.id } } });
      if (already) continue;
      await db.notification.create({ data: { userId: b.sellerProfile.userId, channel: "IN_APP", templateKey: "badge.expiring", locale: b.sellerProfile.user.preferredLocale, payload: { level: b.level, badgeId: b.id, expiresAt: b.expiresAt!.toISOString() } } });
      reminders++;
    }
    const result = { expiredBadges: lapsed.length, expiredIdentityResults: ivs.count, reminders };
    await this.audit.recordSafe({ action: "job.expiry", actorType: "SYSTEM", metadata: result });
    return result;
  }

  // ───────────── Audit log ─────────────

  async auditLog(filter: { action?: string; actorUserId?: string; targetType?: string; targetId?: string; before?: bigint }) {
    const rows = await this.prisma.client.auditLog.findMany({
      where: {
        ...(filter.action ? { action: { startsWith: filter.action } } : {}),
        ...(filter.actorUserId ? { actorUserId: filter.actorUserId } : {}),
        ...(filter.targetType ? { targetType: filter.targetType } : {}),
        ...(filter.targetId ? { targetId: filter.targetId } : {}),
        ...(filter.before ? { seq: { lt: filter.before } } : {}),
      },
      orderBy: { seq: "desc" },
      take: 50,
    });
    return {
      entries: rows.map((r) => ({
        seq: r.seq.toString(),
        occurredAt: r.occurredAt.toISOString(),
        actorType: r.actorType,
        actorUserId: r.actorUserId,
        action: r.action,
        targetType: r.targetType,
        targetId: r.targetId,
        requestId: r.requestId,
        metadata: r.metadata,
        hash: r.hash,
      })),
      nextBefore: rows.length === 50 ? rows.at(-1)!.seq.toString() : null,
    };
  }

  async verifyAuditChain(staff: Viewer, meta: Meta) {
    const [row] = await this.prisma.client.$queryRaw<{ broken: bigint | null; total: bigint }[]>`
      select public.audit_log_verify_chain() as broken, (select count(*) from public.audit_logs) as total`;
    const result = { intact: row!.broken === null, firstBrokenSeq: row!.broken?.toString() ?? null, entries: row!.total.toString(), checkedAt: new Date().toISOString() };
    await this.audit.record({ action: result.intact ? "audit.chain_verified" : "audit.chain_broken", actorType: "STAFF", actorUserId: staff.userId, ...meta, metadata: { intact: result.intact } });
    return result;
  }

  // ───────────── Staff roles ─────────────

  async staff() {
    const rows = await this.prisma.client.staffRoleAssignment.findMany({
      where: { revokedAt: null },
      orderBy: { grantedAt: "asc" },
      include: { user: { select: { phoneE164: true, mfaFactors: { where: { confirmedAt: { not: null }, disabledAt: null }, select: { id: true } } } } },
    });
    return rows.map((r) => ({
      id: r.id,
      userId: r.userId,
      role: r.role,
      phone: r.user.phoneE164 ? maskPhone(r.user.phoneE164) : null,
      mfaEnrolled: r.user.mfaFactors.length > 0,
      grantedById: r.grantedById,
      grantedAt: r.grantedAt.toISOString(),
    }));
  }

  async grantRole(staff: Viewer, input: { phone: string; role: StaffRole }, meta: Meta) {
    const phone = normalizePhone(input.phone, { allowTestRange: this.config.allowTestPhoneNumbers });
    const user = phone ? await this.prisma.client.user.findUnique({ where: { phoneE164: phone } }) : null;
    if (!user || user.status !== "ACTIVE") throw new ApiError(HttpStatus.NOT_FOUND, "user_not_found", "No active account with that phone number. The person must register first.");
    if (user.id === staff.userId) throw new ApiError(HttpStatus.FORBIDDEN, "self_grant", "You cannot change your own roles.");
    try {
      const g = await this.prisma.client.staffRoleAssignment.create({ data: { userId: user.id, role: input.role, grantedById: staff.userId } });
      await this.audit.record({ action: "staff.role_granted", actorType: "STAFF", actorUserId: staff.userId, targetType: "user", targetId: user.id, ...meta, metadata: { role: input.role, grantId: g.id } });
    } catch (e) {
      if ((e as { code?: string })?.code === "P2002") throw new ApiError(HttpStatus.CONFLICT, "already_granted", "This person already has that role.");
      throw e;
    }
    return this.staff();
  }

  async revokeRole(staff: Viewer, grantId: string, meta: Meta) {
    const db = this.prisma.client;
    const g = await db.staffRoleAssignment.findUnique({ where: { id: grantId } });
    if (!g || g.revokedAt) throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "Grant not found.");
    if (g.userId === staff.userId) throw new ApiError(HttpStatus.FORBIDDEN, "self_grant", "You cannot change your own roles.");
    if (g.role === "SUPER_ADMIN" && (await db.staffRoleAssignment.count({ where: { role: "SUPER_ADMIN", revokedAt: null } })) <= 1) {
      throw new ApiError(HttpStatus.CONFLICT, "last_super_admin", "The last Super Admin cannot be removed.");
    }
    await db.$transaction(async (tx) => {
      await tx.staffRoleAssignment.update({ where: { id: g.id }, data: { revokedAt: new Date(), revokedById: staff.userId } });
      // Without any role left, staff sessions are ended.
      const remaining = await tx.staffRoleAssignment.count({ where: { userId: g.userId, revokedAt: null } });
      if (remaining === 0) await tx.session.updateMany({ where: { userId: g.userId, revokedAt: null }, data: { revokedAt: new Date() } });
      await this.audit.record({ action: "staff.role_revoked", actorType: "STAFF", actorUserId: staff.userId, targetType: "user", targetId: g.userId, ...meta, metadata: { role: g.role, grantId: g.id } }, tx);
    });
    return this.staff();
  }

  // ───────────── Pricing (admin-configurable; never affects verification) ─────────────

  async plans(includeInactive = true) {
    const plans = await this.prisma.client.plan.findMany({
      where: includeInactive ? {} : { isActive: true },
      orderBy: { createdAt: "asc" },
      include: { prices: { orderBy: { validFrom: "desc" } } },
    });
    return plans.map((p) => ({
      id: p.id,
      code: p.code,
      kind: p.kind,
      nameEn: p.nameEn,
      nameAm: p.nameAm,
      isActive: p.isActive,
      limits: p.limits,
      currentPrices: p.prices.filter((x) => x.validTo === null).map((x) => ({ id: x.id, amount: x.amount.toFixed(2), currency: x.currency, interval: x.interval, validFrom: x.validFrom.toISOString() })),
      priceHistory: includeInactive
        ? p.prices.map((x) => ({ id: x.id, amount: x.amount.toFixed(2), currency: x.currency, interval: x.interval, validFrom: x.validFrom.toISOString(), validTo: x.validTo?.toISOString() ?? null }))
        : undefined,
    }));
  }

  async createPlan(staff: Viewer, input: { code: string; kind: "FREE_INDIVIDUAL" | "PREMIUM_SELLER" | "BUSINESS_VERIFICATION" | "MARKETPLACE_API" | "ENTERPRISE"; nameEn: string; nameAm: string }, meta: Meta) {
    try {
      const p = await this.prisma.client.plan.create({ data: input });
      await this.audit.record({ action: "pricing.plan_created", actorType: "STAFF", actorUserId: staff.userId, targetType: "plan", targetId: p.id, ...meta, metadata: { slug: input.code, kind: input.kind } });
    } catch (e) {
      if ((e as { code?: string })?.code === "P2002") throw new ApiError(HttpStatus.CONFLICT, "plan_exists", "A plan with this code already exists.");
      throw e;
    }
    return this.plans();
  }

  async updatePlan(staff: Viewer, id: string, input: { nameEn?: string; nameAm?: string; isActive?: boolean }, meta: Meta) {
    await this.prisma.client.plan.update({ where: { id }, data: input }).catch(() => {
      throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "Plan not found.");
    });
    await this.audit.record({ action: "pricing.plan_updated", actorType: "STAFF", actorUserId: staff.userId, targetType: "plan", targetId: id, ...meta, metadata: { changedFields: Object.keys(input) } });
    return this.plans();
  }

  /** Prices are versioned: the current price for the same interval/currency is closed, never edited. */
  async setPrice(staff: Viewer, planId: string, input: { amount: string; currency: string; interval: "MONTH" | "YEAR" | "ONE_TIME" }, meta: Meta) {
    const db = this.prisma.client;
    const plan = await db.plan.findUnique({ where: { id: planId } });
    if (!plan) throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "Plan not found.");
    const amount = new Prisma.Decimal(input.amount);
    await db.$transaction(async (tx) => {
      const now = new Date();
      await tx.planPrice.updateMany({ where: { planId, interval: input.interval, currency: input.currency, validTo: null }, data: { validTo: now } });
      const p = await tx.planPrice.create({ data: { planId, amount, currency: input.currency, interval: input.interval, validFrom: new Date(now.getTime() + 1), createdById: staff.userId } });
      await this.audit.record({ action: "pricing.price_set", actorType: "STAFF", actorUserId: staff.userId, targetType: "plan_price", targetId: p.id, ...meta, metadata: { amount: amount.toFixed(2), currency: input.currency, interval: input.interval } }, tx);
    });
    return this.plans();
  }

  // ───────────── Revenue (from recorded payments only) ─────────────

  async revenue(months: number) {
    const since = new Date(Date.now() - months * 31 * DAY);
    const rows = await this.prisma.client.$queryRaw<{ month: Date; currency: string; status: string; total: Prisma.Decimal; count: bigint }[]>`
      select date_trunc('month', coalesce("paidAt", "createdAt")) as month, currency, status::text as status,
             sum(amount) as total, count(*) as count
      from public.payments
      where coalesce("paidAt", "createdAt") >= ${since}
      group by 1, 2, 3
      order by 1 desc, 2, 3`;
    const subs = await this.prisma.client.subscription.groupBy({ by: ["status"], _count: { _all: true } });
    return {
      since: since.toISOString(),
      byMonth: rows.map((r) => ({ month: r.month.toISOString().slice(0, 7), currency: r.currency, status: r.status, total: new Prisma.Decimal(r.total).toFixed(2), count: Number(r.count) })),
      subscriptions: Object.fromEntries(subs.map((s) => [s.status, s._count._all])),
      note: "Figures come only from recorded payments. No payment provider is connected yet.",
    };
  }
}
