import { Controller, Get, HttpStatus, Inject, Injectable, Req, Res } from "@nestjs/common";
import { ApiExcludeController } from "@nestjs/swagger";
import { timingSafeEqual, createHash } from "node:crypto";
import type { NextFunction, Request, Response } from "express";
import { Counter, Histogram, Registry, collectDefaultMetrics } from "prom-client";
import { ApiError } from "../common/http.js";
import { APP_CONFIG } from "../config/config.module.js";
import type { AppConfig } from "../config/env.js";

/**
 * Prometheus metrics. Labels are low-cardinality and never personal: route templates
 * (e.g. /api/v1/verify/:sellerId), methods, status codes, rule and job names.
 */
@Injectable()
export class MetricsService {
  readonly registry = new Registry();
  readonly httpRequests = new Counter({
    name: "tamagne_http_requests_total",
    help: "HTTP requests by route template and status",
    labelNames: ["method", "route", "status"] as const,
    registers: [this.registry],
  });
  readonly httpDuration = new Histogram({
    name: "tamagne_http_request_duration_seconds",
    help: "HTTP request duration by route template",
    labelNames: ["method", "route"] as const,
    buckets: [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10],
    registers: [this.registry],
  });
  readonly rateLimited = new Counter({
    name: "tamagne_rate_limited_total",
    help: "Requests rejected by a rate-limit rule",
    labelNames: ["rule"] as const,
    registers: [this.registry],
  });
  readonly rateLimitStoreErrors = new Counter({
    name: "tamagne_rate_limit_store_errors_total",
    help: "Rate-limit store failures (requests fail closed)",
    registers: [this.registry],
  });
  readonly auditWriteFailures = new Counter({
    name: "tamagne_audit_write_failures_total",
    help: "Audit entries that could not be written (recordSafe)",
    registers: [this.registry],
  });

  constructor() {
    collectDefaultMetrics({ register: this.registry, prefix: "tamagne_" });
  }

  /** Express middleware recording every response once it finishes. */
  middleware() {
    return (req: Request, res: Response, next: NextFunction) => {
      const start = process.hrtime.bigint();
      res.on("finish", () => {
        const route = routeTemplate(req);
        const seconds = Number(process.hrtime.bigint() - start) / 1e9;
        this.httpRequests.inc({ method: req.method, route, status: String(res.statusCode) });
        this.httpDuration.observe({ method: req.method, route }, seconds);
      });
      next();
    };
  }
}

/** The matched route pattern, never the concrete URL (which may carry IDs or tokens). */
export function routeTemplate(req: Request): string {
  const path = (req.route as { path?: unknown } | undefined)?.path;
  return typeof path === "string" ? `${req.baseUrl}${path}` : "(unmatched)";
}

function sameSecret(given: string, expected: string): boolean {
  const a = createHash("sha256").update(given).digest();
  const b = createHash("sha256").update(expected).digest();
  return timingSafeEqual(a, b);
}

/** GET /api/v1/metrics, bearer-token protected; 404 when METRICS_TOKEN is not configured. */
@ApiExcludeController()
@Controller({ path: "metrics", version: "1" })
export class MetricsController {
  constructor(
    @Inject(MetricsService) private readonly metrics: MetricsService,
    @Inject(APP_CONFIG) private readonly config: AppConfig,
  ) {}

  @Get()
  async scrape(@Req() req: Request, @Res() res: Response) {
    const token = this.config.metricsToken;
    if (!token) throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "Not found.");
    const auth = req.header("authorization") ?? "";
    const given = auth.startsWith("Bearer ") ? auth.slice(7) : "";
    if (!given || !sameSecret(given, token)) throw new ApiError(HttpStatus.UNAUTHORIZED, "unauthorized", "Authentication required.");
    res.setHeader("Content-Type", this.metrics.registry.contentType);
    res.setHeader("Cache-Control", "no-store");
    res.status(HttpStatus.OK).send(await this.metrics.registry.metrics());
  }
}
