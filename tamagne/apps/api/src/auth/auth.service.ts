import { randomUUID } from "node:crypto";
import { HttpStatus, Inject, Injectable } from "@nestjs/common";
import {
  OTP_MAX_ATTEMPTS,
  OTP_TTL_SECONDS,
  POLICY_VERSIONS,
  generateOtpCode,
  hashOtpCode,
  normalizePhone,
  verifyOtpCode,
} from "@tamagne/core";
import type { Response } from "express";
import { ApiError } from "../common/http.js";
import { APP_CONFIG } from "../config/config.module.js";
import type { AppConfig } from "../config/env.js";
import { AuditService } from "../audit/audit.service.js";
import { PrismaService } from "../database/prisma.service.js";
import { CryptoService } from "../security/crypto.service.js";
import { RULES, RateLimiter } from "../security/rate-limit.js";
import { SMS_SENDER, otpMessage, type SmsSender } from "../sms/sms.js";
import { SessionService } from "./session.service.js";

export interface RequestMeta {
  ipHash: string;
  requestId: string;
  userAgent?: string;
}

const INVALID_CODE = () => new ApiError(HttpStatus.BAD_REQUEST, "invalid_code", "The code is incorrect or has expired.");

@Injectable()
export class AuthService {
  constructor(
    @Inject(PrismaService) private readonly prisma: PrismaService,
    @Inject(APP_CONFIG) private readonly config: AppConfig,
    @Inject(CryptoService) private readonly crypto: CryptoService,
    @Inject(RateLimiter) private readonly limiter: RateLimiter,
    @Inject(SMS_SENDER) private readonly sms: SmsSender,
    @Inject(SessionService) private readonly sessions: SessionService,
    @Inject(AuditService) private readonly audit: AuditService,
  ) {}

  normalize(phone: string): string {
    const e164 = normalizePhone(phone, { allowTestRange: this.config.allowTestPhoneNumbers });
    if (!e164) {
      throw new ApiError(HttpStatus.UNPROCESSABLE_ENTITY, "invalid_phone", "Enter an Ethiopian mobile number, e.g. 0911 223 344.", [
        { path: "phone", message: "invalid phone number" },
      ]);
    }
    return e164;
  }

  /**
   * Starts phone verification for registration or login. The response is identical
   * whether or not the number has an account, so it cannot be used to discover users.
   * An SMS is only sent when it could lead to a successful sign-in.
   */
  async requestCode(rawPhone: string, purpose: "REGISTRATION" | "LOGIN", locale: "EN" | "AM", meta: RequestMeta) {
    const phone = this.normalize(rawPhone);
    if (!this.sms.available) {
      throw new ApiError(HttpStatus.SERVICE_UNAVAILABLE, "sms_unavailable", "Phone verification is temporarily unavailable.");
    }
    const phoneKey = this.crypto.hash("phone", phone);
    await this.limiter.consume({ ...RULES.otpSendPerIp, limit: this.config.otpSendLimitPerIp }, meta.ipHash);
    await this.limiter.consume(RULES.otpResendGap, phoneKey);
    await this.limiter.consume(RULES.otpSendPerPhone, phoneKey);

    const user = await this.prisma.client.user.findUnique({ where: { phoneE164: phone }, select: { id: true, status: true } });
    const canSignIn = user ? user.status === "ACTIVE" || user.status === "PENDING_PHONE_VERIFICATION" : purpose === "REGISTRATION";

    const code = generateOtpCode();
    const id = randomUUID(); // generated here because the code hash is bound to the challenge id
    const challenge = await this.prisma.client.phoneOtpChallenge.create({
      data: {
        id,
        phoneE164: phone,
        purpose,
        // When no sign-in is possible, store a hash no code can match (and send no SMS).
        codeHash: canSignIn ? hashOtpCode(this.crypto.pepper, id, code) : "f".repeat(64),
        maxAttempts: OTP_MAX_ATTEMPTS,
        ipHash: meta.ipHash,
        expiresAt: new Date(Date.now() + OTP_TTL_SECONDS * 1000),
      },
    });
    if (canSignIn) await this.sms.send({ to: phone, body: otpMessage(code, locale) });

    await this.audit.recordSafe({
      action: "auth.otp_requested",
      actorType: "SYSTEM",
      targetType: "phone_otp_challenge",
      targetId: challenge.id,
      requestId: meta.requestId,
      ipHash: meta.ipHash,
      metadata: { purpose },
    });
    return { challengeId: challenge.id, expiresAt: challenge.expiresAt.toISOString(), resendAfterSeconds: RULES.otpResendGap.windowSeconds };
  }

