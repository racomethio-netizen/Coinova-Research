import { createHash, randomUUID } from "node:crypto";
import { HttpStatus, Inject, Injectable } from "@nestjs/common";
import { badgeExpiry, checkDocument, type DocumentRejection } from "@tamagne/core";
import type { Prisma } from "@tamagne/db";
import { ApiError } from "../common/http.js";
import { AuditService } from "../audit/audit.service.js";
import type { Viewer } from "../auth/session.service.js";
import { PrismaService } from "../database/prisma.service.js";
import { MALWARE_SCANNER, type MalwareScanner } from "../scanning/scanner.js";
import { OBJECT_STORE, type EncryptedObjectStore } from "../storage/storage.js";

type Tx = Prisma.TransactionClient;
export type IdentityDocumentType = "FAYDA_ID_CARD" | "KEBELE_ID" | "PASSPORT" | "DRIVING_LICENSE";

interface Meta {
  requestId: string;
  ipHash?: string;
}

/** ID images are sensitive: keep them only briefly after a decision (proposed; legal review pending). */
const RETAIN_AFTER_APPROVAL_MS = 30 * 86_400_000;
const RETAIN_AFTER_REJECTION_MS = 90 * 86_400_000;

const MESSAGES: Record<DocumentRejection, string> = {
  empty: "The file is empty.",
  too_large: "The file is larger than 10 MB.",
  unsupported_type: "Upload a photo (JPEG or PNG) or a PDF of your ID.",
  type_mismatch: "The file's contents do not match its type.",
  active_content: "PDFs with scripts or embedded files are not accepted.",
};

/**
 * Interim MANUAL identity review (business rule 6). A reviewer looks at the seller's ID
 * document. The result is a separate IDENTITY_MANUAL_REVIEW badge and is never shown as
 * Fayda verification. No identity numbers are recorded.
 */
@Injectable()
export class IdentityReviewService {
  constructor(
    @Inject(PrismaService) private readonly prisma: PrismaService,
    @Inject(AuditService) private readonly audit: AuditService,
    @Inject(OBJECT_STORE) private readonly store: EncryptedObjectStore,
    @Inject(MALWARE_SCANNER) private readonly scanner: MalwareScanner,
  ) {}

  private async seller(userId: string) {
    const s = await this.prisma.client.sellerProfile.findUnique({ where: { userId }, select: { id: true, accountStatus: true } });
    if (!s) throw new ApiError(HttpStatus.NOT_FOUND, "seller_not_found", "Create your seller profile first.");
    return s;
  }

  async get(userId: string) {
    const s = await this.seller(userId);
    const latest = await this.prisma.client.identityReviewRequest.findFirst({ where: { sellerProfileId: s.id }, orderBy: { createdAt: "desc" } });
    return latest
      ? {
          id: latest.id,
          documentType: latest.documentType,
          status: latest.status,
          scanStatus: latest.scanStatus,
          decisionReason: latest.decisionReason,
          submittedAt: latest.submittedAt.toISOString(),
          decidedAt: latest.decidedAt?.toISOString() ?? null,
        }
      : null;
  }

