import { Body, Controller, Get, HttpCode, HttpStatus, Inject, Post, Req, Res } from "@nestjs/common";
import { ApiAcceptedResponse, ApiOkResponse, ApiOperation, ApiTags } from "@nestjs/swagger";
import { maskPhone } from "@tamagne/core";
import type { Request, Response } from "express";
import { z } from "zod";
import { clientIp, parse } from "../common/http.js";
import { AuditService } from "../audit/audit.service.js";
import { PrismaService } from "../database/prisma.service.js";
import { CryptoService } from "../security/crypto.service.js";
import { AuthService, type RequestMeta } from "./auth.service.js";
import { Authenticated, CurrentViewer } from "./guards.js";
import { SessionService, type Viewer } from "./session.service.js";

const Locale = z.enum(["en", "am"]).default("en").transform((l) => (l === "am" ? "AM" : "EN") as "EN" | "AM");
const Phone = z.string().min(6).max(32);

const RegisterBody = z.object({
  phone: Phone,
  locale: Locale,
  acceptTerms: z.literal(true, { message: "You must accept the terms of service" }),
  acceptPrivacy: z.literal(true, { message: "You must accept the privacy policy" }),
});
const LoginBody = z.object({ phone: Phone, locale: Locale });
const VerifyBody = z.object({ challengeId: z.uuid(), code: z.string().regex(/^\d{6}$/, "6 digits"), locale: Locale });

@ApiTags("auth")
@Controller({ path: "auth", version: "1" })
export class AuthController {
  constructor(
    @Inject(AuthService) private readonly auth: AuthService,
    @Inject(SessionService) private readonly sessions: SessionService,
    @Inject(CryptoService) private readonly crypto: CryptoService,
    @Inject(PrismaService) private readonly prisma: PrismaService,
    @Inject(AuditService) private readonly audit: AuditService,
  ) {}

  private meta(req: Request, res: Response): RequestMeta {
    return {
      ipHash: this.crypto.hash("ip", clientIp(req)),
      requestId: String(res.locals.requestId),
      userAgent: req.header("user-agent") ?? undefined,
    };
  }

  @Get("csrf")
  @ApiOperation({ summary: "Issue a CSRF token (also set as a cookie) for state-changing requests" })
  csrf(@Res({ passthrough: true }) res: Response) {
    return { data: { csrfToken: this.sessions.issueCsrf(res) } };
  }

  @Post("register")
  @HttpCode(HttpStatus.ACCEPTED)
  @ApiOperation({ summary: "Start registration: accept terms and send a verification code by SMS" })
  @ApiAcceptedResponse({ description: "Code sent if the number can be registered or signed in" })
  register(@Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    const input = parse(RegisterBody, body);
    return this.auth.requestCode(input.phone, "REGISTRATION", input.locale, this.meta(req, res)).then((data) => ({ data }));
  }

  @Post("login")
  @HttpCode(HttpStatus.ACCEPTED)
  @ApiOperation({ summary: "Start sign-in: send a verification code by SMS (same response for unknown numbers)" })
  login(@Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    const input = parse(LoginBody, body);
    return this.auth.requestCode(input.phone, "LOGIN", input.locale, this.meta(req, res)).then((data) => ({ data }));
  }

  @Post("otp/verify")
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: "Verify the SMS code; creates the account on first registration and starts a session" })
  async verify(@Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    const input = parse(VerifyBody, body);
    return { data: await this.auth.verifyCode(input.challengeId, input.code, input.locale, res, this.meta(req, res)) };
  }

  @Post("logout")
  @HttpCode(HttpStatus.OK)
  @Authenticated()
  async logout(@CurrentViewer() viewer: Viewer, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    await this.sessions.revoke(viewer.sessionId);
    this.sessions.clearCookies(res);
    await this.audit.recordSafe({ action: "auth.signed_out", actorType: "USER", actorUserId: viewer.userId, ...this.meta(req, res) });
    return { data: { signedOut: true } };
  }

  @Get("session")
  @ApiOkResponse({ description: "The current viewer, or null" })
  async session(@Req() req: Request) {
    const viewer = await this.sessions.resolve(req);
    if (!viewer) return { data: null };
    const user = await this.prisma.client.user.findUniqueOrThrow({
      where: { id: viewer.userId },
      select: { phoneE164: true, sellerProfile: { select: { tamagneId: true } }, mfaFactors: { where: { confirmedAt: { not: null }, disabledAt: null }, select: { id: true } } },
    });
    return {
      data: {
        userId: viewer.userId,
        locale: viewer.locale.toLowerCase(),
        phone: user.phoneE164 ? maskPhone(user.phoneE164) : null,
        tamagneId: user.sellerProfile?.tamagneId ?? null,
        roles: viewer.roles,
        mfa: viewer.isStaff ? { enrolled: user.mfaFactors.length > 0, verified: viewer.mfaVerified } : null,
      },
    };
  }
}
