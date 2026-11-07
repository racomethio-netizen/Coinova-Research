import { Body, Controller, Get, HttpCode, HttpStatus, Inject, Post, Query, Req, Res } from "@nestjs/common";
import { ApiOkResponse, ApiOperation, ApiTags } from "@nestjs/swagger";
import type { Request, Response } from "express";
import { z } from "zod";
import { clientIp, parse } from "../common/http.js";
import { APP_CONFIG } from "../config/config.module.js";
import type { AppConfig } from "../config/env.js";
import { Authenticated, CurrentViewer } from "../auth/guards.js";
import { SessionService, type Viewer } from "../auth/session.service.js";
import { CryptoService } from "../security/crypto.service.js";
import { FaydaService } from "./fayda.service.js";

const StartBody = z.strictObject({ acceptConsent: z.boolean().default(false), locale: z.enum(["en", "am"]).default("en") });

/**
 * Fayda eSignet (OIDC). Status is public; start and callback require the seller's session.
 * Production use requires official NIDP authorization (docs/FAYDA_INTEGRATION.md).
 */
@ApiTags("fayda")
@Controller({ path: "fayda", version: "1" })
export class FaydaStatusController {
  constructor(
    @Inject(APP_CONFIG) private readonly config: AppConfig,
    @Inject(FaydaService) private readonly fayda: FaydaService,
    @Inject(SessionService) private readonly sessions: SessionService,
    @Inject(CryptoService) private readonly crypto: CryptoService,
  ) {}

  private meta(req: Request, res: Response) {
    return { requestId: String(res.locals.requestId), ipHash: this.crypto.hash("ip", clientIp(req)) };
  }

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

  @Post("start")
  @Authenticated()
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: "Record consent and get the Fayda eSignet authorization URL (state, nonce, PKCE S256)" })
  async start(@CurrentViewer() viewer: Viewer, @Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    const input = parse(StartBody, body);
    return { data: await this.fayda.start(viewer, input.acceptConsent, input.locale, this.meta(req, res)) };
  }

  /** eSignet redirects the browser here; we always answer with a redirect back to the web app. */
  @Get("callback")
  @ApiOperation({ summary: "OIDC redirect target (browser only); redirects to the identity page with the result" })
  async callback(@Query() query: Record<string, unknown>, @Req() req: Request, @Res() res: Response) {
    const web = this.config.publicWebUrl;
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("Referrer-Policy", "no-referrer"); // keep the code out of any Referer
    const viewer = await this.sessions.resolve(req);
    if (!viewer) return res.redirect(303, new URL("/en/login?fayda=session_missing", web).toString());
    const str = (k: string) => (typeof query[k] === "string" ? (query[k] as string) : undefined);
    const result = await this.fayda.callback(viewer, { state: str("state"), code: str("code"), error: str("error") }, this.meta(req, res));
    const lang = viewer.locale === "AM" ? "am" : "en";
    const target = new URL(`/${lang}/dashboard/identity`, web);
    target.searchParams.set("fayda", result.ok ? "verified" : "failed");
    if (!result.ok) target.searchParams.set("reason", result.code);
    return res.redirect(303, target.toString());
  }
}
