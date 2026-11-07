import { createHash, randomUUID } from "node:crypto";
import { HttpStatus, Inject, Injectable, Logger } from "@nestjs/common";
import { REQUIRED_DOCUMENT_TYPES, checkDocument, type BusinessDocumentType, type DocumentRejection } from "@tamagne/core";
import { ApiError, mapDbError } from "../common/http.js";
import { AuditService } from "../audit/audit.service.js";
import { PrismaService } from "../database/prisma.service.js";
import { MALWARE_SCANNER, type MalwareScanner } from "../scanning/scanner.js";
import { OBJECT_STORE, type EncryptedObjectStore } from "../storage/storage.js";

export interface BusinessInput {
  legalName: string;
  tradeName?: string | null;
  legalForm: "SOLE_PROPRIETORSHIP" | "PRIVATE_LIMITED_COMPANY" | "SHARE_COMPANY" | "PARTNERSHIP" | "COOPERATIVE" | "OTHER";
  registrationNumber?: string | null;
  tinNumber?: string | null;
  region?: string | null;
  city?: string | null;
}

export interface Meta {
  requestId: string;
  ipHash?: string;
}

const MAX_DOCUMENTS = 10;
const EDITABLE = new Set(["DRAFT", "REJECTED"]);

const REJECTION_MESSAGES: Record<DocumentRejection, string> = {
  empty: "The file is empty.",
  too_large: "The file is larger than 10 MB.",
  unsupported_type: "Upload a PDF, JPEG or PNG file.",
  type_mismatch: "The file's contents do not match its type.",
  active_content: "PDFs with scripts, launch actions or embedded files are not accepted. Print to a plain PDF and try again.",
};

@Injectable()
export class BusinessesService {
  private readonly logger = new Logger("businesses");
  constructor(
    @Inject(PrismaService) private readonly prisma: PrismaService,
    @Inject(AuditService) private readonly audit: AuditService,
    @Inject(OBJECT_STORE) private readonly store: EncryptedObjectStore,
    @Inject(MALWARE_SCANNER) private readonly scanner: MalwareScanner,
  ) {}

  private async sellerOf(userId: string) {
    const seller = await this.prisma.client.sellerProfile.findUnique({ where: { userId }, select: { id: true, accountStatus: true } });
    if (!seller) throw new ApiError(HttpStatus.NOT_FOUND, "seller_not_found", "Create your seller profile first.");
    return seller;
  }

  private async editableBusiness(userId: string) {
    const seller = await this.sellerOf(userId);
    if (seller.accountStatus !== "ACTIVE") throw new ApiError(HttpStatus.FORBIDDEN, "seller_suspended", "Your seller account is not active.");
    const business = await this.prisma.client.business.findUnique({ where: { sellerProfileId: seller.id } });
    if (!business) throw new ApiError(HttpStatus.NOT_FOUND, "business_not_found", "Add your business details first.");
    if (!EDITABLE.has(business.status)) {
      throw new ApiError(HttpStatus.CONFLICT, "business_locked", "This business is under review or already verified and cannot be changed.");
    }
    return { seller, business };
  }

  private async requireConsent(userId: string) {
    const consent = await this.prisma.client.consentRecord.findFirst({ where: { userId, purpose: "BUSINESS_DOCUMENT_PROCESSING", status: "GRANTED" } });
    if (!consent) {
      throw new ApiError(HttpStatus.CONFLICT, "consent_required", "Agree to the processing of your business documents first.");
    }
  }

  async get(userId: string) {
    const seller = await this.sellerOf(userId);
    const business = await this.prisma.client.business.findUnique({
      where: { sellerProfileId: seller.id },
      include: { documents: { where: { purgedAt: null }, orderBy: { createdAt: "asc" } } },
    });
    if (!business) return null;
    return {
      id: business.id,
      legalName: business.legalName,
      tradeName: business.tradeName,
      legalForm: business.legalForm,
      registrationNumber: business.registrationNumber,
      tinNumber: business.tinNumber,
      region: business.region,
      city: business.city,
      status: business.status,
      submittedAt: business.submittedAt?.toISOString() ?? null,
      decidedAt: business.decidedAt?.toISOString() ?? null,
      decisionReason: business.decisionReason,
      requiredDocumentTypes: REQUIRED_DOCUMENT_TYPES,
      documents: business.documents.map((d) => ({
        id: d.id,
        type: d.type,
        mimeType: d.mimeType,
        sizeBytes: d.sizeBytes,
        scanStatus: d.scanStatus,
        reviewStatus: d.reviewStatus,
        rejectionReason: d.rejectionReason,
        uploadedAt: d.createdAt.toISOString(),
      })),
    };
  }

  async create(userId: string, input: BusinessInput, meta: Meta) {
    const seller = await this.sellerOf(userId);
    if (seller.accountStatus !== "ACTIVE") throw new ApiError(HttpStatus.FORBIDDEN, "seller_suspended", "Your seller account is not active.");
    try {
      await this.prisma.client.$transaction(async (tx) => {
        const b = await tx.business.create({ data: { sellerProfileId: seller.id, ...input } });
        await this.audit.record({ action: "business.created", actorType: "USER", actorUserId: userId, targetType: "business", targetId: b.id, ...meta }, tx);
      });
    } catch (e) {
      mapDbError(e, "You have already added a business.");
    }
    return this.get(userId);
  }