  /** Verifies the code, creates the account on first registration, and starts a session. */
  async verifyCode(challengeId: string, code: string, locale: "EN" | "AM", res: Response, meta: RequestMeta) {
    // Brute force is stopped per code (5 attempts); the per-IP cap only limits abuse volume,
    // and scales with the send limit because carriers share IPs.
    await this.limiter.consume({ ...RULES.otpVerifyPerIp, limit: this.config.otpSendLimitPerIp * 3 }, meta.ipHash);
    const db = this.prisma.client;
    const now = new Date();

    // Count the attempt atomically before checking, so parallel guesses can't exceed the limit.
    const counted = await db.$executeRaw`
      update phone_otp_challenges set attempts = attempts + 1
      where id = ${challengeId}::uuid and "consumedAt" is null and "expiresAt" > ${now} and attempts < "maxAttempts"`;
    if (counted !== 1) throw INVALID_CODE();

    const challenge = await db.phoneOtpChallenge.findUniqueOrThrow({ where: { id: challengeId } });
    if (!verifyOtpCode(this.crypto.pepper, challenge.id, code, challenge.codeHash)) {
      await this.audit.recordSafe({
        action: "auth.otp_failed",
        actorType: "SYSTEM",
        targetType: "phone_otp_challenge",
        targetId: challenge.id,
        requestId: meta.requestId,
        ipHash: meta.ipHash,
        metadata: { attempts: challenge.attempts },
      });
      throw INVALID_CODE();
    }

    return db.$transaction(async (tx) => {
      const consumed = await tx.phoneOtpChallenge.updateMany({ where: { id: challenge.id, consumedAt: null }, data: { consumedAt: now } });
      if (consumed.count !== 1) throw INVALID_CODE();

      let user = await tx.user.findUnique({ where: { phoneE164: challenge.phoneE164 } });
      let isNew = false;
      if (!user) {
        if (challenge.purpose !== "REGISTRATION") throw INVALID_CODE();
        user = await tx.user.create({
          data: { phoneE164: challenge.phoneE164, phoneVerifiedAt: now, status: "ACTIVE", preferredLocale: locale, lastLoginAt: now },
        });
        isNew = true;
        for (const purpose of ["TERMS_OF_SERVICE", "PRIVACY_POLICY"] as const) {
          await tx.consentRecord.create({
            data: { userId: user.id, purpose, policyVersion: POLICY_VERSIONS[purpose], locale, ipHash: meta.ipHash },
          });
        }
      } else {
        if (user.status !== "ACTIVE" && user.status !== "PENDING_PHONE_VERIFICATION") throw INVALID_CODE();
        user = await tx.user.update({ where: { id: user.id }, data: { status: "ACTIVE", phoneVerifiedAt: now, lastLoginAt: now } });
      }

      await this.sessions.create(res, user.id, { ipHash: meta.ipHash, userAgent: meta.userAgent }, tx);
      await this.audit.record(
        {
          action: isNew ? "auth.registered" : "auth.signed_in",
          actorType: "USER",
          actorUserId: user.id,
          targetType: "user",
          targetId: user.id,
          requestId: meta.requestId,
          ipHash: meta.ipHash,
          metadata: { method: "phone_otp" },
        },
        tx,
      );
      const seller = await tx.sellerProfile.findUnique({ where: { userId: user.id }, select: { id: true } });
      const isStaff = (await tx.staffRoleAssignment.count({ where: { userId: user.id, revokedAt: null } })) > 0;
      return { userId: user.id, isNew, hasSellerProfile: !!seller, isStaff };
    });
  }
}
