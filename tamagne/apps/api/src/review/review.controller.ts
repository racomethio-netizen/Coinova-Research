import { Body, Controller, Get, HttpCode, HttpStatus, Inject, Param, ParseUUIDPipe, Post, Req, Res } from "@nestjs/common";
import { ApiOperation, ApiProduces, ApiTags } from "@nestjs/swagger";
import type { Request, Response } from "express";
import { z } from "zod";
import { clientIp, parse } from "../common/http.js";
import { CurrentViewer, StaffOnly } from "../auth/guards.js";
import type { Viewer } from "../auth/session.service.js";
import { CryptoService } from "../security/crypto.service.js";
import { ReviewService } from "./review.service.js";

const Reason = z.string().trim().min(3).max(2000);
const DecisionBody = z.strictObject({
  decision: z.enum(["APPROVE", "REJECT", "REQUEST_CHANGES"]),
  reason: Reason,
  riskLevel: z.enum(["LOW", "MEDIUM", "HIGH"]).default("LOW"),
  documents: z
    .array(z.strictObject({ documentId: z.uuid(), status: z.enum(["APPROVED", "REJECTED", "NEEDS_RESUBMISSION"]), reason: Reason.optional() }))
    .max(10)
    .default([]),
});
const SecondBody = z.strictObject({ decision: z.enum(["APPROVE", "REJECT", "REQUEST_CHANGES"]), reason: Reason });

/** Verification officers' business review. Admin UI arrives in Phase 6; these are the server-side rules. */
@ApiTags("admin")
@Controller({ path: "admin", version: "1" })
export class ReviewController {
  constructor(
    @Inject(ReviewService) private readonly review: ReviewService,
    @Inject(CryptoService) private readonly crypto: CryptoService,
  ) {}

  private meta(req: Request, res: Response) {
    return { requestId: String(res.locals.requestId), ipHash: this.crypto.hash("ip", clientIp(req)) };
  }

  @Get("reviews/businesses")
  @StaffOnly("SUPER_ADMIN", "VERIFICATION_OFFICER")
  @ApiOperation({ summary: "Business verification queue (oldest first) and pending second reviews" })
  async queue() {
    return { data: await this.review.queue() };
  }

  @Get("businesses/:id")
  @StaffOnly("SUPER_ADMIN", "VERIFICATION_OFFICER", "SECURITY_OFFICER")
  @ApiOperation({ summary: "Business details, documents (metadata) and review history" })
  async detail(@Param("id", new ParseUUIDPipe()) id: string) {
    return { data: await this.review.detail(id) };
  }

  @Get("documents/:id/file")
  @StaffOnly("SUPER_ADMIN", "VERIFICATION_OFFICER", "SECURITY_OFFICER")
  @ApiProduces("application/pdf", "image/jpeg", "image/png")
  @ApiOperation({ summary: "Download a scanned document (audited)" })
  async file(@CurrentViewer() viewer: Viewer, @Param("id", new ParseUUIDPipe()) id: string, @Req() req: Request, @Res() res: Response) {
    const f = await this.review.documentFile(viewer, id, this.meta(req, res));
    res.setHeader("Content-Type", f.mimeType);
    // Always a download, never rendered inline in our origin; sandboxed if a browser tries.
    res.setHeader("Content-Disposition", `attachment; filename="${f.type.toLowerCase()}-${id.slice(0, 8)}.${f.mimeType === "application/pdf" ? "pdf" : f.mimeType === "image/png" ? "png" : "jpg"}"`);
    res.setHeader("Content-Security-Policy", "sandbox; default-src 'none'");
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.send(f.data);
  }

  @Post("businesses/:id/decision")
  @StaffOnly("SUPER_ADMIN", "VERIFICATION_OFFICER")
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: "Approve, reject or request changes (reasons required; HIGH risk needs a second reviewer)" })
  async decide(@CurrentViewer() viewer: Viewer, @Param("id", new ParseUUIDPipe()) id: string, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.review.decide(viewer, id, parse(DecisionBody, body), this.meta(req, res)) };
  }

  @Post("reviews/:id/second-review")
  @StaffOnly("SUPER_ADMIN", "VERIFICATION_OFFICER", "SECURITY_OFFICER")
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: "Second reviewer's decision on a high-risk review" })
  async second(@CurrentViewer() viewer: Viewer, @Param("id", new ParseUUIDPipe()) id: string, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.review.secondReview(viewer, id, parse(SecondBody, body), this.meta(req, res)) };
  }

  @Post("documents/:id/rescan")
  @StaffOnly("SUPER_ADMIN", "VERIFICATION_OFFICER", "SECURITY_OFFICER")
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: "Re-run the malware scan on a document whose scan failed" })
  async rescan(@CurrentViewer() viewer: Viewer, @Param("id", new ParseUUIDPipe()) id: string, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.review.rescan(viewer, id, this.meta(req, res)) };
  }
}
