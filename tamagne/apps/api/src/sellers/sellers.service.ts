import { HttpStatus, Inject, Injectable } from "@nestjs/common";
import { badgeExpiry, buildPublicVerificationSummary, generateTamagneId, type BadgeInput } from "@tamagne/core";
import { ApiError, mapDbError } from "../common/http.js";
import { APP_CONFIG } from "../config/config.module.js";
import type { AppConfig } from "../config/env.js";
import { AuditService } from "../audit/audit.service.js";
import { PrismaService } from "../database/prisma.service.js";
import type { RequestMeta } from "../auth/auth.service.js";

export interface SellerInput {
  displayName: string;
  displayNameAm?: string | null;
  sellerType: "INDIVIDUAL" | "SOCIAL_MEDIA_SELLER" | "TELEGRAM_SHOP" | "REGISTERED_BUSINESS" | "ECOMMERCE_COMPANY" | "ONLINE_SERVICE_PROVIDER";
  region?: string | null;
  city?: string | null;
}

@Injectable()
export class SellersService {
  constructor(
    @Inject(PrismaService) private readonly prisma: PrismaService,
    @Inject(AuditService) private readonly audit: AuditService,
    @Inject(APP_CONFIG) private readonly config: AppConfig,
  ) {}

  /**
   * Creates the caller's seller profile and issues the Level 1 (phone) badge,
   * evidenced by the OTP challenge that verified the account's phone number.
   */
  async create(userId: string, input: SellerInput, meta: RequestMeta) {
    const db = this.prisma.client;
    const user = await db.user.findUniqueOrThrow({ where: { id: userId }, select: { phoneE164: true, phoneVerifiedAt: true, sellerProfile: { select: { id: true } } } });
    if (user.sellerProfile) throw new ApiError(HttpStatus.CONFLICT, "seller_exists", "You already have a seller profile.");
    if (!user.phoneE164 || !user.phoneVerifiedAt) {
      throw new ApiError(HttpStatus.CONFLICT, "phone_unverified", "Verify your phone number first.");
    }
    const otp = await db.phoneOtpChallenge.findFirst({
      where: { phoneE164: user.phoneE164, consumedAt: { not: null } },
      orderBy: { consumedAt: "desc" },
      select: { id: true },
    });
    if (!otp) throw new ApiError(HttpStatus.CONFLICT, "phone_unverified", "Verify your phone number first.");

    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        return await db.$transaction(async (tx) => {
          const seller = await tx.sellerProfile.create({
            data: {
              userId,
              tamagneId: generateTamagneId(),
              displayName: input.displayName,
              displayNameAm: input.displayNameAm ?? null,
              sellerType: input.sellerType,
              region: input.region ?? null,
              city: input.city ?? null,
            },
          });
          const issuedAt = new Date();
          const badge = await tx.sellerBadge.create({
            data: {
              sellerProfileId: seller.id,
              level: "PHONE",
              method: "PHONE_OTP",
              status: "ACTIVE",
              phoneOtpChallengeId: otp.id,
              issuedAt,
              expiresAt: badgeExpiry("PHONE", issuedAt),
            },
          });
          await tx.verificationEvent.create({
            data: { sellerProfileId: seller.id, badgeId: badge.id, level: "PHONE", type: "ISSUED", actorType: "SYSTEM" },
          });
          await this.audit.record(
            { action: "seller.created", actorType: "USER", actorUserId: userId, targetType: "seller_profile", targetId: seller.id, ...meta, metadata: { sellerType: input.sellerType } },
            tx,
          );
          return seller.id;
        });
      } catch (e) {
        const target = String((e as { meta?: { target?: unknown } })?.meta?.target ?? "");
        const msg = String((e as Error)?.message ?? "");
        // Retry only on a Tamagne ID collision (50-bit random; vanishingly rare).
        if ((e as { code?: string })?.code === "P2002" && (target.includes("tamagneId") || msg.includes("tamagneId"))) continue;
        if ((e as { code?: string })?.code === "P2002") throw new ApiError(HttpStatus.CONFLICT, "seller_exists", "You already have a seller profile.");
        mapDbError(e);
      }
    }
    throw new ApiError(HttpStatus.SERVICE_UNAVAILABLE, "unavailable", "Please try again.");
  }

  async getForUser(userId: string) {
    const seller = await this.prisma.client.sellerProfile.findUnique({
      where: { userId },
      include: {
        badges: {
          orderBy: { createdAt: "desc" },
          include: { identityVerification: { select: { environment: true } } },
        },
      },
    });
    if (!seller) throw new ApiError(HttpStatus.NOT_FOUND, "seller_not_found", "You have not created a seller profile yet.");

    const badgeInputs: BadgeInput[] = seller.badges.map((b) => ({
      level: b.level,
      status: b.status,
      method: b.method,
      issuedAt: b.issuedAt,
      expiresAt: b.expiresAt,
      identityEnvironment: b.identityVerification?.environment ?? null,
    }));
    return {
      id: seller.id,
      tamagneId: seller.tamagneId,
      displayName: seller.displayName,
      displayNameAm: seller.displayNameAm,
      sellerType: seller.sellerType,
      accountStatus: seller.accountStatus,
      isPublic: seller.isPublic,
      region: seller.region,
      city: seller.city,
      createdAt: seller.createdAt.toISOString(),
      verification: buildPublicVerificationSummary(seller.accountStatus, badgeInputs, { production: this.config.isProduction }),
      badges: seller.badges.map((b) => ({
        id: b.id,
        level: b.level,
        status: b.status,
        method: b.method,
        issuedAt: b.issuedAt?.toISOString() ?? null,
        expiresAt: b.expiresAt?.toISOString() ?? null,
        revokedAt: b.revokedAt?.toISOString() ?? null,
      })),
    };
  }

  async update(userId: string, input: Partial<SellerInput>, meta: RequestMeta) {
    const db = this.prisma.client;
    const seller = await db.sellerProfile.findUnique({ where: { userId }, select: { id: true, accountStatus: true } });
    if (!seller) throw new ApiError(HttpStatus.NOT_FOUND, "seller_not_found", "You have not created a seller profile yet.");
    if (seller.accountStatus === "SUSPENDED" || seller.accountStatus === "DEACTIVATED") {
      throw new ApiError(HttpStatus.FORBIDDEN, "seller_suspended", "Your seller account is suspended. You can submit an appeal.");
    }
    const changed = Object.keys(input).filter((k) => input[k as keyof SellerInput] !== undefined);
    if (changed.length === 0) return this.getForUser(userId);
    await db.$transaction(async (tx) => {
      await tx.sellerProfile.update({ where: { id: seller.id }, data: input });
      await this.audit.record(
        { action: "seller.updated", actorType: "USER", actorUserId: userId, targetType: "seller_profile", targetId: seller.id, ...meta, metadata: { changedFields: changed } },
        tx,
      );
    });
    return this.getForUser(userId);
  }
}
