import { Body, Controller, Get, HttpCode, HttpStatus, Inject, Param, Post, Res } from "@nestjs/common";
import { ApiConflictResponse, ApiOperation, ApiParam, ApiTags } from "@nestjs/swagger";
import type { Response } from "express";
import { z } from "zod";
import { ApiError, parse } from "../common/http.js";
import { Authenticated, CurrentViewer, StaffOnly } from "../auth/guards.js";
import type { Viewer } from "../auth/session.service.js";
import { PrismaService } from "../database/prisma.service.js";
import { QrService } from "./qr.service.js";

@ApiTags("sellers")
@Controller({ path: "sellers", version: "1" })
export class SellerQrController {
  constructor(
    @Inject(QrService) private readonly qr: QrService,
    @Inject(PrismaService) private readonly prisma: PrismaService,
  ) {}

  @Get(":sellerId/qr")
  @Authenticated()
  @ApiOperation({ summary: "The signed-in seller's QR code (sellerId: 'me' or own Tamagne ID)" })
  @ApiParam({ name: "sellerId", description: "'me' or your own Tamagne ID" })
  @ApiConflictResponse({ description: "Profile is not public" })
  async get(@CurrentViewer() viewer: Viewer, @Param("sellerId") sellerId: string, @Res({ passthrough: true }) res: Response) {
    if (sellerId !== "me") {
      // Ownership check: another seller's ID looks exactly like an unknown one.
      const own = await this.prisma.client.sellerProfile.findUnique({ where: { userId: viewer.userId }, select: { tamagneId: true } });
      if (!own || own.tamagneId !== sellerId.toUpperCase()) throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "Not found.");
    }
    res.setHeader("Cache-Control", "no-store");
    return { data: await this.qr.getOrCreate(viewer.userId, { requestId: String(res.locals.requestId) }) };
  }

  @Post("me/qr/rotate")
  @Authenticated()
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: "Revoke the current QR code and issue a new one" })
  async rotate(@CurrentViewer() viewer: Viewer, @Res({ passthrough: true }) res: Response) {
    return { data: await this.qr.rotate(viewer.userId, { requestId: String(res.locals.requestId) }) };
  }
}

const RevokeBody = z.strictObject({ reason: z.string().trim().min(5).max(500) });

@ApiTags("admin")
@Controller({ path: "admin/sellers", version: "1" })
export class AdminQrController {
  constructor(@Inject(QrService) private readonly qr: QrService) {}

  @Post(":tamagneId/qr/revoke")
  @StaffOnly("SUPER_ADMIN", "VERIFICATION_OFFICER", "SECURITY_OFFICER")
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: "Revoke a seller's active QR code (with a reason)" })
  async revoke(@CurrentViewer() viewer: Viewer, @Param("tamagneId") tamagneId: string, @Body() body: unknown, @Res({ passthrough: true }) res: Response) {
    const { reason } = parse(RevokeBody, body);
    return { data: await this.qr.revokeByStaff(tamagneId.toUpperCase(), viewer.userId, reason, { requestId: String(res.locals.requestId) }) };
  }
}
