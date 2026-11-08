import { Body, Controller, Get, HttpCode, HttpStatus, Inject, Param, ParseUUIDPipe, Post, Query, Req, Res } from "@nestjs/common";
import { ApiOperation, ApiTags } from "@nestjs/swagger";
import type { Request, Response } from "express";
import { z } from "zod";
import { clientIp, parse } from "../common/http.js";
import { Authenticated, CurrentViewer, StaffOnly } from "../auth/guards.js";
import type { Viewer } from "../auth/session.service.js";
import { CryptoService } from "../security/crypto.service.js";
import { RULES, RateLimiter } from "../security/rate-limit.js";
import { AppealsService } from "./appeals.service.js";

const Subject = z.enum(["BADGE_DECISION", "ACCOUNT_SUSPENSION", "FRAUD_REPORT_OUTCOME", "DOCUMENT_REJECTION", "DATA_CORRECTION"]);
const AppealBody = z.strictObject({ subjectType: Subject, reason: z.string().trim().min(10).max(5000) });
const DecisionBody = z.strictObject({ outcome: z.enum(["UPHELD", "OVERTURNED"]), note: z.string().trim().min(3).max(2000) });

@ApiTags("appeals")
@Controller({ path: "appeals", version: "1" })
export class AppealsController {
  constructor(
    @Inject(AppealsService) private readonly appeals: AppealsService,
    @Inject(CryptoService) private readonly crypto: CryptoService,
    @Inject(RateLimiter) private readonly limiter: RateLimiter,
  ) {}

  @Post()
  @Authenticated()
  @ApiOperation({ summary: "Appeal a decision or request a data correction" })
  async submit(@CurrentViewer() viewer: Viewer, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    await this.limiter.consume(RULES.appealsPerUser, viewer.userId);
    return { data: await this.appeals.submit(viewer, parse(AppealBody, body), { requestId: String(res.locals.requestId), ipHash: this.crypto.hash("ip", clientIp(req)) }) };
  }

  @Get("me")
  @Authenticated()
  @ApiOperation({ summary: "The seller's appeals and their outcomes" })
  async mine(@CurrentViewer() viewer: Viewer) {
    return { data: await this.appeals.mine(viewer) };
  }
}

@ApiTags("admin")
@Controller({ path: "admin/appeals", version: "1" })
export class AdminAppealsController {
  constructor(
    @Inject(AppealsService) private readonly appeals: AppealsService,
    @Inject(CryptoService) private readonly crypto: CryptoService,
  ) {}

  @Get()
  @StaffOnly("SUPER_ADMIN", "VERIFICATION_OFFICER", "SUPPORT_OFFICER")
  @ApiOperation({ summary: "Open appeals (oldest first)" })
  async list(@Query("status") status?: string) {
    const s = status ? parse(z.enum(["SUBMITTED", "UNDER_REVIEW", "UPHELD", "OVERTURNED", "WITHDRAWN"]), status) : undefined;
    return { data: await this.appeals.list(s) };
  }

  @Post(":id/decision")
  @StaffOnly("SUPER_ADMIN", "VERIFICATION_OFFICER", "SUPPORT_OFFICER")
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: "UPHELD = original decision stands; OVERTURNED = reversed (suspensions then need a second reviewer)" })
  async decide(@CurrentViewer() viewer: Viewer, @Param("id", new ParseUUIDPipe()) id: string, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.appeals.decide(viewer, id, parse(DecisionBody, body), { requestId: String(res.locals.requestId), ipHash: this.crypto.hash("ip", clientIp(req)) }) };
  }
}
