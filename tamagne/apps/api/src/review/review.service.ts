import { HttpStatus, Inject, Injectable } from "@nestjs/common";
import { REQUIRED_DOCUMENT_TYPES, badgeExpiry } from "@tamagne/core";
import type { Prisma } from "@tamagne/db";
import { ApiError } from "../common/http.js";
import { AuditService } from "../audit/audit.service.js";
import { PrismaService } from "../database/prisma.service.js";
import { MALWARE_SCANNER, type MalwareScanner } from "../scanning/scanner.js";
import { OBJECT_STORE, type EncryptedObjectStore } from "../storage/storage.js";
import type { Viewer } from "../auth/session.service.js";
import { IdentityReviewService } from "../identity/identity-review.service.js";

type Tx = Prisma.TransactionClient;

export interface DocumentDecision {
  documentId: string;
  status: "APPROVED" | "REJECTED" | "NEEDS_RESUBMISSION";
  reason?: string;
}

export interface BusinessDecision {
  decision: "APPROVE" | "REJECT" | "REQUEST_CHANGES";
  reason: string;
  riskLevel: "LOW" | "MEDIUM" | "HIGH";
  documents: DocumentDecision[];
}

interface Meta {
  requestId: string;
  ipHash?: string;
}

/** Retain approved business documents for one year after the badge expires (proposed; legal review pending). */
const RETAIN_AFTER_BADGE_MS = 365 * 86_400_000;
/** Rejected submissions are kept 90 days for appeals (proposed; legal review pending). */
const RETAIN_REJECTED_MS = 90 * 86_400_000;

@Injectable()
export class ReviewService {
  constructor(
    @Inject(PrismaService) private readonly prisma: PrismaService,
    @Inject(AuditService) private readonly audit: AuditService,
    @Inject(OBJECT_STORE) private readonly store: EncryptedObjectStore,
    @Inject(MALWARE_SCANNER) private readonly scanner: MalwareScanner,
    @Inject(IdentityReviewService) private readonly identity: IdentityReviewService,
  ) {}

  async queue() {
    const [submitted, secondReviews] = await Promise.all([
      this.prisma.client.business.findMany({
        where: { status: "SUBMITTED" },
        orderBy: { submittedAt: "asc" },
        take: 100,
        include: {
          sellerProfile: { select: { tamagneId: true, displayName: true } },
          documents: { where: { purgedAt: null }, select: { scanStatus: true, reviewStatus: true } },
        },
      }),
      this.prisma.client.reviewRecord.findMany({
        where: { status: "PENDING_SECOND_REVIEW", targetType: "BUSINESS" },
        orderBy: { createdAt: "asc" },
        select: { id: true, targetId: true, reviewerId: true, decision: true, riskLevel: true, createdAt: true },
      }),
    ]);
    return {
      businesses: submitted.map((b) => ({
        id: b.id,
        legalName: b.legalName,
        submittedAt: b.submittedAt?.toISOString() ?? null,
        seller: b.sellerProfile,
        documents: { total: b.documents.length, unscanned: b.documents.filter((d) => d.scanStatus !== "CLEAN").length },
        awaitingSecondReview: secondReviews.some((r) => r.targetId === b.id),
      })),
      secondReviews: secondReviews.map((r) => ({ ...r, createdAt: r.createdAt.toISOString() })),
    };
  }

