import { HttpStatus, Inject, Injectable } from "@nestjs/common";
import { ApiError } from "../common/http.js";
import { AuditService } from "../audit/audit.service.js";
import type { Viewer } from "../auth/session.service.js";
import { PrismaService } from "../database/prisma.service.js";
import { SellersAdminService } from "../admin/sellers-admin.service.js";

export type AppealSubject = "BADGE_DECISION" | "ACCOUNT_SUSPENSION" | "FRAUD_REPORT_OUTCOME" | "DOCUMENT_REJECTION" | "DATA_CORRECTION";

interface Meta {
  requestId: string;
  ipHash?: string;
}

/**
 * Seller appeals. Status meaning: UPHELD = the original decision stands (appeal denied);
 * OVERTURNED = the original decision is reversed (appeal granted).
 * Appeals are decided by someone other than the submitter and other than the staff member
 * who made the decision being appealed.
 */
@Injectable()
export class AppealsService {
  constructor(
    @Inject(PrismaService) private readonly prisma: PrismaService,
    @Inject(AuditService) private readonly audit: AuditService,
    @Inject(SellersAdminService) private readonly sellers: SellersAdminService,
  ) {}

  private async sellerOf(userId: string) {
    const s = await this.prisma.client.sellerProfile.findUnique({ where: { userId } });
    if (!s) throw new ApiError(HttpStatus.NOT_FOUND, "seller_not_found", "Create your seller profile first.");
    return s;
  }

  async submit(viewer: Viewer, input: { subjectType: AppealSubject; reason: string }, meta: Meta) {
    const s = await this.sellerOf(viewer.userId);
    if (input.subjectType === "ACCOUNT_SUSPENSION" && s.accountStatus !== "SUSPENDED") {
      throw new ApiError(HttpStatus.CONFLICT, "not_suspended", "Your account is not suspended.");
    }
    try {
      await this.prisma.client.$transaction(async (tx) => {
        const appeal = await tx.appeal.create({ data: { sellerProfileId: s.id, submittedById: viewer.userId, subjectType: input.subjectType, reason: input.reason } });
        await this.audit.record({ action: "appeal.submitted", actorType: "USER", actorUserId: viewer.userId, targetType: "appeal", targetId: appeal.id, ...meta, metadata: { subjectType: input.subjectType } }, tx);
      });
    } catch (e) {
      if ((e as { code?: string })?.code === "P2002") throw new ApiError(HttpStatus.CONFLICT, "appeal_open", "You already have an open appeal about this.");
      throw e;
    }
    return this.mine(viewer);
  }

  async mine(viewer: Viewer) {
    const s = await this.sellerOf(viewer.userId);
    const rows = await this.prisma.client.appeal.findMany({ where: { sellerProfileId: s.id }, orderBy: { createdAt: "desc" }, take: 50 });
    return rows.map((a) => ({ id: a.id, subjectType: a.subjectType, reason: a.reason, status: a.status, decisionNote: a.decisionNote, createdAt: a.createdAt.toISOString(), decidedAt: a.decidedAt?.toISOString() ?? null }));
  }

  async list(status?: "SUBMITTED" | "UNDER_REVIEW" | "UPHELD" | "OVERTURNED" | "WITHDRAWN") {
    const rows = await this.prisma.client.appeal.findMany({
      where: status ? { status } : { status: { in: ["SUBMITTED", "UNDER_REVIEW"] } },
      orderBy: { createdAt: "asc" },
      take: 200,
      include: { sellerProfile: { select: { tamagneId: true, displayName: true, accountStatus: true, suspensionReason: true } } },
    });
    return rows.map((a) => ({
      id: a.id,
      subjectType: a.subjectType,
      reason: a.reason,
      status: a.status,
      createdAt: a.createdAt.toISOString(),
      decisionNote: a.decisionNote,
      seller: a.sellerProfile,
    }));
  }

  /** Staff who made the appealed decision may not decide the appeal. */
  private async assertNoConflict(staff: Viewer, sellerProfileId: string, subject: AppealSubject) {
    const db = this.prisma.client;
    const types = subject === "ACCOUNT_SUSPENSION" ? (["ACCOUNT_STATUS"] as const) : (["BUSINESS", "IDENTITY_MANUAL"] as const);
    const last = await db.reviewRecord.findFirst({ where: { sellerProfileId, targetType: { in: [...types] } }, orderBy: { createdAt: "desc" } });
    const revokedByMe = await db.sellerBadge.count({ where: { sellerProfileId, revokedById: staff.userId } });
    if (last?.reviewerId === staff.userId || last?.secondReviewerId === staff.userId || (subject !== "ACCOUNT_SUSPENSION" && revokedByMe > 0)) {
      throw new ApiError(HttpStatus.FORBIDDEN, "conflict_of_interest", "You made the decision being appealed. Another staff member must decide this appeal.");
    }
  }

  async decide(staff: Viewer, id: string, input: { outcome: "UPHELD" | "OVERTURNED"; note: string }, meta: Meta) {
    const db = this.prisma.client;
    const appeal = await db.appeal.findUnique({ where: { id }, include: { sellerProfile: true } });
    if (!appeal) throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "Appeal not found.");
    if (appeal.status !== "SUBMITTED" && appeal.status !== "UNDER_REVIEW") throw new ApiError(HttpStatus.CONFLICT, "already_decided", "This appeal has already been decided.");
    if (appeal.submittedById === staff.userId || appeal.sellerProfile.userId === staff.userId) {
      throw new ApiError(HttpStatus.FORBIDDEN, "own_account", "You cannot decide your own appeal.");
    }
    await this.assertNoConflict(staff, appeal.sellerProfileId, appeal.subjectType);
    let effect: string | null = null;
    await db.$transaction(async (tx) => {
      await tx.appeal.update({ where: { id }, data: { status: input.outcome, decidedById: staff.userId, decidedAt: new Date(), decisionNote: input.note } });
      if (input.outcome === "OVERTURNED") {
        if (appeal.subjectType === "ACCOUNT_SUSPENSION") {
          await this.sellers.requestReactivation(staff, appeal.sellerProfile.tamagneId, `Appeal overturned: ${input.note}`, meta, tx);
          effect = "reactivation_pending_second_review";
        } else if (appeal.subjectType === "DOCUMENT_REJECTION" || appeal.subjectType === "BADGE_DECISION") {
          const reopened = await tx.business.updateMany({
            where: { sellerProfileId: appeal.sellerProfileId, status: "REJECTED" },
            data: { status: "DRAFT", decisionReason: "Your appeal was successful. Please review your documents and submit again." },
          });
          effect = reopened.count ? "business_reopened" : null;
        }
      }
      const user = await tx.user.findUniqueOrThrow({ where: { id: appeal.sellerProfile.userId }, select: { preferredLocale: true } });
      await tx.notification.create({ data: { userId: appeal.sellerProfile.userId, channel: "IN_APP", templateKey: `appeal.${input.outcome.toLowerCase()}`, locale: user.preferredLocale, payload: { appealId: id } } });
      await this.audit.record({ action: "appeal.decided", actorType: "STAFF", actorUserId: staff.userId, targetType: "appeal", targetId: id, ...meta, metadata: { outcome: input.outcome, effect } }, tx);
    });
    return { id, outcome: input.outcome, effect };
  }
}
