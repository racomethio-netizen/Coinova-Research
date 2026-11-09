import { randomUUID } from "node:crypto";
import { HttpStatus, Inject, Injectable } from "@nestjs/common";
import { POLICY_VERSIONS, badgeExpiry, generateOpaqueToken, generatePkceVerifier, pkceChallengeS256, sha256Hex } from "@tamagne/core";
import { ApiError } from "../common/http.js";
import { APP_CONFIG } from "../config/config.module.js";
import type { AppConfig } from "../config/env.js";
import { AuditService } from "../audit/audit.service.js";
import type { Viewer } from "../auth/session.service.js";
import { PrismaService } from "../database/prisma.service.js";
import { CryptoService } from "../security/crypto.service.js";
import { RULES, RateLimiter } from "../security/rate-limit.js";
import { FaydaError, FaydaOidcClient, type FaydaFailureCode } from "./fayda-oidc.js";

export const FAYDA_CLIENT = Symbol("FAYDA_CLIENT");

const AUTH_REQUEST_TTL_MS = 10 * 60_000;
const IDENTITY_VALIDITY_MS = 365 * 86_400_000;

export type CallbackCode = FaydaFailureCode | "state_invalid" | "state_expired" | "consent_withdrawn" | "duplicate_identity" | "already_verified" | "seller_inactive";

interface Meta {
  requestId: string;
  ipHash: string;
}

@Injectable()
export class FaydaService {
  constructor(
    @Inject(APP_CONFIG) private readonly config: AppConfig,
    @Inject(FAYDA_CLIENT) private readonly client: FaydaOidcClient | null,
    @Inject(PrismaService) private readonly prisma: PrismaService,
    @Inject(CryptoService) private readonly crypto: CryptoService,
    @Inject(AuditService) private readonly audit: AuditService,
    @Inject(RateLimiter) private readonly limiter: RateLimiter,
  ) {}

  private get environment(): "SANDBOX" | "PRODUCTION" {
    return this.config.fayda.enabled && this.config.fayda.mode === "production" ? "PRODUCTION" : "SANDBOX";
  }

  private enabledClient(): FaydaOidcClient {
    if (!this.config.fayda.enabled || !this.client) {
      throw new ApiError(HttpStatus.SERVICE_UNAVAILABLE, "fayda_unavailable", "Fayda verification is not available yet. You can request a manual identity review instead.");
    }
    return this.client;
  }

