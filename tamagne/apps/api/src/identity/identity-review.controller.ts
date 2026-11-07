/// <reference types="multer" />
import { Body, Controller, Delete, Get, HttpCode, HttpStatus, Inject, Param, ParseUUIDPipe, Post, Req, Res, UploadedFile, UseInterceptors } from "@nestjs/common";
import { FileInterceptor } from "@nestjs/platform-express";
import { ApiBody, ApiConsumes, ApiOperation, ApiTags } from "@nestjs/swagger";
import { DOCUMENT_MAX_BYTES } from "@tamagne/core";
import type { Request, Response } from "express";
import { z } from "zod";
import { ApiError, clientIp, parse } from "../common/http.js";
import { Authenticated, CurrentViewer, StaffOnly } from "../auth/guards.js";
import type { Viewer } from "../auth/session.service.js";
import { CryptoService } from "../security/crypto.service.js";
import { RULES, RateLimiter } from "../security/rate-limit.js";
import { IdentityReviewService } from "./identity-review.service.js";

const DOC_TYPES = ["FAYDA_ID_CARD", "KEBELE_ID", "PASSPORT", "DRIVING_LICENSE"] as const;
const UploadFields = z.strictObject({ documentType: z.enum(DOC_TYPES) });
const DecisionBody = z.strictObject({ decision: z.enum(["APPROVE", "REJECT"]), reason: z.string().trim().min(3).max(2000), riskLevel: z.enum(["LOW", "MEDIUM", "HIGH"]).default("LOW") });

@ApiTags("identity")
@Controller({ path: "identity/manual", version: "1" })
export class IdentityReviewController {
  constructor(
    @Inject(IdentityReviewService) private readonly reviews: IdentityReviewService,
    @Inject(CryptoService) private readonly crypto: CryptoService,
    @Inject(RateLimiter) private readonly limiter: RateLimiter,
  ) {}

  private meta(req: Request, res: Response) {
    return { requestId: String(res.locals.requestId), ipHash: this.crypto.hash("ip", clientIp(req)) };
  }

  @Get()
  @Authenticated()
  @ApiOperation({ summary: "Status of the seller's manual identity review" })
  async get(@CurrentViewer() viewer: Viewer) {
    return { data: await this.reviews.get(viewer.userId) };
  }

  @Post()
  @Authenticated()
  @UseInterceptors(FileInterceptor("file", { limits: { fileSize: DOCUMENT_MAX_BYTES, files: 1, fields: 2, fieldSize: 64, parts: 3 } }))
  @ApiConsumes("multipart/form-data")
  @ApiBody({ schema: { type: "object", required: ["documentType", "file"], properties: { documentType: { type: "string", enum: [...DOC_TYPES] }, file: { type: "string", format: "binary" } } } })
  @ApiOperation({ summary: "Submit an ID document for manual identity review (interim; never shown as Fayda)" })
  async submit(@CurrentViewer() viewer: Viewer, @UploadedFile() file: Express.Multer.File | undefined, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    await this.limiter.consume(RULES.uploadPerUser, viewer.userId);
    const { documentType } = parse(UploadFields, body);
    if (!file) throw new ApiError(HttpStatus.UNPROCESSABLE_ENTITY, "file_required", "Choose a file to upload.");
    return { data: await this.reviews.submit(viewer.userId, documentType, file, this.meta(req, res)) };
  }

  @Delete()
  @Authenticated()
  @ApiOperation({ summary: "Withdraw a pending request (the image is deleted)" })
  async withdraw(@CurrentViewer() viewer: Viewer, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.reviews.withdraw(viewer.userId, this.meta(req, res)) };
  }
}

@ApiTags("admin")
@Controller({ path: "admin", version: "1" })
export class AdminIdentityReviewController {
  constructor(
    @Inject(IdentityReviewService) private readonly reviews: IdentityReviewService,
    @Inject(CryptoService) private readonly crypto: CryptoService,
  ) {}

  private meta(req: Request, res: Response) {
    return { requestId: String(res.locals.requestId), ipHash: this.crypto.hash("ip", clientIp(req)) };
  }

  @Get("reviews/identity")
  @StaffOnly("SUPER_ADMIN", "VERIFICATION_OFFICER")
  @ApiOperation({ summary: "Manual identity review queue" })
  async queue() {
    return { data: await this.reviews.queue() };
  }

  @Get("identity-reviews/:id/file")
  @StaffOnly("SUPER_ADMIN", "VERIFICATION_OFFICER")
  @ApiOperation({ summary: "Download the ID document (audited)" })
  async file(@CurrentViewer() viewer: Viewer, @Param("id", new ParseUUIDPipe()) id: string, @Req() req: Request, @Res() res: Response) {
    const f = await this.reviews.file(viewer, id, this.meta(req, res));
    res.setHeader("Content-Type", f.mimeType);
    res.setHeader("Content-Disposition", `attachment; filename="identity-${id.slice(0, 8)}.${f.mimeType === "application/pdf" ? "pdf" : f.mimeType === "image/png" ? "png" : "jpg"}"`);
    res.setHeader("Content-Security-Policy", "sandbox; default-src 'none'");
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.send(f.data);
  }

  @Post("identity-reviews/:id/decision")
  @StaffOnly("SUPER_ADMIN", "VERIFICATION_OFFICER")
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: "Approve or reject a manual identity review (HIGH risk needs a second reviewer)" })
  async decide(@CurrentViewer() viewer: Viewer, @Param("id", new ParseUUIDPipe()) id: string, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.reviews.decide(viewer, id, parse(DecisionBody, body), this.meta(req, res)) };
  }
}