  async update(userId: string, input: Partial<BusinessInput>, meta: Meta) {
    const { business } = await this.editableBusiness(userId);
    const changed = Object.keys(input).filter((k) => input[k as keyof BusinessInput] !== undefined);
    await this.prisma.client.$transaction(async (tx) => {
      // Editing a rejected business re-opens it as a draft.
      await tx.business.update({ where: { id: business.id }, data: { ...input, status: "DRAFT" } });
      await this.audit.record(
        { action: "business.updated", actorType: "USER", actorUserId: userId, targetType: "business", targetId: business.id, ...meta, metadata: { changedFields: changed } },
        tx,
      );
    });
    return this.get(userId);
  }

  /**
   * Upload pipeline: type sniffing and active-content check → malware scan → encrypt →
   * private storage → metadata row. Infected files are never stored.
   */
  async upload(userId: string, type: BusinessDocumentType, file: { buffer: Buffer; mimetype: string }, meta: Meta) {
    const { business } = await this.editableBusiness(userId);
    await this.requireConsent(userId);
    const count = await this.prisma.client.businessDocument.count({ where: { businessId: business.id, purgedAt: null } });
    if (count >= MAX_DOCUMENTS) throw new ApiError(HttpStatus.CONFLICT, "too_many_documents", `You can upload up to ${MAX_DOCUMENTS} documents.`);

    const check = checkDocument(file.buffer, file.mimetype);
    if (!check.ok) {
      throw new ApiError(HttpStatus.UNPROCESSABLE_ENTITY, `document_${check.reason}`, REJECTION_MESSAGES[check.reason]);
    }
    const id = randomUUID();
    const storageKey = `business-documents/${business.id}/${id}`;
    const sha256 = createHash("sha256").update(file.buffer).digest("hex");
    const scan = await this.scanner.scan(file.buffer);
    const now = new Date();

    if (scan.status === "INFECTED") {
      await this.prisma.client.businessDocument.create({
        data: { id, businessId: business.id, uploadedById: userId, type, storageKey, mimeType: check.mime, sizeBytes: file.buffer.length, sha256, scanStatus: "INFECTED", scanEngine: scan.engine, scannedAt: now, purgedAt: now },
      });
      await this.audit.recordSafe({
        action: "document.malware_detected",
        actorType: "USER",
        actorUserId: userId,
        targetType: "business_document",
        targetId: id,
        ...meta,
        metadata: { engine: scan.engine, signature: scan.signature ?? null },
      });
      throw new ApiError(HttpStatus.UNPROCESSABLE_ENTITY, "document_malware_detected", "This file was flagged by our malware scanner and was not stored.");
    }

    await this.store.put(storageKey, file.buffer);
    try {
      await this.prisma.client.$transaction(async (tx) => {
        await tx.businessDocument.create({
          data: {
            id,
            businessId: business.id,
            uploadedById: userId,
            type,
            storageKey,
            mimeType: check.mime,
            sizeBytes: file.buffer.length,
            sha256,
            scanStatus: scan.status,
            scanEngine: scan.engine,
            scannedAt: now,
          },
        });
        await this.audit.record(
          { action: "document.uploaded", actorType: "USER", actorUserId: userId, targetType: "business_document", targetId: id, ...meta, metadata: { type, scanStatus: scan.status } },
          tx,
        );
      });
    } catch (e) {
      await this.store.delete(storageKey).catch(() => this.logger.error(`orphaned object cleanup failed for ${id}`));
      throw e;
    }
    return this.get(userId);
  }

  async removeDocument(userId: string, documentId: string, meta: Meta) {
    const { business } = await this.editableBusiness(userId);
    const doc = await this.prisma.client.businessDocument.findFirst({ where: { id: documentId, businessId: business.id, purgedAt: null } });
    if (!doc) throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "Document not found.");
    await this.store.delete(doc.storageKey);
    await this.prisma.client.$transaction(async (tx) => {
      await tx.businessDocument.update({ where: { id: doc.id }, data: { purgedAt: new Date() } });
      await this.audit.record({ action: "document.removed", actorType: "USER", actorUserId: userId, targetType: "business_document", targetId: doc.id, ...meta }, tx);
    });
    return this.get(userId);
  }

  async submit(userId: string, meta: Meta) {
    const { seller, business } = await this.editableBusiness(userId);
    await this.requireConsent(userId);
    const docs = await this.prisma.client.businessDocument.findMany({ where: { businessId: business.id, purgedAt: null } });
    const missing = REQUIRED_DOCUMENT_TYPES.filter((t) => !docs.some((d) => d.type === t && d.scanStatus === "CLEAN" && d.reviewStatus !== "REJECTED"));
    if (missing.length) {
      throw new ApiError(HttpStatus.CONFLICT, "documents_missing", "Upload the required documents before submitting.", missing.map((t) => ({ path: "documents", message: t })));
    }
    if (docs.some((d) => d.scanStatus === "PENDING")) {
      throw new ApiError(HttpStatus.CONFLICT, "scan_pending", "Some documents are still being scanned. Try again shortly.");
    }
    await this.prisma.client.$transaction(async (tx) => {
      // Re-submission: anything previously flagged goes back to the reviewer.
      await tx.businessDocument.updateMany({ where: { businessId: business.id, purgedAt: null, reviewStatus: "NEEDS_RESUBMISSION" }, data: { reviewStatus: "PENDING", rejectionReason: null } });
      await tx.business.update({ where: { id: business.id }, data: { status: "SUBMITTED", submittedAt: new Date(), decidedAt: null, decisionReason: null } });
      await tx.verificationEvent.create({ data: { sellerProfileId: seller.id, level: "BUSINESS_DOCUMENTS", type: "SUBMITTED", actorType: "USER", actorUserId: userId } });
      await this.audit.record({ action: "business.submitted", actorType: "USER", actorUserId: userId, targetType: "business", targetId: business.id, ...meta }, tx);
    });
    return this.get(userId);
  }
}
