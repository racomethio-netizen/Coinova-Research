import { Controller, Get, Header, HttpStatus, Inject, Param, Req } from "@nestjs/common";
import { ApiGoneResponse, ApiNotFoundResponse, ApiOkResponse, ApiOperation, ApiParam, ApiTags } from "@nestjs/swagger";
import type { Request } from "express";
import { ApiError, clientIp } from "../common/http.js";
import { CryptoService } from "../security/crypto.service.js";
import { RULES, RateLimiter } from "../security/rate-limit.js";
import { VerifyService } from "./verify.service.js";

const DISCLAIMER =
  "TAMAGNE is an independent private platform, not a government institution. Verification confirms identity or documents at a point in time; it does not guarantee honesty, product authenticity or delivery.";

@ApiTags("verify")
@Controller({ path: "verify", version: "1" })
export class VerifyController {
  constructor(
    @Inject(VerifyService) private readonly verify: VerifyService,
    @Inject(RateLimiter) private readonly limiter: RateLimiter,
    @Inject(CryptoService) private readonly crypto: CryptoService,
  ) {}

  @Get(":sellerId")
  // Status can change at any moment (suspension, revocation): never cache.
  @Header("Cache-Control", "no-store")
  @ApiOperation({ summary: "Public verification status by Tamagne ID or QR token" })
  @ApiParam({ name: "sellerId", description: "Tamagne ID (e.g. TMG-7K3QX9M2BD) or QR token" })
  @ApiOkResponse({ description: "Public verification summary (approved fields only)" })
  @ApiNotFoundResponse({ description: "No public seller with this ID" })
  @ApiGoneResponse({ description: "QR code has been revoked" })
  async lookup(@Param("sellerId") sellerId: string, @Req() req: Request) {
    await this.limiter.consume(RULES.verifyPerIp, this.crypto.hash("ip", clientIp(req)));
    if (sellerId.length > 64) throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "No public seller was found with this ID.");
    const result = await this.verify.lookup(sellerId);
    if (result.kind === "qr_revoked") {
      throw new ApiError(HttpStatus.GONE, "qr_revoked", "This QR code is no longer valid. Ask the seller for their current code or Tamagne ID.");
    }
    if (result.kind === "not_found") throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "No public seller was found with this ID.");
    return { data: result.seller, meta: { disclaimer: DISCLAIMER } };
  }
}
