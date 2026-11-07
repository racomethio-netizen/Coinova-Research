import { Body, Controller, Delete, Get, HttpStatus, Inject, Param, Post, Req, Res } from "@nestjs/common";
import { ApiOperation, ApiTags } from "@nestjs/swagger";
import { POLICY_VERSIONS, WITHDRAWABLE_PURPOSES, type ConsentPurpose } from "@tamagne/core";
import type { Request, Response } from "express";
import { z } from "zod";
import { ApiError, clientIp, parse } from "../common/http.js";
import { AuditService } from "../audit/audit.service.js";
import { Authenticated, CurrentViewer } from "../auth/guards.js";
import type { Viewer } from "../auth/session.service.js";
import { PrismaService } from "../database/prisma.service.js";
import { CryptoService } from "../security/crypto.service.js";

const Purpose = z.enum(Object.keys(POLICY_VERSIONS) as [ConsentPurpose, ...ConsentPurpose[]]);
const GrantBody = z.strictObject({ purpose: Purpose });

/**
 * Consent records: purpose, policy version, timestamp and withdrawal. Grants and
 * withdrawals append/close records; they are never edited or deleted (DB-enforced).
 */
@ApiTags("consents")
@Controller({ path: "consents", version: "1" })
export class ConsentsController {
  constructor(
    @Inject(PrismaService) private readonly prisma: PrismaService,
    @Inject(AuditService) private readonly audit: AuditService,
    @Inject(CryptoService) private readonly crypto: CryptoService,
  ) {}

  @Get()
  @Authenticated()
  @ApiOperation({ summary: "Current consent state per purpose" })
  async list(@CurrentViewer() viewer: Viewer) {
    const records = await this.prisma.client.consentRecord.findMany({ where: { userId: viewer.userId }, orderBy: { grantedAt: "desc" } });
    const data = (Object.keys(POLICY_VERSIONS) as ConsentPurpose[]).map((purpose) => {
      const latest = records.find((r) => r.purpose === purpose);
      return {
        purpose,
        status: latest?.status ?? "NONE",
        policyVersion: latest?.policyVersion ?? null,
        currentPolicyVersion: POLICY_VERSIONS[purpose],
        grantedAt: latest?.grantedAt.toISOString() ?? null,
        withdrawnAt: latest?.withdrawnAt?.toISOString() ?? null,
        withdrawable: WITHDRAWABLE_PURPOSES.includes(purpose),
      };
    });
    return { data };
  }

  @Post()
  @Authenticated()
  @ApiOperation({ summary: "Grant consent for a purpose (current policy version)" })
  async grant(@CurrentViewer() viewer: Viewer, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    const { purpose } = parse(GrantBody, body);
    const db = this.prisma.client;
    await db.$transaction(async (tx) => {
      const live = await tx.consentRecord.findFirst({ where: { userId: viewer.userId, purpose, status: "GRANTED" } });
      if (live && live.policyVersion === POLICY_VERSIONS[purpose]) return;
      if (live) {
        // Re-consent to a new policy version: close the old grant, open a new one.
        await tx.consentRecord.update({ where: { id: live.id }, data: { status: "WITHDRAWN", withdrawnAt: new Date(), withdrawalReason: "superseded by new policy version" } });
      }
      const record = await tx.consentRecord.create({
        data: { userId: viewer.userId, purpose, policyVersion: POLICY_VERSIONS[purpose], locale: viewer.locale, ipHash: this.crypto.hash("ip", clientIp(req)) },
      });
      if (purpose === "PUBLIC_PROFILE_DISPLAY") {
        await tx.sellerProfile.updateMany({ where: { userId: viewer.userId }, data: { isPublic: true } });
      }
      await this.audit.record(
        { action: "consent.granted", actorType: "USER", actorUserId: viewer.userId, targetType: "consent_record", targetId: record.id, requestId: String(res.locals.requestId), metadata: { purpose } },
        tx,
      );
    });
    return this.list(viewer);
  }

  @Delete(":purpose")
  @Authenticated()
  @ApiOperation({ summary: "Withdraw consent; stops the processing it covers" })
  async withdraw(@CurrentViewer() viewer: Viewer, @Param("purpose") rawPurpose: string, @Res({ passthrough: true }) res: Response) {
    const purpose = parse(Purpose, rawPurpose);
    if (!WITHDRAWABLE_PURPOSES.includes(purpose)) {
      throw new ApiError(HttpStatus.UNPROCESSABLE_ENTITY, "not_withdrawable", "This consent is required for your account. To withdraw it, close your account.");
    }
    const now = new Date();
    await this.prisma.client.$transaction(async (tx) => {
      const live = await tx.consentRecord.findFirst({ where: { userId: viewer.userId, purpose, status: "GRANTED" } });
      if (!live) return;
      await tx.consentRecord.update({ where: { id: live.id }, data: { status: "WITHDRAWN", withdrawnAt: now, withdrawalReason: "withdrawn by user" } });
      const seller = await tx.sellerProfile.findUnique({ where: { userId: viewer.userId }, select: { id: true } });
      if (seller && purpose === "PUBLIC_PROFILE_DISPLAY") {
        await tx.sellerProfile.update({ where: { id: seller.id }, data: { isPublic: false } });
      }
      if (seller && purpose === "FAYDA_IDENTITY_VERIFICATION") {
        // Withdrawing identity consent revokes the Fayda badge; identity data deletion is scheduled (Phase 7).
        const badges = await tx.sellerBadge.findMany({ where: { sellerProfileId: seller.id, level: "FAYDA_IDENTITY", status: { in: ["ACTIVE", "PENDING", "SUSPENDED"] } } });
        await tx.identityVerification.updateMany({ where: { sellerProfileId: seller.id, status: "VERIFIED" }, data: { status: "REVOKED", revokedAt: now } });
        for (const b of badges) {
          await tx.sellerBadge.update({ where: { id: b.id }, data: { status: "REVOKED", revokedAt: now, revocationReason: "consent withdrawn by seller" } });
          await tx.verificationEvent.create({
            data: { sellerProfileId: seller.id, badgeId: b.id, level: "FAYDA_IDENTITY", type: "REVOKED", actorType: "USER", actorUserId: viewer.userId, reason: "consent withdrawn" },
          });
        }
      }
      await this.audit.record(
        { action: "consent.withdrawn", actorType: "USER", actorUserId: viewer.userId, targetType: "consent_record", targetId: live.id, requestId: String(res.locals.requestId), metadata: { purpose } },
        tx,
      );
    });
    return this.list(viewer);
  }
}
