import { Body, Controller, Get, HttpCode, HttpStatus, Inject, Patch, Post, Req, Res } from "@nestjs/common";
import { ApiConflictResponse, ApiCreatedResponse, ApiOperation, ApiTags } from "@nestjs/swagger";
import type { Request, Response } from "express";
import { z } from "zod";
import { clientIp, parse } from "../common/http.js";
import { Authenticated, CurrentViewer } from "../auth/guards.js";
import type { Viewer } from "../auth/session.service.js";
import { CryptoService } from "../security/crypto.service.js";
import { SellersService } from "./sellers.service.js";

/** Collapses whitespace and strips control characters; rejects markup-like input. */
const text = (min: number, max: number) =>
  z
    .string()
    // eslint-disable-next-line no-control-regex -- stripping control characters is the point
    .transform((s) => s.replace(/[\u0000-\u001f\u007f]/g, "").replace(/\s+/g, " ").trim())
    .pipe(z.string().min(min).max(max).refine((s) => !/[<>]/.test(s), "must not contain < or >"));

const SellerType = z.enum(["INDIVIDUAL", "SOCIAL_MEDIA_SELLER", "TELEGRAM_SHOP", "REGISTERED_BUSINESS", "ECOMMERCE_COMPANY", "ONLINE_SERVICE_PROVIDER"]);
const optionalText = (max: number) => z.union([text(1, max), z.literal("").transform(() => null), z.null()]).optional();

const CreateSeller = z.strictObject({
  displayName: text(2, 120),
  displayNameAm: optionalText(120),
  sellerType: SellerType,
  region: optionalText(80),
  city: optionalText(80),
});
const UpdateSeller = CreateSeller.partial();

@ApiTags("sellers")
@Controller({ path: "sellers", version: "1" })
export class SellersController {
  constructor(
    @Inject(SellersService) private readonly sellers: SellersService,
    @Inject(CryptoService) private readonly crypto: CryptoService,
  ) {}

  private meta(req: Request, res: Response) {
    return { ipHash: this.crypto.hash("ip", clientIp(req)), requestId: String(res.locals.requestId) };
  }

  @Post()
  @Authenticated()
  @HttpCode(HttpStatus.CREATED)
  @ApiOperation({ summary: "Create the signed-in user's seller profile (issues the Level 1 phone badge)" })
  @ApiCreatedResponse({ description: "Seller profile" })
  @ApiConflictResponse({ description: "Profile already exists" })
  async create(@CurrentViewer() viewer: Viewer, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    const input = parse(CreateSeller, body);
    await this.sellers.create(viewer.userId, input, this.meta(req, res));
    return { data: await this.sellers.getForUser(viewer.userId) };
  }

  @Get("me")
  @Authenticated()
  @ApiOperation({ summary: "The signed-in seller's profile and verification status" })
  async me(@CurrentViewer() viewer: Viewer) {
    return { data: await this.sellers.getForUser(viewer.userId) };
  }

  @Patch("me")
  @Authenticated()
  @ApiOperation({ summary: "Update profile details (not verification status)" })
  async update(@CurrentViewer() viewer: Viewer, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    const input = parse(UpdateSeller, body);
    return { data: await this.sellers.update(viewer.userId, input, this.meta(req, res)) };
  }
}
