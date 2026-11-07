import { Inject, Injectable } from "@nestjs/common";
import { buildPublicVerificationSummary, isQrToken, normalizeTamagneId, type PublicVerificationSummary } from "@tamagne/core";
import { APP_CONFIG } from "../config/config.module.js";
import type { AppConfig } from "../config/env.js";
import { PrismaService } from "../database/prisma.service.js";

/**
 * The ONLY fields the public lookup, QR page and (later) marketplace API may return.
 * Adding a field here is a privacy decision: update docs/ARCHITECTURE.md §6 and the
 * allow-list test in test/verify.test.ts.
 */
export interface PublicSellerView {
  tamagneId: string;
  displayName: string;
  displayNameAm: string | null;
  accountStatus: PublicVerificationSummary["accountStatus"];
  verification: Omit<PublicVerificationSummary, "accountStatus">;
  matchedBy: "tamagne_id" | "qr_token";
  checkedAt: string;
}

export type LookupResult = { kind: "found"; seller: PublicSellerView } | { kind: "qr_revoked" } | { kind: "not_found" };

@Injectable()
export class VerifyService {
  constructor(
    @Inject(PrismaService) private readonly prisma: PrismaService,
    @Inject(APP_CONFIG) private readonly config: AppConfig,
  ) {}

  /**
   * Resolves a Tamagne ID (typing errors normalized) or a QR token.
   * Unknown IDs and profiles without public-display consent are indistinguishable
   * ("not_found"). A revoked QR token is reported as such: tokens are 256-bit random,
   * so this reveals nothing enumerable, and it warns buyers about stale or misused codes.
   */
  async lookup(identifier: string, now = new Date()): Promise<LookupResult> {
    const raw = identifier.trim();
    let sellerProfileId: string | null = null;
    let matchedBy: PublicSellerView["matchedBy"];

    if (isQrToken(raw)) {
      const qr = await this.prisma.client.qrToken.findUnique({ where: { token: raw }, select: { sellerProfileId: true, status: true } });
      if (!qr) return { kind: "not_found" };
      if (qr.status !== "ACTIVE") return { kind: "qr_revoked" };
      sellerProfileId = qr.sellerProfileId;
      matchedBy = "qr_token";
    } else {
      const tamagneId = normalizeTamagneId(raw);
      if (!tamagneId) return { kind: "not_found" };
      const s = await this.prisma.client.sellerProfile.findUnique({ where: { tamagneId }, select: { id: true } });
      if (!s) return { kind: "not_found" };
      sellerProfileId = s.id;
      matchedBy = "tamagne_id";
    }

    const seller = await this.prisma.client.sellerProfile.findUnique({
      where: { id: sellerProfileId },
      select: {
        tamagneId: true,
        displayName: true,
        displayNameAm: true,
        accountStatus: true,
        isPublic: true,
        deletedAt: true,
        badges: {
          select: {
            level: true,
            status: true,
            method: true,
            issuedAt: true,
            expiresAt: true,
            identityVerification: { select: { environment: true } },
          },
        },
      },
    });
    if (!seller || !seller.isPublic || seller.deletedAt) return { kind: "not_found" };

    const { accountStatus, ...verification } = buildPublicVerificationSummary(
      seller.accountStatus,
      seller.badges.map((b) => ({ ...b, identityEnvironment: b.identityVerification?.environment ?? null })),
      { now, production: this.config.isProduction },
    );
    return {
      kind: "found",
      seller: {
        tamagneId: seller.tamagneId,
        displayName: seller.displayName,
        displayNameAm: seller.displayNameAm,
        accountStatus,
        verification,
        matchedBy,
        checkedAt: now.toISOString(),
      },
    };
  }
}
