import { Body, Controller, Get, HttpCode, HttpStatus, Inject, Param, ParseUUIDPipe, Patch, Post, Query, Req, Res } from "@nestjs/common";
import { ApiOperation, ApiTags } from "@nestjs/swagger";
import type { Request, Response } from "express";
import { z } from "zod";
import { clientIp, parse } from "../common/http.js";
import { CurrentViewer, StaffOnly } from "../auth/guards.js";
import type { Viewer } from "../auth/session.service.js";
import { CryptoService } from "../security/crypto.service.js";
import { OperationsService } from "./operations.service.js";
import { SellersAdminService } from "./sellers-admin.service.js";

const Reason = z.string().trim().min(5).max(1000);
const ReasonBody = z.strictObject({ reason: Reason });
const Role = z.enum(["SUPER_ADMIN", "VERIFICATION_OFFICER", "SUPPORT_OFFICER", "SECURITY_OFFICER", "FINANCE_OFFICER"]);
const PlanBody = z.strictObject({
  code: z.string().trim().regex(/^[a-z0-9_]{2,64}$/),
  kind: z.enum(["FREE_INDIVIDUAL", "PREMIUM_SELLER", "BUSINESS_VERIFICATION", "MARKETPLACE_API", "ENTERPRISE"]),
  nameEn: z.string().trim().min(2).max(120),
  nameAm: z.string().trim().min(2).max(120),
});
const PlanPatch = z.strictObject({ nameEn: z.string().trim().min(2).max(120).optional(), nameAm: z.string().trim().min(2).max(120).optional(), isActive: z.boolean().optional() });
const PriceBody = z.strictObject({
  amount: z.string().trim().regex(/^\d{1,12}(\.\d{1,2})?$/, "decimal with up to 2 places"),
  currency: z.string().trim().regex(/^[A-Z]{3}$/).default("ETB"),
  interval: z.enum(["MONTH", "YEAR", "ONE_TIME"]),
});

/**
 * Admin console API. Every route needs a staff role (listed per route) and an
 * MFA-verified session. Sensitive actions are audited.
 */
@ApiTags("admin")
@Controller({ path: "admin", version: "1" })
export class AdminController {
  constructor(
    @Inject(SellersAdminService) private readonly sellers: SellersAdminService,
    @Inject(OperationsService) private readonly ops: OperationsService,
    @Inject(CryptoService) private readonly crypto: CryptoService,
  ) {}

  private meta(req: Request, res: Response) {
    return { requestId: String(res.locals.requestId), ipHash: this.crypto.hash("ip", clientIp(req)) };
  }

  @Get("me")
  @StaffOnly()
  @ApiOperation({ summary: "Staff identity and roles" })
  me(@CurrentViewer() viewer: Viewer) {
    return { data: { userId: viewer.userId, roles: viewer.roles } };
  }

  @Get("overview")
  @StaffOnly()
  @ApiOperation({ summary: "Queue sizes and status counts" })
  async overview() {
    return { data: await this.ops.overview() };
  }

  @Get("reviews/second")
  @StaffOnly("SUPER_ADMIN", "VERIFICATION_OFFICER", "SECURITY_OFFICER")
  @ApiOperation({ summary: "Decisions waiting for a second reviewer" })
  async secondReviews() {
    return { data: await this.ops.secondReviews() };
  }

  // ── Sellers ──
  @Get("sellers")
  @StaffOnly("SUPER_ADMIN", "VERIFICATION_OFFICER", "SUPPORT_OFFICER", "SECURITY_OFFICER")
  @ApiOperation({ summary: "Search sellers by Tamagne ID, phone (exact) or name" })
  async search(@Query("q") q = "") {
    return { data: await this.sellers.search(String(q)) };
  }

  @Get("sellers/:tamagneId")
  @StaffOnly("SUPER_ADMIN", "VERIFICATION_OFFICER", "SUPPORT_OFFICER", "SECURITY_OFFICER")
  @ApiOperation({ summary: "Seller detail: status, badges, business, reports, appeals, history" })
  async seller(@Param("tamagneId") tamagneId: string) {
    return { data: await this.sellers.detail(tamagneId) };
  }

