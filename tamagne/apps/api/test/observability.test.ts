import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { createFieldCipher, parseKeyRing } from "@tamagne/core";
import pg from "pg";
import request from "supertest";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { JsonLogger, accessLogMiddleware, redactLogText } from "../src/observability/logger.js";
import { RekeyService } from "../src/security/rekey.service.js";
import { OBJECT_STORE, type EncryptedObjectStore } from "../src/storage/storage.js";
import { Client, fakePhone, staffClient, startApp, testEnv, type TestHarness } from "./support.js";

const TOKEN = "m".repeat(40);
let h: TestHarness & { storageDir: string };
beforeAll(async () => {
  h = await startApp({ METRICS_TOKEN: TOKEN });
});
afterAll(async () => {
  await h.close();
});

describe("metrics endpoint", () => {
  it("requires the bearer token and exposes route templates, never concrete IDs", async () => {
    await request(h.url).get("/api/v1/verify/TMG-ZZZZZZZZZZ");
    expect((await request(h.url).get("/api/v1/metrics")).status).toBe(401);
    expect((await request(h.url).get("/api/v1/metrics").set("Authorization", `Bearer ${"x".repeat(40)}`)).status).toBe(401);
    const res = await request(h.url).get("/api/v1/metrics").set("Authorization", `Bearer ${TOKEN}`);
    expect(res.status).toBe(200);
    expect(res.headers["content-type"]).toContain("text/plain");
    expect(res.text).toContain('tamagne_http_requests_total{method="GET",route="/api/v1/verify/:sellerId",status="404"}');
    expect(res.text).toContain("tamagne_process_cpu_seconds_total");
    expect(res.text).not.toContain("TMG-ZZZZZZZZZZ");
  });

  it("counts rate-limit rejections by rule", async () => {
    const limited = await startApp({ METRICS_TOKEN: TOKEN, VERIFY_LOOKUP_LIMIT_PER_MIN: "10" });
    try {
      for (let i = 0; i < 12; i++) await request(limited.url).get("/api/v1/verify/TMG-ZZZZZZZZZZ");
      const text = (await request(limited.url).get("/api/v1/metrics").set("Authorization", `Bearer ${TOKEN}`)).text;
      expect(text).toContain('tamagne_rate_limited_total{rule="verify-ip"} 2');
    } finally {
      await limited.close();
    }
  });

  it("answers 404 when no token is configured", async () => {
    const plain = await startApp();
    try {
      expect((await request(plain.url).get("/api/v1/metrics").set("Authorization", `Bearer ${TOKEN}`)).status).toBe(404);
    } finally {
      await plain.close();
    }
  });
});

describe("structured logs", () => {
  it("writes one JSON object per line with level and context, masking digit runs", () => {
    const out: string[] = [];
    const err: string[] = [];
    const log = new JsonLogger(undefined, (l) => out.push(l), (l) => err.push(l));
    log.log("TAMAGNE API listening on :4000", "bootstrap");
    log.warn("sms to +251 911 234 567 code 482913 failed", "sms");
    log.error("boom", "Error: secret input value\n    at foo (a.js:1:1)\n    at bar (b.js:2:2)", "http");
    log.debug("not enabled", "x");
    expect(out).toHaveLength(2);
    expect(JSON.parse(out[0]!)).toMatchObject({ level: "info", context: "bootstrap", msg: "TAMAGNE API listening on :4000" });
    const warn = JSON.parse(out[1]!);
    expect(warn.msg).toBe("sms to [redacted-number] code [redacted-code] failed");
    const e = JSON.parse(err[0]!);
    expect(e).toMatchObject({ level: "error", context: "http", msg: "boom" });
    expect(e.stack).toContain("at foo");
    expect(e.stack).not.toContain("secret input value");
  });

  it("does not mask dates, ports or IDs", () => {
    expect(redactLogText("2026-11-09 port 4000 TMG-7K3QX9M2BD 3f2a1b4c-0000-4000-8000-000000000001")).toBe(
      "2026-11-09 port 4000 TMG-7K3QX9M2BD 3f2a1b4c-0000-4000-8000-000000000001",
    );
  });

  it("access log records the route template, status and request ID only", async () => {
    const lines: Record<string, unknown>[] = [];
    const express = (await import("express")).default;
    const app = express();
    app.use((_req, res, next) => {
      res.locals.requestId = "req-1";
      next();
    });
    app.use(accessLogMiddleware((e) => lines.push(e)));
    app.get("/api/v1/verify/:id", (_req, res) => {
      res.status(404).json({});
    });
    await request(app).get("/api/v1/verify/TMG-SECRETID?q=private");
    expect(lines).toHaveLength(1);
    expect(lines[0]).toMatchObject({ msg: "request", method: "GET", route: "/api/v1/verify/:id", status: 404, requestId: "req-1" });
    expect(JSON.stringify(lines[0])).not.toMatch(/SECRETID|private/);
  });
});

/**
 * Key rotation rewrites every encrypted row, so it runs in its own database: on the shared test
 * database it would make concurrently running test files unable to read their own rows.
 */