  async submit(userId: string, documentType: IdentityDocumentType, file: { buffer: Buffer; mimetype: string }, meta: Meta) {
    const s = await this.seller(userId);
    if (s.accountStatus !== "ACTIVE") throw new ApiError(HttpStatus.FORBIDDEN, "seller_suspended", "Your seller account is not active.");
    const db = this.prisma.client;
    const consent = await db.consentRecord.findFirst({ where: { userId, purpose: "IDENTITY_DOCUMENT_REVIEW", status: "GRANTED" } });
    if (!consent) throw new ApiError(HttpStatus.CONFLICT, "consent_required", "Agree to the identity document review first.");
    const open = await db.identityReviewRequest.findFirst({ where: { sellerProfileId: s.id, status: "SUBMITTED", purgedAt: null } });
    if (open) throw new ApiError(HttpStatus.CONFLICT, "review_pending", "Your identity document is already waiting for review.");
    const now = new Date();
    const live = await db.sellerBadge.findFirst({
      where: { sellerProfileId: s.id, level: { in: ["IDENTITY_MANUAL_REVIEW", "FAYDA_IDENTITY"] }, status: "ACTIVE", expiresAt: { gt: now } },
    });
    if (live) throw new ApiError(HttpStatus.CONFLICT, "already_verified", "Your identity is already verified.");

    const check = checkDocument(file.buffer, file.mimetype);
    if (!check.ok) throw new ApiError(HttpStatus.UNPROCESSABLE_ENTITY, `document_${check.reason}`, MESSAGES[check.reason]);
    const id = randomUUID();
    const storageKey = `identity-documents/${s.id}/${id}`;
    const sha256 = createHash("sha256").update(file.buffer).digest("hex");
    const scan = await this.scanner.scan(file.buffer);
    if (scan.status === "INFECTED") {
      await this.audit.recordSafe({ action: "document.malware_detected", actorType: "USER", actorUserId: userId, targetType: "identity_review_request", targetId: id, ...meta, metadata: { engine: scan.engine, signature: scan.signature ?? null } });
      throw new ApiError(HttpStatus.UNPROCESSABLE_ENTITY, "document_malware_detected", "This file was flagged by our malware scanner and was not stored.");
    }
    if (scan.status !== "CLEAN") {
      // Unlike business documents, ID images are not kept unless they can be reviewed now.
      throw new ApiError(HttpStatus.SERVICE_UNAVAILABLE, "scanner_unavailable", "We can't check files right now. Please try again later.");
    }
    await this.store.put(storageKey, file.buffer);
    try {
      await db.$transaction(async (tx) => {
        await tx.identityReviewRequest.create({
          data: { id, sellerProfileId: s.id, uploadedById: userId, documentType, storageKey, mimeType: check.mime, sizeBytes: file.buffer.length, sha256, scanStatus: "CLEAN", scanEngine: scan.engine, scannedAt: now },
        });
        await tx.verificationEvent.create({ data: { sellerProfileId: s.id, level: "IDENTITY_MANUAL_REVIEW", type: "SUBMITTED", actorType: "USER", actorUserId: userId } });
        await this.audit.record({ action: "identity_review.submitted", actorType: "USER", actorUserId: userId, targetType: "identity_review_request", targetId: id, ...meta, metadata: { documentType } }, tx);
      });
    } catch (e) {
      await this.store.delete(storageKey).catch(() => undefined);
      if ((e as { code?: string })?.code === "P2002") throw new ApiError(HttpStatus.CONFLICT, "review_pending", "Your identity document is already waiting for review.");
      throw e;
    }
    return this.get(userId);
  }

