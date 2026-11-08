import { randomUUID } from "node:crypto";
import { HttpStatus, Inject, Injectable } from "@nestjs/common";
import { isQrToken, normalizeTamagneId } from "@tamagne/core";
import { ApiError } from "../common/http.js";
import { AuditService } from "../audit/audit.service.js";
import type { Viewer } from "../auth/session.service.js";
import { PrismaService } from "../database/prisma.service.js";
import { CryptoService } from "../security/crypto.service.js";

export type FraudCategory = "NON_DELIVERY" | "COUNTERFEIT_PRODUCT" | "IMPERSONATION" | "PAYMENT_FRAUD" | "MISLEADING_INFORMATION" | "OTHER";
export type FraudStatus = "SUBMITTED" | "TRIAGED" | "INVESTIGATING" | "ACTION_TAKEN" | "DISMISSED";

interface Meta {
  requestId: string;
  ipHash: string;
}

const NEXT: Record<FraudStatus, FraudStatus[]> = {
  SUBMITTED: ["TRIAGED", "INVESTIGATING", "DISMISSED"],
  TRIAGED: ["INVESTIGATING", "DISMISSED", "ACTION_TAKEN"],
  INVESTIGATING: ["ACTION_TAKEN", "DISMISSED"],
  ACTION_TAKEN: [],
  DISMISSED: [],
};

@Injectable()
export class ReportsService {
  constructor(
    @Inject(PrismaService) private readonly prisma: PrismaService,
    @Inject(AuditService) private readonly audit: AuditService,
    @Inject(CryptoService) private readonly crypto: CryptoService,
  ) {}

  /**
   * Public fraud report. The response is the same whether or not the seller exists or is
   * public, so reports cannot be used to discover sellers. Contact details are optional
   * and encrypted; the reporter's IP is kept only as a keyed hash for abuse control.
   */
  async submit(input: { seller: string; category: FraudCategory; description: string; contact?: string | null }, viewer: Viewer | null, meta: Meta) {
    const raw = input.seller.trim();
    let sellerProfileId: string | null = null;
    if (isQrToken(raw)) {
      sellerProfileId = (await this.prisma.client.qrToken.findUnique({ where: { token: raw }, select: { sellerProfileId: true } }))?.sellerProfileId ?? null;
    } else {
      const id = normalizeTamagneId(raw);
      if (id) sellerProfileId = (await this.prisma.client.sellerProfile.findUnique({ where: { tamagneId: id }, select: { id: true } }))?.id ?? null;
    }
    if (sellerProfileId) {
      const reportId = randomUUID();
      await this.prisma.client.fraudReport.create({
        data: {
          id: reportId,
          sellerProfileId,
          reporterUserId: viewer?.userId ?? null,
          reporterContactEnc: input.contact ? new Uint8Array(this.crypto.cipher.encrypt(input.contact, `fraud_reports.reporterContactEnc:${reportId}`)) : null,
          reporterIpHash: meta.ipHash,
          category: input.category,
          description: input.description,
        },
      });
      await this.audit.record({ action: "report.submitted", actorType: viewer ? "USER" : "SYSTEM", actorUserId: viewer?.userId ?? null, targetType: "fraud_report", targetId: reportId, ...meta, metadata: { category: input.category } });
    }
    return { received: true };
  }

  async list(status?: FraudStatus) {
    const rows = await this.prisma.client.fraudReport.findMany({
      where: status ? { status } : { status: { in: ["SUBMITTED", "TRIAGED", "INVESTIGATING"] } },
      orderBy: { createdAt: "asc" },
      take: 200,
      include: { sellerProfile: { select: { tamagneId: true, displayName: true, accountStatus: true } } },
    });
    const counts = await this.prisma.client.fraudReport.groupBy({ by: ["sellerProfileId"], _count: { _all: true }, where: { sellerProfileId: { in: [...new Set(rows.map((r) => r.sellerProfileId))] } } });
    return rows.map((r) => ({
      id: r.id,
      category: r.category,
      status: r.status,
      createdAt: r.createdAt.toISOString(),
      assignedToId: r.assignedToId,
      seller: r.sellerProfile,
      reportsAgainstSeller: counts.find((c) => c.sellerProfileId === r.sellerProfileId)?._count._all ?? 1,
      excerpt: r.description.slice(0, 160),
    }));
  }

  async detail(staff: Viewer, id: string, meta: Meta) {
    const r = await this.prisma.client.fraudReport.findUnique({ where: { id }, include: { sellerProfile: { select: { tamagneId: true, displayName: true, accountStatus: true } } } });
    if (!r) throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "Report not found.");
    let contact: string | null = null;
    if (r.reporterContactEnc) {
      contact = this.crypto.cipher.decrypt(Buffer.from(r.reporterContactEnc), `fraud_reports.reporterContactEnc:${r.id}`);
      await this.audit.record({ action: "report.contact_viewed", actorType: "STAFF", actorUserId: staff.userId, targetType: "fraud_report", targetId: r.id, ...meta });
    }
    return {
      id: r.id,
      category: r.category,
      status: r.status,
      description: r.description,
      reporterContact: contact,
      reporterSignedIn: r.reporterUserId !== null,
      assignedToId: r.assignedToId,
      resolutionNote: r.resolutionNote,
      createdAt: r.createdAt.toISOString(),
      resolvedAt: r.resolvedAt?.toISOString() ?? null,
      seller: r.sellerProfile,
    };
  }

  async update(staff: Viewer, id: string, input: { status: FraudStatus; note?: string; assignToMe?: boolean }, meta: Meta) {
    const r = await this.prisma.client.fraudReport.findUnique({ where: { id } });
    if (!r) throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "Report not found.");
    if (input.status !== r.status && !NEXT[r.status].includes(input.status)) {
      throw new ApiError(HttpStatus.CONFLICT, "invalid_transition", `A ${r.status.toLowerCase()} report cannot move to ${input.status.toLowerCase()}.`);
    }
    const closing = input.status === "ACTION_TAKEN" || input.status === "DISMISSED";
    if (closing && !input.note) throw new ApiError(HttpStatus.UNPROCESSABLE_ENTITY, "note_required", "Add a resolution note.");
    await this.prisma.client.$transaction(async (tx) => {
      await tx.fraudReport.update({
        where: { id },
        data: {
          status: input.status,
          ...(input.assignToMe ? { assignedToId: staff.userId } : {}),
          ...(closing ? { resolutionNote: input.note, resolvedAt: new Date() } : {}),
        },
      });
      await this.audit.record({ action: "report.updated", actorType: "STAFF", actorUserId: staff.userId, targetType: "fraud_report", targetId: id, ...meta, metadata: { from: r.status, to: input.status } }, tx);
    });
    return this.detail(staff, id, meta);
  }
}
