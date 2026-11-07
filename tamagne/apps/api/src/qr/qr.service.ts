import { HttpStatus, Inject, Injectable } from "@nestjs/common";
import { generateQrToken } from "@tamagne/core";
import QRCode from "qrcode";
import { ApiError } from "../common/http.js";
import { APP_CONFIG } from "../config/config.module.js";
import type { AppConfig } from "../config/env.js";
import { AuditService } from "../audit/audit.service.js";
import { PrismaService } from "../database/prisma.service.js";

export interface QrView {
  url: string;
  svg: string;
  createdAt: string;
}

@Injectable()
export class QrService {
  constructor(
    @Inject(PrismaService) private readonly prisma: PrismaService,
    @Inject(AuditService) private readonly audit: AuditService,
    @Inject(APP_CONFIG) private readonly config: AppConfig,
  ) {}

  /** Language-neutral URL; the web app redirects to the visitor's language. */
  verifyUrl(token: string): string {
    return new URL(`/verify/${token}`, this.config.publicWebUrl).toString();
  }

  private async render(token: string, createdAt: Date): Promise<QrView> {
    const url = this.verifyUrl(token);
    // Error correction "M": survives smudges on printed stickers while staying scannable on small prints.
    const svg = await QRCode.toString(url, { type: "svg", errorCorrectionLevel: "M", margin: 2, color: { dark: "#071226", light: "#ffffff" } });
    return { url, svg, createdAt: createdAt.toISOString() };
  }

  /** Returns the seller's active QR, creating one if needed. Requires a public profile. */
  async getOrCreate(userId: string, meta: { requestId: string }): Promise<QrView> {
    const seller = await this.prisma.client.sellerProfile.findUnique({ where: { userId }, select: { id: true, isPublic: true, accountStatus: true } });
    if (!seller) throw new ApiError(HttpStatus.NOT_FOUND, "seller_not_found", "You have not created a seller profile yet.");
    if (!seller.isPublic) {
      throw new ApiError(HttpStatus.CONFLICT, "profile_not_public", "Make your profile public first; otherwise the QR code would show nothing to buyers.");
    }
    const existing = await this.prisma.client.qrToken.findFirst({ where: { sellerProfileId: seller.id, status: "ACTIVE" } });
    if (existing) return this.render(existing.token, existing.createdAt);
    try {
      const created = await this.prisma.client.$transaction(async (tx) => {
        const qr = await tx.qrToken.create({ data: { sellerProfileId: seller.id, token: generateQrToken() } });
        await this.audit.record({ action: "qr.issued", actorType: "USER", actorUserId: userId, targetType: "qr_token", targetId: qr.id, requestId: meta.requestId }, tx);
        return qr;
      });
      return this.render(created.token, created.createdAt);
    } catch (e) {
      // Concurrent first request created it (one active QR per seller is DB-enforced).
      if ((e as { code?: string })?.code === "P2002") {
        const qr = await this.prisma.client.qrToken.findFirstOrThrow({ where: { sellerProfileId: seller.id, status: "ACTIVE" } });
        return this.render(qr.token, qr.createdAt);
      }
      throw e;
    }
  }

  /** Revokes the current QR and issues a new one (e.g. a printed code was misused). */
  async rotate(userId: string, meta: { requestId: string }): Promise<QrView> {
    const seller = await this.prisma.client.sellerProfile.findUnique({ where: { userId }, select: { id: true, isPublic: true } });
    if (!seller) throw new ApiError(HttpStatus.NOT_FOUND, "seller_not_found", "You have not created a seller profile yet.");
    if (!seller.isPublic) throw new ApiError(HttpStatus.CONFLICT, "profile_not_public", "Make your profile public first.");
    const qr = await this.prisma.client.$transaction(async (tx) => {
      const now = new Date();
      const old = await tx.qrToken.findFirst({ where: { sellerProfileId: seller.id, status: "ACTIVE" } });
      if (old) {
        await tx.qrToken.update({ where: { id: old.id }, data: { status: "REVOKED", revokedAt: now, revokedById: userId, revokedReason: "rotated by seller" } });
      }
      const created = await tx.qrToken.create({ data: { sellerProfileId: seller.id, token: generateQrToken() } });
      await this.audit.record(
        { action: "qr.rotated", actorType: "USER", actorUserId: userId, targetType: "qr_token", targetId: created.id, requestId: meta.requestId, metadata: { revokedQrId: old?.id ?? null } },
        tx,
      );
      return created;
    });
    return this.render(qr.token, qr.createdAt);
  }

  /** Staff revocation (fraud, impersonation). The seller can issue a new code afterwards unless suspended. */
  async revokeByStaff(tamagneId: string, staffUserId: string, reason: string, meta: { requestId: string }): Promise<{ revoked: number }> {
    const seller = await this.prisma.client.sellerProfile.findUnique({ where: { tamagneId }, select: { id: true } });
    if (!seller) throw new ApiError(HttpStatus.NOT_FOUND, "seller_not_found", "Seller not found.");
    return this.prisma.client.$transaction(async (tx) => {
      const active = await tx.qrToken.findMany({ where: { sellerProfileId: seller.id, status: "ACTIVE" } });
      for (const q of active) {
        await tx.qrToken.update({ where: { id: q.id }, data: { status: "REVOKED", revokedAt: new Date(), revokedById: staffUserId, revokedReason: reason } });
        await this.audit.record({ action: "qr.revoked_by_staff", actorType: "STAFF", actorUserId: staffUserId, targetType: "qr_token", targetId: q.id, requestId: meta.requestId }, tx);
      }
      return { revoked: active.length };
    });
  }
}
