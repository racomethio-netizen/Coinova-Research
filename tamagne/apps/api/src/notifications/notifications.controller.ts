import { Controller, Get, HttpCode, HttpStatus, Inject, Param, ParseUUIDPipe, Post } from "@nestjs/common";
import { ApiOperation, ApiTags } from "@nestjs/swagger";
import { Authenticated, CurrentViewer } from "../auth/guards.js";
import type { Viewer } from "../auth/session.service.js";
import { PrismaService } from "../database/prisma.service.js";
import { OperationsService } from "../admin/operations.service.js";

/** In-app notifications. Payloads hold IDs and levels only; the web app renders the text in EN/AM. */
@ApiTags("notifications")
@Controller({ path: "notifications", version: "1" })
export class NotificationsController {
  constructor(@Inject(PrismaService) private readonly prisma: PrismaService) {}

  @Get()
  @Authenticated()
  @ApiOperation({ summary: "The signed-in user's latest in-app notifications" })
  async list(@CurrentViewer() viewer: Viewer) {
    const rows = await this.prisma.client.notification.findMany({ where: { userId: viewer.userId, channel: "IN_APP" }, orderBy: { createdAt: "desc" }, take: 30 });
    return {
      data: rows.map((n) => ({ id: n.id, templateKey: n.templateKey, payload: n.payload, read: n.readAt !== null, createdAt: n.createdAt.toISOString() })),
    };
  }

  @Post(":id/read")
  @Authenticated()
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: "Mark a notification as read" })
  async read(@CurrentViewer() viewer: Viewer, @Param("id", new ParseUUIDPipe()) id: string) {
    // Scoped to the viewer: another user's notification simply does not match.
    const r = await this.prisma.client.notification.updateMany({ where: { id, userId: viewer.userId, readAt: null }, data: { readAt: new Date(), status: "READ" } });
    return { data: { updated: r.count } };
  }
}

@ApiTags("plans")
@Controller({ path: "plans", version: "1" })
export class PublicPlansController {
  constructor(@Inject(OperationsService) private readonly ops: OperationsService) {}

  @Get()
  @ApiOperation({ summary: "Active plans and current prices (set by administrators; prices never affect verification)" })
  async list() {
    const plans = await this.ops.plans(false);
    return { data: plans.map(({ id, code, kind, nameEn, nameAm, currentPrices }) => ({ id, code, kind, nameEn, nameAm, currentPrices })) };
  }
}
