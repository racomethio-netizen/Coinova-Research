import { describe, expect, it } from "vitest";
import { ConfigError, loadConfig } from "../src/config/env.js";

const key = (n: number) => Buffer.alloc(32, n).toString("base64");
const jwk = JSON.stringify({ kty: "RSA", n: "test", e: "AQAB", d: "test-private" });

const dev = {
  APP_ENV: "development",
  DATABASE_URL: "postgresql://postgres@127.0.0.1:54339/tamagne_test",
  FIELD_ENCRYPTION_KEYS: `1:${key(1)}`,
  FIELD_ENCRYPTION_ACTIVE_KEY_ID: "1",
  HMAC_PEPPER: key(2),
};
const prod = {
  ...dev,
  APP_ENV: "production",
  DATABASE_URL: "postgresql://app@db.internal:5432/tamagne?sslmode=verify-full",
  CORS_ORIGINS: "https://tamagne.example",
  PUBLIC_WEB_URL: "https://tamagne.example",
  REDIS_URL: "rediss://cache.internal:6380/0",
};
const faydaProd = {
  FAYDA_MODE: "production",
  FAYDA_ISSUER: "https://esignet.fayda.example",
  FAYDA_CLIENT_ID: "client",
  FAYDA_REDIRECT_URI: "https://tamagne.example/api/v1/fayda/callback",
  FAYDA_CLIENT_PRIVATE_KEY_JWK: jwk,
};

function issues(env: Record<string, string>): string[] {
  try {
    loadConfig(env);
    return [];
  } catch (e) {
    if (e instanceof ConfigError) return e.issues;
    throw e;
  }
}

describe("configuration", () => {
  it("Fayda is disabled by default with a manual-review explanation", () => {
    const c = loadConfig(dev);
    expect(c.fayda.enabled).toBe(false);
    expect(c.fayda.enabled === false && c.fayda.reason).toMatch(/manual review/);
  });

  it("refuses sandbox Fayda mode in production", () => {
    expect(issues({ ...prod, ...faydaProd, FAYDA_MODE: "sandbox" }).join()).toMatch(/must not use FAYDA_MODE=sandbox/);
  });

  it("refuses production Fayda mode outside production", () => {
    expect(issues({ ...dev, ...faydaProd }).join()).toMatch(/only allowed when APP_ENV=production/);
  });

  it("refuses a sandbox-looking issuer in production mode", () => {
    expect(issues({ ...prod, ...faydaProd, FAYDA_ISSUER: "https://esignet.collab.mosip.example" }).join()).toMatch(/looks like a sandbox/);
  });

  it("requires complete Fayda settings when enabled", () => {
    expect(issues({ ...dev, FAYDA_MODE: "sandbox", FAYDA_ISSUER: "https://sandbox.example" }).join()).toMatch(
      /requires FAYDA_CLIENT_ID, FAYDA_REDIRECT_URI, FAYDA_CLIENT_PRIVATE_KEY_JWK/,
    );
    expect(issues({ ...dev, ...faydaProd, FAYDA_MODE: "sandbox", FAYDA_CLIENT_PRIVATE_KEY_JWK: "not json" }).join()).toMatch(/valid JSON/);
    expect(issues({ ...dev, ...faydaProd, FAYDA_MODE: "sandbox", FAYDA_CLIENT_PRIVATE_KEY_JWK: '{"kty":"RSA"}' }).join()).toMatch(/private JWK/);
    expect(issues({ ...dev, ...faydaProd, FAYDA_ISSUER: "http://insecure.example" }).join()).toMatch(/FAYDA_ISSUER/);
  });

  it("accepts a complete production configuration", () => {
    const c = loadConfig({ ...prod, ...faydaProd });
    expect(c.isProduction).toBe(true);
    expect(c.fayda.enabled && c.fayda.mode).toBe("production");
  });

  it("production requires TLS to the database, https origins, no OpenAPI UI and no dev placeholders", () => {
    const all = issues({
      ...prod,
      DATABASE_URL: "postgresql://app@db/tamagne",
      CORS_ORIGINS: "http://tamagne.example",
      OPENAPI_ENABLED: "true",
      HMAC_PEPPER: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    }).join("\n");
    expect(all).toMatch(/sslmode=verify-full/);
    expect(all).toMatch(/CORS_ORIGINS must be https/);
    expect(all).toMatch(/OPENAPI_ENABLED must be false/);
    expect(all).toMatch(/HMAC_PEPPER still holds a development placeholder/);
  });

  it("rejects wildcard CORS and weak or inconsistent keys", () => {
    expect(issues({ ...dev, CORS_ORIGINS: "*" }).join()).toMatch(/not \*/);
    expect(issues({ ...dev, HMAC_PEPPER: Buffer.alloc(16).toString("base64") }).join()).toMatch(/HMAC_PEPPER/);
    expect(issues({ ...dev, FIELD_ENCRYPTION_ACTIVE_KEY_ID: "2" }).join()).toMatch(/active key 2/);
  });

  it("error messages never echo secret values", () => {
    const secret = Buffer.alloc(16, 9).toString("base64");
    const msg = issues({ ...dev, HMAC_PEPPER: secret, FIELD_ENCRYPTION_KEYS: `1:${secret}` }).join("\n");
    expect(msg).not.toContain(secret);
  });

  it("SMS and test-number switches are confined to safe environments", () => {
    expect(issues({ ...prod, SMS_PROVIDER: "console" }).join()).toMatch(/only allowed in development\/test/);
    expect(issues({ ...dev, SMS_PROVIDER: "test" }).join()).toMatch(/only allowed when APP_ENV=test/);
    expect(issues({ ...prod, ALLOW_TEST_PHONE_NUMBERS: "true" }).join()).toMatch(/ALLOW_TEST_PHONE_NUMBERS must be false/);
    const { REDIS_URL: _omit, ...noRedis } = prod;
    expect(issues(noRedis).join()).toMatch(/REDIS_URL is required/);
    expect(loadConfig({ ...dev, SMS_PROVIDER: "console" }).smsProvider).toBe("console");
    expect(loadConfig(prod).secureCookies).toBe(true);
    expect(loadConfig(dev).secureCookies).toBe(false);
  });

  it("document storage and scanning are confined to safe settings", () => {
    expect(issues({ ...prod, STORAGE_DRIVER: "local" }).join()).toMatch(/STORAGE_DRIVER=local is only for development/);
    expect(issues({ ...prod, SCANNER: "eicar-only" }).join()).toMatch(/not malware protection/);
    expect(loadConfig({ ...dev, STORAGE_DRIVER: "local", SCANNER: "eicar-only" }).storage.driver).toBe("local");
    expect(loadConfig(prod).storage.driver).toBe("disabled");
    expect(loadConfig({ ...prod, SCANNER: "clamav", CLAMAV_HOST: "clamav.internal" }).scanner).toEqual({ kind: "clamav", host: "clamav.internal", port: 3310 });
  });
});