  @Post("sellers/:tamagneId/suspend")
  @StaffOnly("SUPER_ADMIN", "SUPPORT_OFFICER", "SECURITY_OFFICER")
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: "Suspend a seller (reason required; immediate)" })
  async suspend(@CurrentViewer() viewer: Viewer, @Param("tamagneId") tamagneId: string, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.sellers.suspend(viewer, tamagneId, parse(ReasonBody, body).reason, this.meta(req, res)) };
  }

  @Post("sellers/:tamagneId/reactivate")
  @StaffOnly("SUPER_ADMIN", "SECURITY_OFFICER")
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: "Request reactivation (high-risk: needs a second reviewer)" })
  async reactivate(@CurrentViewer() viewer: Viewer, @Param("tamagneId") tamagneId: string, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.sellers.requestReactivation(viewer, tamagneId, parse(ReasonBody, body).reason, this.meta(req, res)) };
  }

  @Post("badges/:id/revoke")
  @StaffOnly("SUPER_ADMIN", "VERIFICATION_OFFICER", "SECURITY_OFFICER")
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: "Revoke a verification badge (reason required)" })
  async revokeBadge(@CurrentViewer() viewer: Viewer, @Param("id", new ParseUUIDPipe()) id: string, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.sellers.revokeBadge(viewer, id, parse(ReasonBody, body).reason, this.meta(req, res)) };
  }

  // ── Expiry ──
  @Get("expiry")
  @StaffOnly("SUPER_ADMIN", "VERIFICATION_OFFICER")
  @ApiOperation({ summary: "Badges expiring within N days (default 30), including lapsed ones" })
  async expiry(@Query("days") days = "30") {
    return { data: await this.ops.expiring(parse(z.coerce.number().int().min(1).max(365), days)) };
  }

  @Post("jobs/expiry")
  @StaffOnly("SUPER_ADMIN")
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: "Run the expiry job now (it also runs on a schedule)" })
  async runExpiry() {
    return { data: await this.ops.runExpiryJob() };
  }

  // ── Audit ──
  @Get("audit")
  @StaffOnly("SUPER_ADMIN", "SECURITY_OFFICER")
  @ApiOperation({ summary: "Audit log (newest first, 50 per page)" })
  async audit(@Query() q: Record<string, string | undefined>) {
    const f = parse(
      z.object({
        action: z.string().max(100).optional(),
        actorUserId: z.uuid().optional(),
        targetType: z.string().max(64).optional(),
        targetId: z.string().max(64).optional(),
        before: z.string().regex(/^\d{1,19}$/).transform(BigInt).optional(),
      }),
      q,
    );
    return { data: await this.ops.auditLog(f) };
  }

  @Get("security/events")
  @StaffOnly("SUPER_ADMIN", "SECURITY_OFFICER")
  @ApiOperation({ summary: "Security event counts (24 h / 7 d) with alert thresholds, and recent events" })
  async securityEvents() {
    return { data: await this.ops.securityEvents() };
  }

  @Get("audit/verify")
  @StaffOnly("SUPER_ADMIN", "SECURITY_OFFICER")
  @ApiOperation({ summary: "Verify the audit log hash chain" })
  async verifyAudit(@CurrentViewer() viewer: Viewer, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.ops.verifyAuditChain(viewer, this.meta(req, res)) };
  }

  // ── Staff ──
  @Get("staff")
  @StaffOnly("SUPER_ADMIN")
  @ApiOperation({ summary: "Live staff role grants" })
  async staff() {
    return { data: await this.ops.staff() };
  }

  @Post("staff")
  @StaffOnly("SUPER_ADMIN")
  @ApiOperation({ summary: "Grant a staff role to a registered account (not yourself)" })
  async grant(@CurrentViewer() viewer: Viewer, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.ops.grantRole(viewer, parse(z.strictObject({ phone: z.string().min(6).max(32), role: Role }), body), this.meta(req, res)) };
  }

  @Post("staff/:id/revoke")
  @StaffOnly("SUPER_ADMIN")
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: "Revoke a staff role grant (not your own; never the last Super Admin)" })
  async revoke(@CurrentViewer() viewer: Viewer, @Param("id", new ParseUUIDPipe()) id: string, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.ops.revokeRole(viewer, id, this.meta(req, res)) };
  }

  // ── Pricing & revenue ──
  @Get("plans")
  @StaffOnly("SUPER_ADMIN", "FINANCE_OFFICER")
  @ApiOperation({ summary: "Plans with current prices and price history" })
  async plans() {
    return { data: await this.ops.plans() };
  }

  @Post("plans")
  @StaffOnly("SUPER_ADMIN", "FINANCE_OFFICER")
  @ApiOperation({ summary: "Create a plan" })
  async createPlan(@CurrentViewer() viewer: Viewer, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.ops.createPlan(viewer, parse(PlanBody, body), this.meta(req, res)) };
  }

  @Patch("plans/:id")
  @StaffOnly("SUPER_ADMIN", "FINANCE_OFFICER")
  @ApiOperation({ summary: "Rename or (de)activate a plan" })
  async updatePlan(@CurrentViewer() viewer: Viewer, @Param("id", new ParseUUIDPipe()) id: string, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.ops.updatePlan(viewer, id, parse(PlanPatch, body), this.meta(req, res)) };
  }

  @Post("plans/:id/prices")
  @StaffOnly("SUPER_ADMIN", "FINANCE_OFFICER")
  @ApiOperation({ summary: "Set a new price (the previous one is closed, kept in history)" })
  async setPrice(@CurrentViewer() viewer: Viewer, @Param("id", new ParseUUIDPipe()) id: string, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.ops.setPrice(viewer, id, parse(PriceBody, body), this.meta(req, res)) };
  }

  @Get("revenue")
  @StaffOnly("SUPER_ADMIN", "FINANCE_OFFICER")
  @ApiOperation({ summary: "Revenue by month from recorded payments" })
  async revenue(@Query("months") months = "12") {
    return { data: await this.ops.revenue(parse(z.coerce.number().int().min(1).max(36), months)) };
  }
}