  /** The seller withdraws a pending request; the image is deleted immediately. */
  async withdraw(userId: string, meta: Meta) {
    const s = await this.seller(userId);
    const open = await this.prisma.client.identityReviewRequest.findFirst({ where: { sellerProfileId: s.id, status: "SUBMITTED", purgedAt: null } });
    if (!open) throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "No pending request.");
    await this.store.delete(open.storageKey);
    await this.prisma.client.$transaction(async (tx) => {
      await tx.identityReviewRequest.update({ where: { id: open.id }, data: { status: "WITHDRAWN", purgedAt: new Date() } });
      await this.audit.record({ action: "identity_review.withdrawn", actorType: "USER", actorUserId: userId, targetType: "identity_review_request", targetId: open.id, ...meta }, tx);
    });
    return this.get(userId);
  }

  // ───────────── Reviewer side ─────────────

  async queue() {
    const rows = await this.prisma.client.identityReviewRequest.findMany({
      where: { status: "SUBMITTED", purgedAt: null },
      orderBy: { submittedAt: "asc" },
      take: 100,
      include: { sellerProfile: { select: { tamagneId: true, displayName: true } } },
    });
    const pending = await this.prisma.client.reviewRecord.findMany({ where: { targetType: "IDENTITY_MANUAL", status: "PENDING_SECOND_REVIEW" }, select: { id: true, targetId: true } });
    return rows.map((r) => ({
      id: r.id,
      documentType: r.documentType,
      submittedAt: r.submittedAt.toISOString(),
      seller: r.sellerProfile,
      secondReviewId: pending.find((p) => p.targetId === r.id)?.id ?? null,
    }));
  }

  async file(staff: Viewer, id: string, meta: Meta) {
    const r = await this.prisma.client.identityReviewRequest.findUnique({ where: { id } });
    if (!r || r.purgedAt) throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "Not found.");
    if (r.scanStatus !== "CLEAN") throw new ApiError(HttpStatus.CONFLICT, "document_not_scanned", "This document has not passed the malware scan.");
    const data = await this.store.get(r.storageKey);
    await this.audit.record({ action: "identity_document.viewed", actorType: "STAFF", actorUserId: staff.userId, targetType: "identity_review_request", targetId: r.id, ...meta });
    return { data, mimeType: r.mimeType };
  }

  async decide(staff: Viewer, id: string, input: { decision: "APPROVE" | "REJECT"; reason: string; riskLevel: "LOW" | "MEDIUM" | "HIGH" }, meta: Meta) {
    const db = this.prisma.client;
    const r = await db.identityReviewRequest.findUnique({ where: { id }, include: { sellerProfile: { select: { userId: true } } } });
    if (!r || r.purgedAt) throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "Not found.");
    if (r.status !== "SUBMITTED") throw new ApiError(HttpStatus.CONFLICT, "not_pending", "This request is not awaiting review.");
    if (r.sellerProfile.userId === staff.userId) throw new ApiError(HttpStatus.FORBIDDEN, "own_account", "You cannot review your own seller account.");
    const pending = await db.reviewRecord.findFirst({ where: { targetType: "IDENTITY_MANUAL", targetId: r.id, status: "PENDING_SECOND_REVIEW" } });
    if (pending) throw new ApiError(HttpStatus.CONFLICT, "awaiting_second_review", "A decision is already waiting for a second reviewer.");
    const highRisk = input.riskLevel === "HIGH";
    return db.$transaction(async (tx) => {
      const review = await tx.reviewRecord.create({
        data: {
          sellerProfileId: r.sellerProfileId,
          targetType: "IDENTITY_MANUAL",
          targetId: r.id,
          reviewerId: staff.userId,
          decision: input.decision,
          reason: input.reason,
          riskLevel: input.riskLevel,
          requiresSecondReview: highRisk,
          status: highRisk ? "PENDING_SECOND_REVIEW" : "FINAL",
          finalizedAt: highRisk ? null : new Date(),
        },
      });
      await this.audit.record(
        { action: "review.identity_decided", actorType: "STAFF", actorUserId: staff.userId, targetType: "identity_review_request", targetId: r.id, ...meta, metadata: { decision: input.decision, riskLevel: input.riskLevel, reviewId: review.id } },
        tx,
      );
      if (highRisk) return { status: "PENDING_SECOND_REVIEW" as const, reviewId: review.id, outcome: null };
      await this.finalize(tx, r.id, review.id, input.decision, input.reason, staff.userId);
      return { status: "FINAL" as const, reviewId: review.id, outcome: input.decision };
    });
  }

  /** Applies a final outcome (also called by the second-review flow). */
  async finalize(tx: Tx, requestId: string, reviewId: string, outcome: "APPROVE" | "REJECT", reason: string, staffUserId: string) {
    const r = await tx.identityReviewRequest.findUniqueOrThrow({ where: { id: requestId }, include: { sellerProfile: { select: { userId: true, user: { select: { preferredLocale: true } } } } } });
    const now = new Date();
    if (outcome === "APPROVE") {
      await tx.identityReviewRequest.update({ where: { id: r.id }, data: { status: "APPROVED", decidedAt: now, retainUntil: new Date(now.getTime() + RETAIN_AFTER_APPROVAL_MS) } });
      const live = await tx.sellerBadge.findMany({ where: { sellerProfileId: r.sellerProfileId, level: "IDENTITY_MANUAL_REVIEW", status: { in: ["ACTIVE", "PENDING", "SUSPENDED"] } } });
      for (const b of live) {
        await tx.sellerBadge.update({ where: { id: b.id }, data: { status: "REVOKED", revokedAt: now, revokedById: staffUserId, revocationReason: "superseded by a new identity review" } });
      }
      const badge = await tx.sellerBadge.create({
        data: { sellerProfileId: r.sellerProfileId, level: "IDENTITY_MANUAL_REVIEW", method: "MANUAL_IDENTITY_REVIEW", status: "ACTIVE", reviewRecordId: reviewId, issuedAt: now, expiresAt: badgeExpiry("IDENTITY_MANUAL_REVIEW", now) },
      });
      await tx.verificationEvent.create({ data: { sellerProfileId: r.sellerProfileId, badgeId: badge.id, level: "IDENTITY_MANUAL_REVIEW", type: "ISSUED", actorType: "STAFF", actorUserId: staffUserId } });
    } else {
      await tx.identityReviewRequest.update({ where: { id: r.id }, data: { status: "REJECTED", decidedAt: now, decisionReason: reason, retainUntil: new Date(now.getTime() + RETAIN_AFTER_REJECTION_MS) } });
      await tx.verificationEvent.create({ data: { sellerProfileId: r.sellerProfileId, level: "IDENTITY_MANUAL_REVIEW", type: "REJECTED", actorType: "STAFF", actorUserId: staffUserId } });
    }
    await tx.notification.create({
      data: { userId: r.sellerProfile.userId, channel: "IN_APP", templateKey: outcome === "APPROVE" ? "identity.manual_approved" : "identity.manual_rejected", locale: r.sellerProfile.user.preferredLocale, payload: { requestId: r.id } },
    });
  }
}