  async detail(businessId: string) {
    const b = await this.prisma.client.business.findUnique({
      where: { id: businessId },
      include: {
        sellerProfile: { select: { tamagneId: true, displayName: true, displayNameAm: true, accountStatus: true } },
        documents: { where: { purgedAt: null }, orderBy: { createdAt: "asc" } },
      },
    });
    if (!b) throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "Business not found.");
    const reviews = await this.prisma.client.reviewRecord.findMany({ where: { targetType: "BUSINESS", targetId: b.id }, orderBy: { createdAt: "desc" } });
    return {
      id: b.id,
      legalName: b.legalName,
      tradeName: b.tradeName,
      legalForm: b.legalForm,
      registrationNumber: b.registrationNumber,
      tinNumber: b.tinNumber,
      region: b.region,
      city: b.city,
      status: b.status,
      submittedAt: b.submittedAt?.toISOString() ?? null,
      seller: b.sellerProfile,
      documents: b.documents.map((d) => ({
        id: d.id,
        type: d.type,
        mimeType: d.mimeType,
        sizeBytes: d.sizeBytes,
        sha256: d.sha256,
        scanStatus: d.scanStatus,
        scanEngine: d.scanEngine,
        reviewStatus: d.reviewStatus,
        rejectionReason: d.rejectionReason,
        uploadedAt: d.createdAt.toISOString(),
      })),
      reviews: reviews.map((r) => ({
        id: r.id,
        decision: r.decision,
        reason: r.reason,
        riskLevel: r.riskLevel,
        status: r.status,
        reviewerId: r.reviewerId,
        secondReviewerId: r.secondReviewerId,
        secondDecision: r.secondDecision,
        createdAt: r.createdAt.toISOString(),
      })),
    };
  }

  /** Reviewers only ever receive files that passed the malware scan. Every view is audited. */
  async documentFile(staff: Viewer, documentId: string, meta: Meta) {
    const doc = await this.prisma.client.businessDocument.findUnique({ where: { id: documentId } });
    if (!doc || doc.purgedAt) throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "Document not found.");
    if (doc.scanStatus !== "CLEAN") throw new ApiError(HttpStatus.CONFLICT, "document_not_scanned", "This document has not passed the malware scan.");
    const data = await this.store.get(doc.storageKey);
    await this.audit.record({ action: "document.viewed", actorType: "STAFF", actorUserId: staff.userId, targetType: "business_document", targetId: doc.id, ...meta });
    return { data, mimeType: doc.mimeType, type: doc.type };
  }

  private async assertNotOwnAccount(staff: Viewer, sellerProfileId: string) {
    const seller = await this.prisma.client.sellerProfile.findUniqueOrThrow({ where: { id: sellerProfileId }, select: { userId: true } });
    if (seller.userId === staff.userId) throw new ApiError(HttpStatus.FORBIDDEN, "own_account", "You cannot review your own seller account.");
  }

  async decide(staff: Viewer, businessId: string, input: BusinessDecision, meta: Meta) {
    const business = await this.prisma.client.business.findUnique({ where: { id: businessId }, include: { documents: { where: { purgedAt: null } } } });
    if (!business) throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "Business not found.");
    if (business.status !== "SUBMITTED") throw new ApiError(HttpStatus.CONFLICT, "not_submitted", "This business is not awaiting review.");
    await this.assertNotOwnAccount(staff, business.sellerProfileId);
    const pending = await this.prisma.client.reviewRecord.findFirst({ where: { targetType: "BUSINESS", targetId: business.id, status: "PENDING_SECOND_REVIEW" } });
    if (pending) throw new ApiError(HttpStatus.CONFLICT, "awaiting_second_review", "A decision is already waiting for a second reviewer.");

    const docsById = new Map(business.documents.map((d) => [d.id, d]));
    for (const d of input.documents) {
      const doc = docsById.get(d.documentId);
      if (!doc) throw new ApiError(HttpStatus.UNPROCESSABLE_ENTITY, "unknown_document", "A document does not belong to this business.");
      if (d.status === "APPROVED" && doc.scanStatus !== "CLEAN") {
        throw new ApiError(HttpStatus.UNPROCESSABLE_ENTITY, "document_not_scanned", "Only documents that passed the malware scan can be approved.");
      }
      if (d.status !== "APPROVED" && !d.reason) {
        throw new ApiError(HttpStatus.UNPROCESSABLE_ENTITY, "reason_required", "Give a reason for every document you do not approve.");
      }
    }
    const finalStatus = new Map(business.documents.map((d) => [d.id, d.reviewStatus as string]));
    for (const d of input.documents) finalStatus.set(d.documentId, d.status);

    if (input.decision === "APPROVE") {
      if ([...finalStatus.values()].some((s) => s !== "APPROVED")) {
        throw new ApiError(HttpStatus.UNPROCESSABLE_ENTITY, "documents_undecided", "Approve every document (or remove it) before approving the business.");
      }
      const approvedTypes = new Set(business.documents.map((d) => d.type));
      const missing = REQUIRED_DOCUMENT_TYPES.filter((t) => !approvedTypes.has(t));
      if (missing.length) throw new ApiError(HttpStatus.UNPROCESSABLE_ENTITY, "documents_missing", `Required documents missing: ${missing.join(", ")}.`);
    }

    const highRisk = input.riskLevel === "HIGH";
    const now = new Date();
    try {
      return await this.prisma.client.$transaction(async (tx) => {
        for (const d of input.documents) {
          await tx.businessDocument.update({
            where: { id: d.documentId },
            data: { reviewStatus: d.status, rejectionReason: d.status === "APPROVED" ? null : d.reason! },
          });
        }
        // Every high-risk decision needs a different second reviewer (DB-enforced).
        const needsSecond = highRisk;
        const review = await tx.reviewRecord.create({
          data: {
            sellerProfileId: business.sellerProfileId,
            targetType: "BUSINESS",
            targetId: business.id,
            reviewerId: staff.userId,
            decision: input.decision === "APPROVE" ? "APPROVE" : input.decision === "REJECT" ? "REJECT" : "REQUEST_CHANGES",
            reason: input.reason,
            riskLevel: input.riskLevel,
            requiresSecondReview: highRisk,
            status: needsSecond ? "PENDING_SECOND_REVIEW" : "FINAL",
            finalizedAt: needsSecond ? null : now,
          },
        });
        await this.audit.record(
          { action: "review.business_decided", actorType: "STAFF", actorUserId: staff.userId, targetType: "business", targetId: business.id, ...meta, metadata: { decision: input.decision, riskLevel: input.riskLevel, reviewId: review.id } },
          tx,
        );
        if (needsSecond) return { status: "PENDING_SECOND_REVIEW" as const, reviewId: review.id, outcome: null };
        if (input.decision === "APPROVE") await this.finalizeApproval(tx, business.id, review.id, staff.userId);
        else await this.closeWithoutApproval(tx, business.id, input.decision, input.reason, staff.userId);
        return { status: "FINAL" as const, reviewId: review.id, outcome: input.decision };
      });
    } catch (e) {
      this.mapReviewError(e);
    }
  }

  /**
   * Second decision on a high-risk review. The business is approved only if BOTH reviewers
   * approve; otherwise the more cautious outcome applies.
   */
  async secondReview(staff: Viewer, reviewId: string, input: { decision: "APPROVE" | "REJECT" | "REQUEST_CHANGES"; reason: string }, meta: Meta) {
    const review = await this.prisma.client.reviewRecord.findUnique({ where: { id: reviewId } });
    if (!review || !["BUSINESS", "IDENTITY_MANUAL"].includes(review.targetType) || review.status !== "PENDING_SECOND_REVIEW") {
      throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "No review is waiting for a second decision.");
    }
    if (review.reviewerId === staff.userId) {
      throw new ApiError(HttpStatus.FORBIDDEN, "same_reviewer", "A different reviewer must make the second decision.");
    }
    await this.assertNotOwnAccount(staff, review.sellerProfileId);
    const now = new Date();
    try {
      return await this.prisma.client.$transaction(async (tx) => {
        await tx.reviewRecord.update({
          where: { id: review.id },
          data: { secondReviewerId: staff.userId, secondDecision: input.decision, secondReason: input.reason, secondReviewedAt: now, status: "FINAL", finalizedAt: now },
        });
        await this.audit.record(
          { action: "review.second_decision", actorType: "STAFF", actorUserId: staff.userId, targetType: "review_record", targetId: review.id, ...meta, metadata: { decision: input.decision } },
          tx,
        );
        const first = review.decision as "APPROVE" | "REJECT" | "REQUEST_CHANGES";
        const outcome = first === "APPROVE" && input.decision === "APPROVE" ? "APPROVE" : input.decision !== "APPROVE" ? input.decision : first;
        if (review.targetType === "IDENTITY_MANUAL") {
          const idOutcome = outcome === "APPROVE" ? "APPROVE" : "REJECT";
          await this.identity.finalize(tx, review.targetId, review.id, idOutcome, outcome === input.decision ? input.reason : review.reason, staff.userId);
          return { status: "FINAL" as const, reviewId: review.id, outcome: idOutcome };
        }
        if (outcome === "APPROVE") await this.finalizeApproval(tx, review.targetId, review.id, staff.userId);
        else await this.closeWithoutApproval(tx, review.targetId, outcome, outcome === input.decision ? input.reason : review.reason, staff.userId);
        return { status: "FINAL" as const, reviewId: review.id, outcome };
      });
    } catch (e) {
      this.mapReviewError(e);
    }
  }

  private async finalizeApproval(tx: Tx, businessId: string, reviewId: string, staffUserId: string) {
    const business = await tx.business.findUniqueOrThrow({ where: { id: businessId } });
    const now = new Date();
    // Re-verification supersedes any live business badge.
    const live = await tx.sellerBadge.findMany({ where: { sellerProfileId: business.sellerProfileId, level: "BUSINESS_DOCUMENTS", status: { in: ["ACTIVE", "PENDING", "SUSPENDED"] } } });
    for (const b of live) {
      await tx.sellerBadge.update({ where: { id: b.id }, data: { status: "REVOKED", revokedAt: now, revokedById: staffUserId, revocationReason: "superseded by a new business review" } });
    }
    await tx.business.update({ where: { id: business.id }, data: { status: "VERIFIED", decidedAt: now, decisionReason: null } });
    const expiresAt = badgeExpiry("BUSINESS_DOCUMENTS", now);
    const badge = await tx.sellerBadge.create({
      data: { sellerProfileId: business.sellerProfileId, level: "BUSINESS_DOCUMENTS", method: "MANUAL_DOCUMENT_REVIEW", status: "ACTIVE", reviewRecordId: reviewId, issuedAt: now, expiresAt },
    });
    await tx.businessDocument.updateMany({ where: { businessId: business.id, purgedAt: null }, data: { retainUntil: new Date(expiresAt.getTime() + RETAIN_AFTER_BADGE_MS) } });
    await tx.verificationEvent.create({
      data: { sellerProfileId: business.sellerProfileId, badgeId: badge.id, level: "BUSINESS_DOCUMENTS", type: "ISSUED", actorType: "STAFF", actorUserId: staffUserId },
    });
    await this.notify(tx, business.sellerProfileId, "business.verified", business.id);
  }

  private async closeWithoutApproval(tx: Tx, businessId: string, decision: "REJECT" | "REQUEST_CHANGES", reason: string, staffUserId: string) {
    const business = await tx.business.findUniqueOrThrow({ where: { id: businessId } });
    const now = new Date();
    if (decision === "REJECT") {
      await tx.business.update({ where: { id: business.id }, data: { status: "REJECTED", decidedAt: now, decisionReason: reason } });
      await tx.businessDocument.updateMany({ where: { businessId: business.id, purgedAt: null }, data: { retainUntil: new Date(now.getTime() + RETAIN_REJECTED_MS) } });
    } else {
      await tx.business.update({ where: { id: business.id }, data: { status: "DRAFT", decisionReason: reason } });
    }
    await tx.verificationEvent.create({
      data: { sellerProfileId: business.sellerProfileId, level: "BUSINESS_DOCUMENTS", type: "REJECTED", actorType: "STAFF", actorUserId: staffUserId, reason: decision === "REJECT" ? "rejected" : "changes requested" },
    });
    await this.notify(tx, business.sellerProfileId, decision === "REJECT" ? "business.rejected" : "business.changes_requested", business.id);
  }

  private async notify(tx: Tx, sellerProfileId: string, templateKey: string, businessId: string) {
    const seller = await tx.sellerProfile.findUniqueOrThrow({ where: { id: sellerProfileId }, select: { userId: true, user: { select: { preferredLocale: true } } } });
    await tx.notification.create({ data: { userId: seller.userId, channel: "IN_APP", templateKey, locale: seller.user.preferredLocale, payload: { businessId } } });
  }

  /** Re-scan a document whose scan failed (scanner outage). Infected files are deleted at once. */
  async rescan(staff: Viewer, documentId: string, meta: Meta) {
    const doc = await this.prisma.client.businessDocument.findUnique({ where: { id: documentId } });
    if (!doc || doc.purgedAt) throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "Document not found.");
    if (doc.scanStatus === "CLEAN") return { scanStatus: "CLEAN" as const };
    const data = await this.store.get(doc.storageKey);
    const result = await this.scanner.scan(data);
    const now = new Date();
    if (result.status === "INFECTED") await this.store.delete(doc.storageKey);
    await this.prisma.client.$transaction(async (tx) => {
      await tx.businessDocument.update({
        where: { id: doc.id },
        data: { scanStatus: result.status, scanEngine: result.engine, scannedAt: now, ...(result.status === "INFECTED" ? { purgedAt: now } : {}) },
      });
      await this.audit.record(
        { action: result.status === "INFECTED" ? "document.malware_detected" : "document.rescanned", actorType: "STAFF", actorUserId: staff.userId, targetType: "business_document", targetId: doc.id, ...meta, metadata: { scanStatus: result.status } },
        tx,
      );
    });
    return { scanStatus: result.status };
  }

  private mapReviewError(e: unknown): never {
    if ((e as { code?: string })?.code === "P2002") {
      throw new ApiError(HttpStatus.CONFLICT, "duplicate_business", "Another verified business already uses this TIN or registration number. Investigate before approving.");
    }
    throw e;
  }
}
