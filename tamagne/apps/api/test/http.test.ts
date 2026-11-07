import type { INestApplication } from "@nestjs/common";
import request from "supertest";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createApp } from "../src/app.factory.js";
import { loadConfig } from "../src/config/env.js";

const key = (n: number) => Buffer.alloc(32, n).toString("base64");
const baseEnv = {
  APP_ENV: "test",
  DATABASE_URL: process.env.TEST_DATABASE_URL ?? "postgresql://postgres@127.0.0.1:54339/tamagne_test",
  FIELD_ENCRYPTION_KEYS: `1:${key(1)}`,
  FIELD_ENCRYPTION_ACTIVE_KEY_ID: "1",
  HMAC_PEPPER: key(2),
  CORS_ORIGINS: "http://localhost:3000",
  OPENAPI_ENABLED: "true",
};

describe("HTTP surface", () => {
  let app: INestApplication;
  beforeAll(async () => {
    app = await createApp(loadConfig(baseEnv));
    await app.init();
  });
  afterAll(async () => {
    await app.close();
  });

  it("liveness responds with the data envelope and security headers", async () => {
    const res = await request(app.getHttpServer()).get("/api/v1/health").expect(200);
    expect(res.body).toEqual({ data: { status: "ok" } });
    expect(res.headers["x-powered-by"]).toBeUndefined();
    expect(res.headers["x-content-type-options"]).toBe("nosniff");
    expect(res.headers["content-security-policy"]).toContain("default-src 'none'");
    expect(res.headers["x-request-id"]).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("readiness checks the database (requires the test database: npm run db:test:up)", async () => {
    const res = await request(app.getHttpServer()).get("/api/v1/health/ready");
    expect(res.status).toBe(200);
    expect(res.body.data.checks.database).toBe("ok");
  });

  it("Fayda reports disabled without exposing configuration", async () => {
    const res = await request(app.getHttpServer()).get("/api/v1/fayda/status").expect(200);
    expect(res.body.data).toMatchObject({ enabled: false, mode: "disabled", manualReviewAvailable: true });
    expect(JSON.stringify(res.body)).not.toMatch(/issuer|client|jwk/i);
  });

  it("unknown routes return the error envelope", async () => {
    const res = await request(app.getHttpServer()).get("/api/v1/nope").expect(404);
    expect(res.body.error).toMatchObject({ code: "not_found" });
    expect(res.body.error.requestId).toBe(res.headers["x-request-id"]);
  });

  it("rejects oversized and malformed JSON without leaking parser details", async () => {
    const big = await request(app.getHttpServer())
      .post("/api/v1/health")
      .set("content-type", "application/json")
      .send(JSON.stringify({ x: "a".repeat(200_000) }));
    expect(big.status).toBe(413);
    expect(big.body.error.code).toBe("payload_too_large");
    const bad = await request(app.getHttpServer()).post("/api/v1/health").set("content-type", "application/json").send("{bad");
    expect(bad.status).toBe(400);
    expect(bad.body.error.message).toBe("Malformed request body.");
  });

  it("honours only well-formed upstream request IDs", async () => {
    const ok = await request(app.getHttpServer()).get("/api/v1/health").set("x-request-id", "edge-12345678");
    expect(ok.headers["x-request-id"]).toBe("edge-12345678");
    const evil = await request(app.getHttpServer()).get("/api/v1/health").set("x-request-id", "<script>alert(1)</script>");
    expect(evil.headers["x-request-id"]).not.toContain("<");
  });

  it("CORS allows only configured origins", async () => {
    const allowed = await request(app.getHttpServer()).options("/api/v1/health").set("origin", "http://localhost:3000").set("access-control-request-method", "GET");
    expect(allowed.headers["access-control-allow-origin"]).toBe("http://localhost:3000");
    const denied = await request(app.getHttpServer()).options("/api/v1/health").set("origin", "https://evil.example").set("access-control-request-method", "GET");
    expect(denied.headers["access-control-allow-origin"]).toBeUndefined();
  });

  it("serves the OpenAPI document when enabled", async () => {
    const res = await request(app.getHttpServer()).get("/api/docs/openapi.json").expect(200);
    expect(res.body.info.title).toBe("TAMAGNE API");
    expect(Object.keys(res.body.paths)).toEqual(expect.arrayContaining(["/api/v1/health", "/api/v1/health/ready", "/api/v1/fayda/status"]));
  });
});

describe("readiness without a database", () => {
  it("returns 503 when the database is unreachable", async () => {
    const app = await createApp(loadConfig({ ...baseEnv, DATABASE_URL: "postgresql://nobody@127.0.0.1:1/none", OPENAPI_ENABLED: "false" }));
    await app.init();
    try {
      const res = await request(app.getHttpServer()).get("/api/v1/health/ready").expect(503);
      expect(res.body.data).toEqual({ status: "not_ready", checks: { database: "unavailable" } });
      await request(app.getHttpServer()).get("/api/docs/openapi.json").expect(404);
    } finally {
      await app.close();
    }
  });
});
