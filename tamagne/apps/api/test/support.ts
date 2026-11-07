import { randomInt } from "node:crypto";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { base32Decode, totpAt, totpStep } from "@tamagne/core";
import type { INestApplication } from "@nestjs/common";
import request from "supertest";
import type TestAgent from "supertest/lib/agent.js";
import { createApp } from "../src/app.factory.js";
import type { AppOverrides } from "../src/app.module.js";
import { loadConfig } from "../src/config/env.js";
import { PrismaService } from "../src/database/prisma.service.js";
import { MemoryRateLimitStore } from "../src/security/rate-limit.js";
import { TestOutboxSmsSender } from "../src/sms/sms.js";

export const WEB_ORIGIN = "http://localhost:3000";
const key = (n: number) => Buffer.alloc(32, n).toString("base64");

export const testEnv = {
  APP_ENV: "test",
  DATABASE_URL: process.env.TEST_DATABASE_URL ?? "postgresql://postgres@127.0.0.1:54339/tamagne_test",
  FIELD_ENCRYPTION_KEYS: `1:${key(1)}`,
  FIELD_ENCRYPTION_ACTIVE_KEY_ID: "1",
  HMAC_PEPPER: key(2),
  CORS_ORIGINS: WEB_ORIGIN,
  PUBLIC_WEB_URL: WEB_ORIGIN,
  SMS_PROVIDER: "test",
  ALLOW_TEST_PHONE_NUMBERS: "true",
};

/** Synthetic numbers in the unassigned +999 range: never a real person. */
export const fakePhone = () => `+999${String(randomInt(0, 1e9)).padStart(9, "0")}`;

export interface TestHarness {
  app: INestApplication;
  /** Base URL of the app listening on an ephemeral loopback port. */
  url: string;
  sms: TestOutboxSmsSender;
  clock: { now: number };
  db: PrismaService["client"];
  close(): Promise<void>;
}

export async function startApp(env: Record<string, string> = {}, overrides: AppOverrides = {}): Promise<TestHarness & { storageDir: string }> {
  const sms = new TestOutboxSmsSender();
  const clock = { now: Date.now() };
  const storageDir = mkdtempSync(join(tmpdir(), "tamagne-docs-"));
  const app = await createApp(
    loadConfig({ STORAGE_DRIVER: "local", STORAGE_LOCAL_DIR: storageDir, SCANNER: "eicar-only", ...testEnv, ...env }),
    { smsSender: sms, rateLimitStore: new MemoryRateLimitStore(() => clock.now), ...overrides },
  );
  await app.listen(0, "127.0.0.1");
  const url = await app.getUrl();
  return { app, url: url.replace("[::1]", "127.0.0.1"), sms, clock, storageDir, db: app.get(PrismaService).client, close: () => app.close() };
}

/** A browser-like client: keeps cookies, sends Origin and the CSRF header. */
export class Client {
  readonly agent: TestAgent;
  private csrf?: string;
  constructor(private readonly h: TestHarness, private readonly origin = WEB_ORIGIN) {
    this.agent = request.agent(h.url);
  }

  async token(): Promise<string> {
    if (!this.csrf) {
      const res = await this.agent.get("/api/v1/auth/csrf").expect(200);
      this.csrf = res.body.data.csrfToken as string;
    }
    return this.csrf;
  }

  get(path: string) {
    return this.agent.get(path);
  }

  /** Like a browser reading document.cookie: pick up a rotated CSRF cookie from any response. */
  private track<T extends { headers: Record<string, unknown> }>(res: T): T {
    const cookies = (res.headers["set-cookie"] as string[] | undefined) ?? [];
    for (const c of cookies) {
      const m = /^tamagne_csrf=([^;]*)/.exec(c);
      if (m) this.csrf = m[1] || undefined;
    }
    return res;
  }

  async post(path: string, body: object = {}) {
    // Fetch the token first: the agent attaches cookies when the request is created.
    const token = await this.token();
    return this.track(await this.agent.post(path).set("Origin", this.origin).set("X-CSRF-Token", token).send(body));
  }

  async patch(path: string, body: object = {}) {
    // Fetch the token first: the agent attaches cookies when the request is created.
    const token = await this.token();
    return this.track(await this.agent.patch(path).set("Origin", this.origin).set("X-CSRF-Token", token).send(body));
  }

  async delete(path: string) {
    const token = await this.token();
    return this.track(await this.agent.delete(path).set("Origin", this.origin).set("X-CSRF-Token", token));
  }

  /** Multipart upload with CSRF headers. */
  async upload(path: string, fields: Record<string, string>, file?: { data: Buffer; filename: string; contentType: string }) {
    const token = await this.token();
    let req = this.agent.post(path).set("Origin", this.origin).set("X-CSRF-Token", token);
    for (const [k, v] of Object.entries(fields)) req = req.field(k, v);
    if (file) req = req.attach("file", file.data, { filename: file.filename, contentType: file.contentType });
    return this.track(await req);
  }

  /** Full phone sign-up/sign-in; returns the verify response body. */
  async signIn(phone: string, purpose: "register" | "login" = "register") {
    const start = await this.post(
      `/api/v1/auth/${purpose}`,
      purpose === "register" ? { phone, acceptTerms: true, acceptPrivacy: true } : { phone },
    );
    if (start.status !== 202) throw new Error(`start failed: ${start.status} ${JSON.stringify(start.body)}`);
    const code = this.h.sms.lastCodeFor(phone);
    if (!code) throw new Error("no SMS sent");
    // All test clients share one IP: move past every rate-limit window so tests stay independent.
    this.h.clock.now += 16 * 60_000;
    const verify = await this.post("/api/v1/auth/otp/verify", { challengeId: start.body.data.challengeId, code });
    if (verify.status !== 200) throw new Error(`verify failed: ${verify.status} ${JSON.stringify(verify.body)}`);
    return verify.body.data as { userId: string; isNew: boolean; hasSellerProfile: boolean };
  }
}

/** Signs in a new staff member with `role`, enrols TOTP and completes MFA for the session. */
export async function staffClient(h: TestHarness, role: "SUPER_ADMIN" | "VERIFICATION_OFFICER" | "SUPPORT_OFFICER" | "SECURITY_OFFICER" | "FINANCE_OFFICER") {
  const c = new Client(h);
  const phone = fakePhone();
  const { userId } = await c.signIn(phone);
  await h.db.staffRoleAssignment.create({ data: { userId, role } });
  await c.signIn(phone, "login");
  const { secret } = (await c.post("/api/v1/auth/mfa/totp/enroll")).body.data;
  const ok = await c.post("/api/v1/auth/mfa/totp/confirm", { code: totpAt(base32Decode(secret), totpStep(new Date())) });
  if (ok.status !== 200) throw new Error(`MFA confirm failed: ${ok.status}`);
  return { c, userId };
}
