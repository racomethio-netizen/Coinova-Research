import { Inject, Injectable } from "@nestjs/common";
import { generateOpaqueToken, sha256Hex } from "@tamagne/core";
import type { Prisma } from "@tamagne/db";
import type { CookieOptions, Request, Response } from "express";
import { readCookie } from "../common/http.js";
import { APP_CONFIG } from "../config/config.module.js";
import type { AppConfig } from "../config/env.js";
import { PrismaService } from "../database/prisma.service.js";

export type StaffRole = "SUPER_ADMIN" | "VERIFICATION_OFFICER" | "SUPPORT_OFFICER" | "SECURITY_OFFICER" | "FINANCE_OFFICER";

export interface Viewer {
  userId: string;
  sessionId: string;
  status: string;
  locale: "EN" | "AM";
  roles: StaffRole[];
  isStaff: boolean;
  mfaVerified: boolean;
}

/** Seller sessions: 30 days absolute, 7 days idle. Staff sessions: 12 hours absolute, 30 minutes idle. */
const LIFETIME = {
  seller: { absoluteMs: 30 * 86_400_000, idleMs: 7 * 86_400_000 },
  staff: { absoluteMs: 12 * 3_600_000, idleMs: 30 * 60_000 },
};
const TOUCH_INTERVAL_MS = 60_000;

@Injectable()
export class SessionService {
  readonly cookieName: string;
  readonly csrfCookieName: string;

  constructor(
    @Inject(PrismaService) private readonly prisma: PrismaService,
    @Inject(APP_CONFIG) private readonly config: AppConfig,
  ) {
    // __Host- cookies must be Secure, path=/ and host-only: they cannot be set by a subdomain.
    this.cookieName = config.secureCookies ? "__Host-tamagne_session" : "tamagne_session";
    this.csrfCookieName = config.secureCookies ? "__Host-tamagne_csrf" : "tamagne_csrf";
  }

  private cookieOptions(httpOnly: boolean, maxAgeMs?: number): CookieOptions {
    return { httpOnly, secure: this.config.secureCookies, sameSite: "lax", path: "/", ...(maxAgeMs ? { maxAge: maxAgeMs } : {}) };
  }

  /** Creates a session (new random token; any previous cookie is replaced, preventing fixation). */
  async create(res: Response, userId: string, meta: { ipHash?: string; userAgent?: string }, tx?: Prisma.TransactionClient) {
    const db = tx ?? this.prisma.client;
    const staff = (await db.staffRoleAssignment.count({ where: { userId, revokedAt: null } })) > 0;
    const life = staff ? LIFETIME.staff : LIFETIME.seller;
    const token = generateOpaqueToken(32);
    const now = new Date();
    const session = await db.session.create({
      data: {
        userId,
        tokenHash: sha256Hex(token),
        ipHash: meta.ipHash ?? null,
        userAgent: meta.userAgent?.slice(0, 256) ?? null,
        expiresAt: new Date(now.getTime() + life.absoluteMs),
      },
    });
    res.cookie(this.cookieName, token, this.cookieOptions(true, life.absoluteMs));
    this.issueCsrf(res);
    return session;
  }

  /** Double-submit CSRF token: readable cookie that the client echoes in X-CSRF-Token. */
  issueCsrf(res: Response): string {
    const token = generateOpaqueToken(32);
    res.cookie(this.csrfCookieName, token, this.cookieOptions(false));
    return token;
  }

  async resolve(req: Request): Promise<Viewer | null> {
    const token = readCookie(req, this.cookieName);
    if (!token || !/^[A-Za-z0-9_-]{43}$/.test(token)) return null;
    const session = await this.prisma.client.session.findUnique({
      where: { tokenHash: sha256Hex(token) },
      include: {
        user: {
          select: {
            id: true,
            status: true,
            preferredLocale: true,
            roleAssignments: { where: { revokedAt: null }, select: { role: true } },
          },
        },
      },
    });
    if (!session || session.revokedAt) return null;
    const now = Date.now();
    const roles = session.user.roleAssignments.map((r) => r.role as StaffRole);
    const life = roles.length ? LIFETIME.staff : LIFETIME.seller;
    if (session.expiresAt.getTime() <= now || session.lastSeenAt.getTime() + life.idleMs <= now) return null;
    if (session.user.status !== "ACTIVE") return null;

    if (now - session.lastSeenAt.getTime() > TOUCH_INTERVAL_MS) {
      await this.prisma.client.session.update({ where: { id: session.id }, data: { lastSeenAt: new Date(now) } });
    }
    return {
      userId: session.user.id,
      sessionId: session.id,
      status: session.user.status,
      locale: session.user.preferredLocale,
      roles,
      isStaff: roles.length > 0,
      mfaVerified: session.mfaVerifiedAt !== null,
    };
  }

  async revoke(sessionId: string): Promise<void> {
    await this.prisma.client.session.updateMany({ where: { id: sessionId, revokedAt: null }, data: { revokedAt: new Date() } });
  }

  clearCookies(res: Response): void {
    res.clearCookie(this.cookieName, this.cookieOptions(true));
    res.clearCookie(this.csrfCookieName, this.cookieOptions(false));
  }
}
