import { Controller, Get, HttpCode, HttpStatus, Inject, Res } from "@nestjs/common";
import { ApiOkResponse, ApiServiceUnavailableResponse, ApiTags } from "@nestjs/swagger";
import type { Response } from "express";
import { PrismaService } from "../database/prisma.service.js";

@ApiTags("health")
@Controller({ path: "health", version: "1" })
export class HealthController {
  // Explicit @Inject: DI must not depend on emitted decorator metadata (tsx/esbuild do not emit it).
  constructor(@Inject(PrismaService) private readonly prisma: PrismaService) {}

  /** Liveness: the process is up. Never touches dependencies. */
  @Get()
  @HttpCode(HttpStatus.OK)
  @ApiOkResponse({ description: "Process is alive" })
  live(): { data: { status: "ok" } } {
    return { data: { status: "ok" } };
  }

  /** Readiness: dependencies needed to serve traffic are reachable. */
  @Get("ready")
  @ApiOkResponse({ description: "Ready to serve traffic" })
  @ApiServiceUnavailableResponse({ description: "A dependency is unavailable" })
  async ready(@Res({ passthrough: true }) res: Response) {
    const database = (await this.prisma.isReachable()) ? "ok" : "unavailable";
    const ready = database === "ok";
    res.status(ready ? HttpStatus.OK : HttpStatus.SERVICE_UNAVAILABLE);
    return { data: { status: ready ? "ready" : "not_ready", checks: { database } } };
  }
}
