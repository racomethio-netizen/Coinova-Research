/// <reference types="multer" />
import { Body, Controller, Delete, Get, HttpCode, HttpStatus, Inject, Param, ParseUUIDPipe, Patch, Post, Req, Res, UploadedFile, UseInterceptors } from "@nestjs/common";
import { FileInterceptor } from "@nestjs/platform-express";
import { ApiBody, ApiConsumes, ApiOperation, ApiTags } from "@nestjs/swagger";
import { DOCUMENT_MAX_BYTES, DOCUMENT_TYPES } from "@tamagne/core";
import type { Request, Response } from "express";
import { z } from "zod";
import { ApiError, clientIp, parse } from "../common/http.js";
import { Authenticated, CurrentViewer } from "../auth/guards.js";
import type { Viewer } from "../auth/session.service.js";
import { CryptoService } from "../security/crypto.service.js";
import { RULES, RateLimiter } from "../security/rate-limit.js";
import { BusinessesService } from "./businesses.service.js";

const text = (min: number, max: number) =>
  z
    .string()
    // eslint-disable-next-line no-control-regex -- stripping control characters is the point
    .transform((s) => s.replace(/[\u0000-\u001f\u007f]/g, "").replace(/\s+/g, " ").trim())
    .pipe(z.string().min(min).max(max).refine((s) => !/[<>]/.test(s), "must not contain < or >"));
const optional = <T extends z.ZodType>(t: T) => z.union([t, z.literal("").transform(() => null), z.null()]).optional();

const BusinessBody = z.strictObject({
  legalName: text(2, 200),
  tradeName: optional(text(1, 200)),
  legalForm: z.enum(["SOLE_PROPRIETORSHIP", "PRIVATE_LIMITED_COMPANY", "SHARE_COMPANY", "PARTNERSHIP", "COOPERATIVE", "OTHER"]),
  registrationNumber: optional(z.string().trim().regex(/^[A-Za-z0-9/-]{3,64}$/, "letters, digits, / and - only")),
  tinNumber: optional(z.string().trim().regex(/^\d{10}$/, "10 digits")),
  region: optional(text(1, 80)),
  city: optional(text(1, 80)),
});
const UploadFields = z.strictObject({ type: z.enum(DOCUMENT_TYPES) });

@ApiTags("businesses")
@Controller({ path: "businesses", version: "1" })
export class BusinessesController {
  constructor(
    @Inject(BusinessesService) private readonly businesses: BusinessesService,
    @Inject(CryptoService) private readonly crypto: CryptoService,
    @Inject(RateLimiter) private readonly limiter: RateLimiter,
  ) {}

  private meta(req: Request, res: Response) {
    return { requestId: String(res.locals.requestId), ipHash: this.crypto.hash("ip", clientIp(req)) };
  }

  @Get("me")
  @Authenticated()
  @ApiOperation({ summary: "The signed-in seller's business and document status" })
  async me(@CurrentViewer() viewer: Viewer) {
    return { data: await this.businesses.get(viewer.userId) };
  }

  @Post()
  @Authenticated()
  @ApiOperation({ summary: "Add business details (one business per seller)" })
  async create(@CurrentViewer() viewer: Viewer, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.businesses.create(viewer.userId, parse(BusinessBody, body), this.meta(req, res)) };
  }

  @Patch("me")
  @Authenticated()
  @ApiOperation({ summary: "Edit business details while in draft or after rejection" })
  async update(@CurrentViewer() viewer: Viewer, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.businesses.update(viewer.userId, parse(BusinessBody.partial(), body), this.meta(req, res)) };
  }

  @Post("documents")
  @Authenticated()
  @UseInterceptors(FileInterceptor("file", { limits: { fileSize: DOCUMENT_MAX_BYTES, files: 1, fields: 2, fieldSize: 64, parts: 3 } }))
  @ApiConsumes("multipart/form-data")
  @ApiBody({
    schema: {
      type: "object",
      required: ["type", "file"],
      properties: { type: { type: "string", enum: [...DOCUMENT_TYPES] }, file: { type: "string", format: "binary", description: "PDF, JPEG or PNG, ≤ 10 MB" } },
    },
  })
  @ApiOperation({ summary: "Upload a business document (type-checked, malware-scanned, encrypted at rest)" })
  async upload(
    @CurrentViewer() viewer: Viewer,
    @UploadedFile() file: Express.Multer.File | undefined,
    @Body() body: unknown,
    @Req() req: Request,
    @Res({ passthrough: true }) res: Response,
  ) {
    await this.limiter.consume(RULES.uploadPerUser, viewer.userId);
    const { type } = parse(UploadFields, body);
    if (!file) throw new ApiError(HttpStatus.UNPROCESSABLE_ENTITY, "file_required", "Choose a file to upload.");
    return { data: await this.businesses.upload(viewer.userId, type, file, this.meta(req, res)) };
  }

  @Delete("me/documents/:id")
  @Authenticated()
  @ApiOperation({ summary: "Remove a document before submission" })
  async remove(@CurrentViewer() viewer: Viewer, @Param("id", new ParseUUIDPipe()) id: string, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.businesses.removeDocument(viewer.userId, id, this.meta(req, res)) };
  }

  @Post("me/submit")
  @Authenticated()
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: "Submit the business for review" })
  async submit(@CurrentViewer() viewer: Viewer, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    return { data: await this.businesses.submit(viewer.userId, this.meta(req, res)) };
  }
}