  /** Records consent (if given), creates a single-use authorization request and returns the eSignet URL. */
  async start(viewer: Viewer, acceptConsent: boolean, uiLocale: "en" | "am", meta: Meta): Promise<{ authorizationUrl: string }> {
    const client = this.enabledClient();
    await this.limiter.consume(RULES.faydaStartPerUser, viewer.userId);
    const db = this.prisma.client;
    const seller = await db.sellerProfile.findUnique({ where: { userId: viewer.userId }, select: { id: true, accountStatus: true } });
    if (!seller) throw new ApiError(HttpStatus.NOT_FOUND, "seller_not_found", "Create your seller profile first.");
    if (seller.accountStatus !== "ACTIVE") throw new ApiError(HttpStatus.FORBIDDEN, "seller_suspended", "Your seller account is not active.");
    const live = await db.sellerBadge.findFirst({ where: { sellerProfileId: seller.id, level: "FAYDA_IDENTITY", status: "ACTIVE", expiresAt: { gt: new Date() } } });
    if (live) throw new ApiError(HttpStatus.CONFLICT, "already_verified", "Your Fayda identity is already verified.");

    let consent = await db.consentRecord.findFirst({ where: { userId: viewer.userId, purpose: "FAYDA_IDENTITY_VERIFICATION", status: "GRANTED" } });
    if (!consent) {
      if (!acceptConsent) {
        throw new ApiError(HttpStatus.CONFLICT, "consent_required", "Agree to Fayda identity verification first.");
      }
      consent = await db.$transaction(async (tx) => {
        const c = await tx.consentRecord.create({
          data: { userId: viewer.userId, purpose: "FAYDA_IDENTITY_VERIFICATION", policyVersion: POLICY_VERSIONS.FAYDA_IDENTITY_VERIFICATION, locale: viewer.locale, ipHash: meta.ipHash },
        });
        await this.audit.record({ action: "consent.granted", actorType: "USER", actorUserId: viewer.userId, targetType: "consent_record", targetId: c.id, ...meta, metadata: { purpose: "FAYDA_IDENTITY_VERIFICATION" } }, tx);
        return c;
      });
    }

    const state = generateOpaqueToken(32);
    const nonce = generateOpaqueToken(32);
    const verifier = generatePkceVerifier();
    const id = randomUUID();
    const consentId = consent.id;
    await db.$transaction(async (tx) => {
      await tx.oidcAuthRequest.create({
        data: {
          id,
          userId: viewer.userId,
          sellerProfileId: seller.id,
          environment: this.environment,
          stateHash: sha256Hex(state),
          nonceHash: sha256Hex(nonce),
          codeVerifierEnc: new Uint8Array(this.crypto.cipher.encrypt(verifier, `oidc_auth_requests.codeVerifierEnc:${id}`)),
          redirectUri: this.config.fayda.enabled ? this.config.fayda.redirectUri : "",
          consentRecordId: consentId,
          expiresAt: new Date(Date.now() + AUTH_REQUEST_TTL_MS),
        },
      });
      await this.audit.record({ action: "fayda.started", actorType: "USER", actorUserId: viewer.userId, targetType: "oidc_auth_request", targetId: id, ...meta, metadata: { environment: this.environment } }, tx);
    });
    let authorizationUrl: string;
    try {
      authorizationUrl = await client.authorizationUrl({ state, nonce, codeChallenge: pkceChallengeS256(verifier), uiLocale });
    } catch {
      throw new ApiError(HttpStatus.SERVICE_UNAVAILABLE, "fayda_unreachable", "Fayda could not be reached. Please try again later.");
    }
    return { authorizationUrl };
  }