async function isolatedDatabase() {
  const base = new URL(testEnv.DATABASE_URL);
  const name = `tamagne_rekey_${process.pid}_${Date.now()}`;
  const admin = new pg.Client({ connectionString: Object.assign(new URL(base), { pathname: "/postgres" }).toString() });
  await admin.connect();
  await admin.query(`create database ${name}`);
  const url = Object.assign(new URL(base), { pathname: `/${name}` }).toString();
  const c = new pg.Client({ connectionString: url });
  await c.connect();
  const dir = join(import.meta.dirname, "../../../packages/db/prisma/migrations");
  for (const m of readdirSync(dir).filter((d) => /^\d/.test(d)).sort()) await c.query(readFileSync(join(dir, m, "migration.sql"), "utf8"));
  await c.end();
  return {
    url,
    async drop() {
      await admin.query(`drop database ${name} with (force)`);
      await admin.end();
    },
  };
}

describe("key rotation job", () => {
  it("re-encrypts fields and documents under the new key; the old key can then be retired", async () => {
    const iso = await isolatedDatabase();
    const outer = h;
    h = await startApp({ DATABASE_URL: iso.url });
    try {
      await rotationScenario(iso.url);
    } finally {
      await h.close();
      h = outer;
      await iso.drop();
    }
  });
});

async function rotationScenario(databaseUrl: string) {
    // Data written under key 1.
    const seller = new Client(h);
    await seller.signIn(fakePhone());
    const s = (await seller.post("/api/v1/sellers", { displayName: "Fixture Rekey Shop", sellerType: "REGISTERED_BUSINESS", city: "Adama" })).body.data;
    await seller.post("/api/v1/businesses", { legalName: "Fixture Rekey PLC", legalForm: "OTHER", tinNumber: String(1_000_000_000 + Math.floor(Math.random() * 8e9)) });
    await seller.post("/api/v1/consents", { purpose: "BUSINESS_DOCUMENT_PROCESSING" });
    const pdf = Buffer.from("%PDF-1.7\n% rekey (synthetic fixture)\n%%EOF\n");
    const up = await seller.upload("/api/v1/businesses/documents", { type: "BUSINESS_LICENSE" }, { data: pdf, filename: "l.pdf", contentType: "application/pdf" });
    expect(up.status).toBe(201);
    const doc = await h.db.businessDocument.findUniqueOrThrow({ where: { id: up.body.data.documents[0].id } });
    const report = await new Client(h).post("/api/v1/reports", {
      seller: s.tamagneId,
      category: "NON_DELIVERY",
      description: "Paid by transfer; nothing delivered after 3 weeks (rekey fixture).",
      contact: "rekey@example.test",
    });
    expect(report.status).toBe(202);
    const { userId: staffId } = await staffClient(h, "SECURITY_OFFICER");
    const factor = await h.db.mfaFactor.findFirstOrThrow({ where: { userId: staffId } });
    expect(factor.secretEnc[1]).toBe(1);

    // New deployment: key 2 active, key 1 still in the ring.
    const k = (n: number) => Buffer.alloc(32, n).toString("base64");
    const rotated = await startApp({ FIELD_ENCRYPTION_KEYS: `1:${k(1)},2:${k(9)}`, FIELD_ENCRYPTION_ACTIVE_KEY_ID: "2", STORAGE_LOCAL_DIR: h.storageDir, DATABASE_URL: databaseUrl });
    try {
      const result = await rotated.app.get(RekeyService).runRekeyJob();
      expect(result.mfaFactorsRotated).toBeGreaterThanOrEqual(1);
      expect(result.objectsRotated).toBeGreaterThanOrEqual(1);
      expect(result.remaining).toBe(0);
      const again = await rotated.app.get(RekeyService).runRekeyJob();
      expect(again.mfaFactorsRotated + again.objectsRotated + again.reporterContactsRotated).toBe(0); // idempotent

      // Readable with key 2 alone.
      const onlyNew = createFieldCipher({ keys: parseKeyRing(`2:${k(9)}`), activeKeyId: 2 });
      const f = await h.db.mfaFactor.findUniqueOrThrow({ where: { id: factor.id } });
      expect(f.secretEnc[1]).toBe(2);
      expect(onlyNew.decrypt(Buffer.from(f.secretEnc), `mfa_factors.secretEnc:${factor.id}`)).toMatch(/^[A-Z2-7]+=*$/);
      const r = await h.db.fraudReport.findFirstOrThrow({ where: { reporterContactEnc: { not: null } } });
      expect(onlyNew.decrypt(Buffer.from(r.reporterContactEnc!), `fraud_reports.reporterContactEnc:${r.id}`)).toBe("rekey@example.test");
      const store = rotated.app.get<EncryptedObjectStore>(OBJECT_STORE);
      const blob = await store.inner.get(doc.storageKey);
      expect(blob[1]).toBe(2);
      expect((await store.get(doc.storageKey)).equals(pdf)).toBe(true);

      const audit = await h.db.auditLog.findFirstOrThrow({ where: { action: "job.rekey" }, orderBy: { seq: "desc" } });
      expect(audit.metadata).toHaveProperty("remaining");
      expect(JSON.stringify(audit.metadata)).not.toContain("rekey@example.test");
    } finally {
      await rotated.close();
    }
}
