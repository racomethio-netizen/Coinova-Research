import "reflect-metadata";
import { VersioningType, type INestApplication } from "@nestjs/common";
import { NestFactory } from "@nestjs/core";
import type { NestExpressApplication } from "@nestjs/platform-express";
import { DocumentBuilder, SwaggerModule, type OpenAPIObject } from "@nestjs/swagger";
import express from "express";
import helmet from "helmet";
import { AppModule, type AppOverrides } from "./app.module.js";
import type { AppConfig } from "./config/env.js";
import { ApiExceptionFilter, bodyParserErrorHandler, requestIdMiddleware } from "./http/errors.js";
import { SessionService } from "./auth/session.service.js";
import { csrfMiddleware } from "./security/csrf.js";
import { JsonLogger, accessLogMiddleware } from "./observability/logger.js";
import { MetricsService } from "./observability/metrics.js";

/** JSON bodies are small; documents are uploaded as multipart in Phase 4 with their own limit. */
const JSON_BODY_LIMIT = "100kb";

export async function createApp(config: AppConfig, overrides: AppOverrides = {}): Promise<INestApplication> {
  const jsonLogger = config.logFormat === "json" ? new JsonLogger() : undefined;
  const app = await NestFactory.create<NestExpressApplication>(AppModule.forRoot(config, overrides), {
    bodyParser: false,
    logger: config.appEnv === "test" ? false : (jsonLogger ?? ["log", "warn", "error"]),
  });

  if (config.trustProxyHops > 0) app.set("trust proxy", config.trustProxyHops);
  app.disable("x-powered-by");
  app.use(requestIdMiddleware);
  app.use(app.get(MetricsService).middleware());
  if (jsonLogger && config.appEnv !== "test") app.use(accessLogMiddleware((entry) => jsonLogger.log(entry, "http")));
  app.use(
    helmet({
      // The API serves JSON only; lock everything down.
      contentSecurityPolicy: { directives: { defaultSrc: ["'none'"], frameAncestors: ["'none'"] } },
      strictTransportSecurity: config.isProduction ? { maxAge: 63072000, includeSubDomains: true, preload: true } : false,
      referrerPolicy: { policy: "no-referrer" },
    }),
  );
  app.use(express.json({ limit: JSON_BODY_LIMIT }));
  app.use(bodyParserErrorHandler);
  // CSRF applies to every state-changing request; allowed origins are the web app's.
  app.use(csrfMiddleware([config.publicWebUrl, ...config.corsOrigins], app.get(SessionService).csrfCookieName));
  app.enableCors({
    origin: config.corsOrigins,
    credentials: true,
    methods: ["GET", "POST", "PATCH", "DELETE"],
    allowedHeaders: ["Content-Type", "X-Request-Id", "X-CSRF-Token"],
    maxAge: 600,
  });
  app.setGlobalPrefix("api");
  app.enableVersioning({ type: VersioningType.URI, defaultVersion: "1" });
  app.useGlobalFilters(new ApiExceptionFilter());
  app.enableShutdownHooks();

  if (config.openApiEnabled) {
    SwaggerModule.setup("api/docs", app, buildOpenApiDocument(app), { jsonDocumentUrl: "api/docs/openapi.json" });
  }
  return app;
}

export function buildOpenApiDocument(app: INestApplication): OpenAPIObject {
  const doc = new DocumentBuilder()
    .setTitle("TAMAGNE API")
    .setDescription(
      "Seller identity and business verification for Ethiopia. TAMAGNE is an independent private platform, not a government institution. " +
        "Verification confirms identity or documents at a point in time; it does not guarantee honesty, product authenticity or delivery.",
    )
    .setVersion("1")
    .addCookieAuth("tamagne_session")
    .addApiKey({ type: "apiKey", in: "header", name: "X-Api-Key" }, "marketplace")
    .build();
  return SwaggerModule.createDocument(app, doc);
}
