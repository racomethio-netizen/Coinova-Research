import { Body, Controller, Get, HttpCode, HttpStatus, Inject, Param, ParseUUIDPipe, Patch, Post, Query, Req, Res } from "@nestjs/common";
import { ApiAcceptedResponse, ApiOperation, ApiTags } from "@nestjs/swagger";
import type { Request, Response } from "express";
import { z } from "zod";
import { clientIp, parse } from "../common/http.js";
import { CurrentViewer, StaffOnly } from "../auth/guards.js";
import { SessionService, type Viewer } from "../auth/session.service.js";
import { CryptoService } from "../security/crypto.service.js";
import { RULES, RateLimiter } from "../security/rate-limit.js";
import { ReportsService } from "./reports.service.js";

const Category = z.enum(["NON_DELIVERY", "COUNTERFEIT_PRODUCT", "IMPERSONATION", "PAYMENT_FRAUD", "MISLEADING_INFORMATION", "OTHER"]);
const Status = z.enum(["SUBMITTED", "TRIAGED", "INVESTIGATING", "ACTION_TAKEN", "DISMISSED"]);
const ReportBody = z.strictObject({
  seller: z.string().trim().min(3).max(64),
  category: Category,
  // eslint-disable-next-line no-control-regex -- strip control characters except newlines
  description: z.string().transform((s) => s.replace(/[\u0000-\u0009\u000b-\u001f\u007f]/g, "").trim()).pipe(z.string().min(20).max(5000)),
  contact: z.union([z.string().trim().min(3).max(200), z.literal("").transform(() => null), z.null()]).optional(),
});
const UpdateBody = z.strictObject({ status: Status, note: z.string().trim().min(3).max(2000).optional(), assignToMe: z.boolean().optional() });

@ApiTags("reports")
@Controller({ path: "reports", version: "1" })
export class ReportsController {
  constructor(
    @Inject(ReportsService) private readonly reports: ReportsService,
    @Inject(SessionService) private readonly sessions: SessionService,
    @Inject(RateLimiter) private readonly limiter: RateLimiter,
    @Inject(CryptoService) private readonly crypto: CryptoService,
  ) {}

  @Post()
  @HttpCode(HttpStatus.ACCEPTED)
  @ApiOperation({ summary: "Report a seller (sign-in optional). Same response whether or not the seller exists." })
  @ApiAcceptedResponse({ description: "Report received" })
  async submit(@Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    const ipHash = this.crypto.hash("ip", clientIp(req));
    await this.limiter.consume(RULES.reportsPerIp, ipHash);
    const input = parse(ReportBody, body);
    const viewer = await this.sessions.resolve(req);
    return { data: await this.reports.submit(input, viewer, { requestId: String(res.locals.requestId), ipHash }) };
  }
}

@ApiTags("admin")
@Controller({ path: "admin/reports", version: "1" })
export class AdminReportsController {
  constructor(
    @Inject(ReportsService) private readonly reports: ReportsService,
    @Inject(CryptoService) private readonly crypto: CryptoService,
  ) {}

  private meta(req: Request, res: Response) {
    return { requestId: String(res.locals.requestId), ipHash: this.crypto.hash("ip", clientIp(req)) };
  }

  @Get()
  @StaffOnly("SUPER_ADMIN", "SUPPORT_OFFICER", "SECURITY_OFFICER")
  @ApiOperation({ summary: "Fraud reports (open by default)" })
  async list(@Query("status") status?: string) {
    return { data: await this.reports.list(status ? parse(Status, status) : undefined) };
  }

  @Get(":id")
  @StaffOnly("SUPER_ADMIN", "SUPPORT_OFFICER", "SECURITY_OFFICER")
  @ApiOperation({ summary: "Report detail (viewing reporter contact is audited)" })
  async detail(@CurrentViewer() viewer: Viewer, @Param("id", new ParseUUIDPipe()) id: string, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.reports.detail(viewer, id, this.meta(req, res)) };
  }

  @Patch(":id")
  @StaffOnly("SUPER_ADMIN", "SUPPORT_OFFICER", "SECURITY_OFFICER")
  @ApiOperation({ summary: "Triage, investigate, resolve or dismiss (closing needs a note)" })
  async update(@CurrentViewer() viewer: Viewer, @Param("id", new ParseUUIDPipe()) id: string, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.reports.update(viewer, id, parse(UpdateBody, body), this.meta(req, res)) };
  }
}