  /**
   * Handles the eSignet redirect. The authorization request must belong to the signed-in
   * user (prevents an attacker binding a victim's identity to the attacker's account),
   * be unexpired, and is consumed exactly once before any token is redeemed.
   */
  async callback(viewer: Viewer, query: { state?: string; code?: string; error?: string }, meta: Meta): Promise<{ ok: true } | { ok: false; code: CallbackCode }> {
    const client = this.enabledClient();
    const db = this.prisma.client;
    if (!query.state || !/^[A-Za-z0-9_-]{43}$/.test(query.state)) return this.reject(viewer, null, "state_invalid", meta);

    const req = await db.oidcAuthRequest.findUnique({ where: { stateHash: sha256Hex(query.state) } });
    if (!req || req.userId !== viewer.userId || req.environment !== this.environment) return this.reject(viewer, null, "state_invalid", meta);
    const consumed = await db.oidcAuthRequest.updateMany({ where: { id: req.id, consumedAt: null }, data: { consumedAt: new Date() } });
    if (consumed.count !== 1) return this.reject(viewer, null, "state_invalid", meta);
    if (req.expiresAt.getTime() <= Date.now()) return this.reject(viewer, req, "state_expired", meta);

    if (query.error || !query.code) return this.reject(viewer, req, "provider_error", meta);
    const consent = await db.consentRecord.findUnique({ where: { id: req.consentRecordId } });
    if (consent?.status !== "GRANTED") return this.reject(viewer, req, "consent_withdrawn", meta);
    const seller = await db.sellerProfile.findUniqueOrThrow({ where: { id: req.sellerProfileId }, select: { accountStatus: true } });
    if (seller.accountStatus !== "ACTIVE") return this.reject(viewer, req, "seller_inactive", meta);

    let identity;
    try {
      const verifier = this.crypto.cipher.decrypt(Buffer.from(req.codeVerifierEnc), `oidc_auth_requests.codeVerifierEnc:${req.id}`);
      identity = await client.complete(query.code.slice(0, 2048), verifier, req.nonceHash);
    } catch (e) {
      return this.reject(viewer, req, e instanceof FaydaError ? e.code : "id_token_invalid", meta);
    }

    // Only a keyed hash of the provider subject is stored; never the subject, FIN or FAN.
    const subjectHash = this.crypto.hash("subject", `${identity.issuer}|${identity.subject}`);
    const now = new Date();
    const ivId = randomUUID();
    try {
      await db.$transaction(async (tx) => {
        // Re-verification by the same seller supersedes their previous result.
        const previous = await tx.identityVerification.findFirst({ where: { environment: req.environment, subjectHash, status: "VERIFIED" } });
        if (previous && previous.sellerProfileId !== req.sellerProfileId) throw new DuplicateIdentity();
        if (previous) await tx.identityVerification.update({ where: { id: previous.id }, data: { status: "EXPIRED" } });

        const expiresAt = new Date(now.getTime() + IDENTITY_VALIDITY_MS);
        await tx.identityVerification.create({
          data: {
            id: ivId,
            sellerProfileId: req.sellerProfileId,
            authRequestId: req.id,
            environment: req.environment,
            status: "VERIFIED",
            subjectHash,
            issuer: identity.issuer,
            audience: identity.audience,
            idTokenJti: identity.jti,
            acr: identity.acr,
            signatureVerified: true,
            verifiedNameEnc: identity.name ? new Uint8Array(this.crypto.cipher.encrypt(identity.name, `identity_verifications.verifiedNameEnc:${ivId}`)) : null,
            verifiedAt: now,
            expiresAt,
          },
        });
        const live = await tx.sellerBadge.findMany({ where: { sellerProfileId: req.sellerProfileId, level: "FAYDA_IDENTITY", status: { in: ["ACTIVE", "PENDING", "SUSPENDED"] } } });
        for (const b of live) {
          await tx.sellerBadge.update({ where: { id: b.id }, data: { status: "REVOKED", revokedAt: now, revocationReason: "superseded by a new Fayda verification" } });
        }
        const badgeEnd = new Date(Math.min(badgeExpiry("FAYDA_IDENTITY", now).getTime(), expiresAt.getTime()));
        // The DB trigger independently re-checks that this badge is backed by a VERIFIED, signature-validated result.
        const badge = await tx.sellerBadge.create({
          data: { sellerProfileId: req.sellerProfileId, level: "FAYDA_IDENTITY", method: "FAYDA_ESIGNET_OIDC", status: "ACTIVE", identityVerificationId: ivId, issuedAt: now, expiresAt: badgeEnd },
        });
        await tx.verificationEvent.create({
          data: { sellerProfileId: req.sellerProfileId, badgeId: badge.id, identityVerificationId: ivId, level: "FAYDA_IDENTITY", type: "ISSUED", actorType: "SYSTEM", metadata: { environment: req.environment } },
        });
        await this.audit.record(
          { action: "fayda.verified", actorType: "USER", actorUserId: viewer.userId, targetType: "identity_verification", targetId: ivId, ...meta, metadata: { environment: req.environment, acr: identity.acr } },
          tx,
        );
      });
    } catch (e) {
      if (e instanceof DuplicateIdentity || (e as { code?: string })?.code === "P2002") {
        await this.audit.recordSafe({ action: "fayda.duplicate_identity", actorType: "USER", actorUserId: viewer.userId, targetType: "oidc_auth_request", targetId: req.id, ...meta });
        return this.reject(viewer, req, "duplicate_identity", meta);
      }
      throw e;
    }
    return { ok: true };
  }

  private async reject(viewer: Viewer, req: { id: string; sellerProfileId: string; environment: "SANDBOX" | "PRODUCTION" } | null, code: CallbackCode, meta: Meta) {
    if (req) {
      await this.prisma.client.identityVerification.create({
        data: { sellerProfileId: req.sellerProfileId, authRequestId: req.id, environment: req.environment, status: "FAILED", failureCode: code },
      });
      await this.prisma.client.verificationEvent.create({
        data: { sellerProfileId: req.sellerProfileId, level: "FAYDA_IDENTITY", type: "FAILED", actorType: "SYSTEM", reason: code },
      });
    }
    await this.audit.recordSafe({ action: "fayda.failed", actorType: "USER", actorUserId: viewer.userId, targetType: "oidc_auth_request", targetId: req?.id, ...meta, metadata: { reason: code } });
    return { ok: false as const, code };
  }
}

class DuplicateIdentity extends Error {}
