import { Controller, Get, Inject } from "@nestjs/common";
import { ApiOkResponse, ApiTags } from "@nestjs/swagger";
import { APP_CONFIG } from "../config/config.module.js";
import type { AppConfig } from "../config/env.js";

/**
 * Reports whether Fayda eSignet authentication is available, so the UI can show
 * the manual-review path instead. Exposes the mode only; never issuer details,
 * client IDs or keys. The OIDC flow itself (start/callback) is Phase 5.
 */
@ApiTags("fayda")
@Controller({ path: "fayda", version: "1" })
export class FaydaStatusController {
  constructor(@Inject(APP_CONFIG) private readonly config: AppConfig) {}

  @Get("status")
  @ApiOkResponse({ description: "Fayda integration availability" })
  status() {
    const f = this.config.fayda;
    return {
      data: f.enabled
        ? { enabled: true, mode: f.mode, manualReviewAvailable: true }
        : { enabled: false, mode: "disabled" as const, manualReviewAvailable: true, reason: f.reason },
    };
  }
}
