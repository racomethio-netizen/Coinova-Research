import { randomUUID } from "node:crypto";
import { Body, Controller, HttpCode, HttpStatus, Inject, Post, Req, Res } from "@nestjs/common";
import { ApiOperation, ApiTags } from "@nestjs/swagger";
import { generateTotpSecret, totpUri, verifyTotp } from "@tamagne/core";
import QRCode from "qrcode";
import type { Request, Response } from "express";
import { z } from "zod";
import { ApiError, clientIp, parse } from "../common/http.js";
import { AuditService } from "../audit/audit.service.js";
import { PrismaService } from "../database/prisma.service.js";
import { CryptoService } from "../security/crypto.service.js";
import { RULES, RateLimiter } from "../security/rate-limit.js";
import { Authenticated, CurrentViewer } from "./guards.js";
import type { Viewer } from "./session.service.js";

const CodeBody = z.strictObject({ code: z.string().regex(/^\d{6}$/, "6 digits") });
const aad = (factorId: string) => `mfa_factors.secretEnc:${factorId}`;

/** TOTP two-factor authentication. Mandatory for staff before any admin action. */
@ApiTags("auth")
@Controller({ path: "auth/mfa", version: "1" })
export class MfaController {
  constructor(
    @Inject(PrismaService) private readonly prisma: PrismaService,
    @Inject(CryptoService) private readonly crypto: CryptoService,
    @Inject(RateLimiter) private readonly limiter: RateLimiter,
    @Inject(AuditService) private readonly audit: AuditService,
  ) {}

  private requireStaff(viewer: Viewer) {
    if (!viewer.isStaff) throw new ApiError(HttpStatus.FORBIDDEN, "forbidden", "Two-factor enrolment is available to staff accounts.");
  }

  @Post("totp/enroll")
  @Authenticated()
  @ApiOperation({ summary: "Start TOTP enrolment; returns the secret once" })
  async enroll(@CurrentViewer() viewer: Viewer, @Res({ passthrough: true }) res: Response) {
    this.requireStaff(viewer);
    const db = this.prisma.client;
    const confirmed = await db.mfaFactor.findFirst({ where: { userId: viewer.userId, confirmedAt: { not: null }, disabledAt: null } });
    if (confirmed) throw new ApiError(HttpStatus.CONFLICT, "mfa_already_enrolled", "Two-factor authentication is already set up.");
    await db.mfaFactor.updateMany({ where: { userId: viewer.userId, confirmedAt: null, disabledAt: null }, data: { disabledAt: new Date() } });
    const id = randomUUID();
    const secret = generateTotpSecret();
    await db.mfaFactor.create({ data: { id, userId: viewer.userId, type: "TOTP", secretEnc: new Uint8Array(this.crypto.cipher.encrypt(secret, aad(id))) } });
    await this.audit.recordSafe({ action: "mfa.enrollment_started", actorType: "STAFF", actorUserId: viewer.userId, targetType: "mfa_factor", targetId: id, requestId: String(res.locals.requestId) });
    const otpauthUri = totpUri(secret, viewer.userId.slice(0, 8));
    // QR rendered server-side so the secret never goes to a third-party QR service.
    const qrSvg = await QRCode.toString(otpauthUri, { type: "svg", errorCorrectionLevel: "M", margin: 2 });
    return { data: { secret, otpauthUri, qrSvg } };
  }

  @Post("totp/confirm")
  @Authenticated()
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: "Confirm enrolment with a code from the authenticator app" })
  async confirm(@CurrentViewer() viewer: Viewer, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    this.requireStaff(viewer);
    const { code } = parse(CodeBody, body);
    await this.limiter.consume(RULES.mfaVerifyPerUser, viewer.userId);
    const factor = await this.prisma.client.mfaFactor.findFirst({ where: { userId: viewer.userId, confirmedAt: null, disabledAt: null }, orderBy: { createdAt: "desc" } });
    if (!factor) throw new ApiError(HttpStatus.CONFLICT, "mfa_not_started", "Start two-factor setup first.");
    await this.check(viewer, factor.id, factor.secretEnc, factor.lastUsedStep, code, req, res, true);
    return { data: { enrolled: true, verified: true } };
  }

  @Post("verify")
  @Authenticated()
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: "Complete two-factor verification for this session" })
  async verify(@CurrentViewer() viewer: Viewer, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    this.requireStaff(viewer);
    const { code } = parse(CodeBody, body);
    await this.limiter.consume(RULES.mfaVerifyPerUser, viewer.userId);
    const factor = await this.prisma.client.mfaFactor.findFirst({ where: { userId: viewer.userId, confirmedAt: { not: null }, disabledAt: null } });
    if (!factor) throw new ApiError(HttpStatus.FORBIDDEN, "mfa_enrollment_required", "Set up two-factor authentication first.");
    await this.check(viewer, factor.id, factor.secretEnc, factor.lastUsedStep, code, req, res, false);
    return { data: { verified: true } };
  }

  private async check(viewer: Viewer, factorId: string, secretEnc: Uint8Array, lastUsedStep: bigint | null, code: string, req: Request, res: Response, confirming: boolean) {
    const secret = this.crypto.cipher.decrypt(Buffer.from(secretEnc), aad(factorId));
    const step = verifyTotp(secret, code, { lastUsedStep: lastUsedStep === null ? null : Number(lastUsedStep) });
    const ipHash = this.crypto.hash("ip", clientIp(req));
    if (step === null) {
      await this.audit.recordSafe({ action: "mfa.failed", actorType: "STAFF", actorUserId: viewer.userId, targetType: "mfa_factor", targetId: factorId, ipHash, requestId: String(res.locals.requestId) });
      throw new ApiError(HttpStatus.BAD_REQUEST, "invalid_code", "The code is incorrect or was already used.");
    }
    await this.prisma.client.$transaction(async (tx) => {
      // Conditional update: a step can be used once even under concurrent requests.
      const used = await tx.$executeRaw`
        update mfa_factors set "lastUsedStep" = ${step}, "confirmedAt" = coalesce("confirmedAt", now())
        where id = ${factorId}::uuid and ("lastUsedStep" is null or "lastUsedStep" < ${step})`;
      if (used !== 1) throw new ApiError(HttpStatus.BAD_REQUEST, "invalid_code", "The code is incorrect or was already used.");
      await tx.session.update({ where: { id: viewer.sessionId }, data: { mfaVerifiedAt: new Date() } });
      await this.audit.record(
        { action: confirming ? "mfa.enrolled" : "mfa.verified", actorType: "STAFF", actorUserId: viewer.userId, targetType: "mfa_factor", targetId: factorId, ipHash, requestId: String(res.locals.requestId) },
        tx,
      );
    });
  }
}
