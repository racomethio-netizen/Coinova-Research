import { Body, Controller, Get, HttpCode, HttpStatus, Inject, Param, ParseUUIDPipe, Patch, Post, Query, Req, Res } from "@nestjs/common";
import { ApiOperation, ApiTags } from "@nestjs/swagger";
import type { Request, Response } from "express";
import { z } from "zod";
import { clientIp, parse } from "../common/http.js";
import { Authenticated, CurrentViewer, StaffOnly } from "../auth/guards.js";
import { SessionService, type Viewer } from "../auth/session.service.js";
import { CryptoService } from "../security/crypto.service.js";
import { RULES, RateLimiter } from "../security/rate-limit.js";
import { PrivacyService } from "./privacy.service.js";

const CorrectionBody = z.strictObject({ details: z.string().trim().min(10).max(2000) });
const DeletionBody = z.strictObject({ confirm: z.literal("DELETE", { message: "Type DELETE to confirm" }) });
const UpdateBody = z.strictObject({ status: z.enum(["IN_PROGRESS", "COMPLETED", "REJECTED"]), outcome: z.string().trim().min(3).max(2000).optional() });

@ApiTags("privacy")
@Controller({ path: "privacy", version: "1" })
export class PrivacyController {
  constructor(
    @Inject(PrivacyService) private readonly privacy: PrivacyService,
    @Inject(SessionService) private readonly sessions: SessionService,
    @Inject(CryptoService) private readonly crypto: CryptoService,
    @Inject(RateLimiter) private readonly limiter: RateLimiter,
  ) {}

  private meta(req: Request, res: Response) {
    return { requestId: String(res.locals.requestId), ipHash: this.crypto.hash("ip", clientIp(req)) };
  }

  @Get("export")
  @Authenticated()
  @ApiOperation({ summary: "Download all personal data TAMAGNE holds about you (JSON)" })
  async export(@CurrentViewer() viewer: Viewer, @Req() req: Request, @Res() res: Response) {
    await this.limiter.consume(RULES.exportPerUser, viewer.userId);
    const data = await this.privacy.exportData(viewer, this.meta(req, res));
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.setHeader("Content-Disposition", `attachment; filename="tamagne-my-data-${new Date().toISOString().slice(0, 10)}.json"`);
    res.setHeader("Cache-Control", "no-store");
    res.send(JSON.stringify(data, null, 2));
  }

  @Get("requests")
  @Authenticated()
  @ApiOperation({ summary: "Your privacy requests and their status" })
  async mine(@CurrentViewer() viewer: Viewer) {
    return { data: await this.privacy.mine(viewer) };
  }

  @Post("requests/correction")
  @Authenticated()
  @ApiOperation({ summary: "Ask for personal data to be corrected" })
  async correction(@CurrentViewer() viewer: Viewer, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.privacy.requestCorrection(viewer, parse(CorrectionBody, body).details, this.meta(req, res)) };
  }

  @Post("deletion")
  @Authenticated()
  @HttpCode(HttpStatus.ACCEPTED)
  @ApiOperation({ summary: "Delete your account (locked now, erased after the grace period)" })
  async deletion(@CurrentViewer() viewer: Viewer, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    parse(DeletionBody, body);
    const result = await this.privacy.requestDeletion(viewer, this.meta(req, res));
    this.sessions.clearCookies(res);
    return { data: result };
  }
}

@ApiTags("admin")
@Controller({ path: "admin/privacy", version: "1" })
export class AdminPrivacyController {
  constructor(
    @Inject(PrivacyService) private readonly privacy: PrivacyService,
    @Inject(CryptoService) private readonly crypto: CryptoService,
  ) {}

  @Get("requests")
  @StaffOnly("SUPER_ADMIN", "SUPPORT_OFFICER")
  @ApiOperation({ summary: "Open data-subject requests (oldest due first)" })
  async list(@Query("status") status?: string) {
    return { data: await this.privacy.list({ status: status ? parse(z.enum(["RECEIVED", "IN_PROGRESS", "COMPLETED", "REJECTED"]), status) : undefined }) };
  }

  @Patch("requests/:id")
  @StaffOnly("SUPER_ADMIN", "SUPPORT_OFFICER")
  @ApiOperation({ summary: "Progress a correction request, or cancel a pending deletion (REJECTED)" })
  async update(@CurrentViewer() viewer: Viewer, @Param("id", new ParseUUIDPipe()) id: string, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.privacy.update(viewer, id, parse(UpdateBody, body), { requestId: String(res.locals.requestId), ipHash: this.crypto.hash("ip", clientIp(req)) }) };
  }
}
