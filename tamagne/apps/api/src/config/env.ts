import { createFieldCipher, parseKeyRing } from "@tamagne/core";
import { z } from "zod";

/**
 * Server configuration, validated once at startup. Secrets come only from the
 * process environment (populated by a secrets manager in production) and are
 * never logged. See .env.example for every variable.
 */

const base64Key32 = z
  .string()
  .refine((v) => Buffer.from(v, "base64").length === 32, "must be 32 bytes, base64-encoded");

const booleanString = z
  .enum(["true", "false"])
  .default("false")
  .transform((v) => v === "true");


const EnvSchema = z.object({
  APP_ENV: z.enum(["development", "test", "staging", "production"]).default("development"),
  PORT: z.coerce.number().int().min(1).max(65535).default(4000),
  /** Comma-separated exact origins allowed to call the API from a browser. */
  CORS_ORIGINS: z.string().default("http://localhost:3000"),
  PUBLIC_WEB_URL: z.url().default("http://localhost:3000"),
  DATABASE_URL: z.string().min(1),
  REDIS_URL: z.string().optional(),
  /** "1:<base64>,2:<base64>": field-encryption key ring (see @tamagne/core parseKeyRing). */
  FIELD_ENCRYPTION_KEYS: z.string().min(1),
  FIELD_ENCRYPTION_ACTIVE_KEY_ID: z.coerce.number().int().min(0).max(255),
  /** Pepper for HMACs of identity subjects, OTP codes and IP addresses. */
  HMAC_PEPPER: base64Key32,
  OPENAPI_ENABLED: booleanString,
  /** SMS delivery for OTP. "console" prints codes (development only); "test" keeps an in-memory outbox (APP_ENV=test only). */
  SMS_PROVIDER: z.enum(["disabled", "console", "test"]).default("disabled"),
  /** Document storage. "local" encrypts files to a directory (development/test only). */
  STORAGE_DRIVER: z.enum(["disabled", "local"]).default("disabled"),
  STORAGE_LOCAL_DIR: z.string().default(".tmp/storage"),
  /** Malware scanning. "eicar-only" detects the EICAR test file only (development/test). */
  SCANNER: z.enum(["disabled", "clamav", "eicar-only"]).default("disabled"),
  CLAMAV_HOST: z.string().default("127.0.0.1"),
  CLAMAV_PORT: z.coerce.number().int().min(1).max(65535).default(3310),
  /** OTP sends per IP per 15 minutes. Generous because mobile carriers share IPs (CGNAT); per-phone limits stay strict. */
  OTP_SEND_LIMIT_PER_IP: z.coerce.number().int().min(5).max(10000).default(30),
  /** Days to answer access/correction requests (confirm the statutory period with counsel). */
  DSR_RESPONSE_DAYS: z.coerce.number().int().min(1).max(90).default(30),
  /** Grace period before an account deletion is carried out (support can cancel meanwhile). */
  DELETION_GRACE_DAYS: z.coerce.number().int().min(0).max(60).default(14),
  /** Accept synthetic +999 phone numbers. Never in production. */
  ALLOW_TEST_PHONE_NUMBERS: booleanString,
  TRUST_PROXY_HOPS: z.coerce.number().int().min(0).max(5).default(0),

  // ── Fayda eSignet (OIDC). Disabled unless explicitly configured. ──
  FAYDA_MODE: z.enum(["disabled", "sandbox", "production"]).default("disabled"),
  /** HTTPS issuer. Plain http is accepted only for a loopback test provider when APP_ENV=test. */
  FAYDA_ISSUER: z.url().optional(),
  FAYDA_CLIENT_ID: z.string().min(1).optional(),
  FAYDA_REDIRECT_URI: z.url().optional(),
  /** Private JWK (JSON) for private_key_jwt client assertion. From the secrets manager only. */
  FAYDA_CLIENT_PRIVATE_KEY_JWK: z.string().min(1).optional(),
  FAYDA_SCOPES: z.string().default("openid profile"),
  FAYDA_ACR_VALUES: z.string().optional(),
  FAYDA_CLAIMS_LOCALES: z.string().default("en am"),
  /** Userinfo claims requested (space-separated). Keep minimal: no picture, no ID numbers. */
  FAYDA_USERINFO_CLAIMS: z.string().default("name"),
});

export type RawEnv = z.input<typeof EnvSchema>;

export type FaydaConfig =
  | { enabled: false; mode: "disabled"; reason: string }
  | {
      enabled: true;
      mode: "sandbox" | "production";
      issuer: string;
      clientId: string;
      redirectUri: string;
      clientPrivateKeyJwk: string;
      scopes: string[];
      acrValues: string | undefined;
      claimsLocales: string;
      userinfoClaims: string[];
      /** Only for APP_ENV=test with a loopback issuer. */
      allowInsecureLoopback: boolean;
    };

export interface AppConfig {
  appEnv: "development" | "test" | "staging" | "production";
  isProduction: boolean;
  port: number;
  corsOrigins: string[];
  publicWebUrl: string;
  databaseUrl: string;
  redisUrl: string | undefined;
  fieldEncryption: { keys: string; activeKeyId: number };
  hmacPepper: Buffer;
  openApiEnabled: boolean;
  smsProvider: "disabled" | "console" | "test";
  storage: { driver: "disabled" | "local"; localDir: string };
  scanner: { kind: "disabled" | "clamav" | "eicar-only"; host: string; port: number };
  allowTestPhoneNumbers: boolean;
  otpSendLimitPerIp: number;
  privacy: { dsrResponseDays: number; deletionGraceDays: number };
  /** Cookies are Secure and use the __Host- prefix outside development/test. */
  secureCookies: boolean;
  trustProxyHops: number;
  fayda: FaydaConfig;
}

export class ConfigError extends Error {
  constructor(readonly issues: string[]) {
    // Messages name variables only; never values.
    super(`Invalid configuration:\n - ${issues.join("\n - ")}`);
    this.name = "ConfigError";
  }
}

const SANDBOX_HOST_HINTS = ["sandbox", "collab", "mock", "test", "dev", "staging", "localhost", "127.0.0.1"];

function looksLikeSandbox(url: string): boolean {
  const host = new URL(url).hostname.toLowerCase();
  return SANDBOX_HOST_HINTS.some((h) => host.includes(h));
}

function faydaConfig(env: z.output<typeof EnvSchema>, issues: string[]): FaydaConfig {
  if (env.FAYDA_MODE === "disabled") {
    return {
      enabled: false,
      mode: "disabled",
      reason:
        "Fayda integration is disabled. Identity verification is available only through the manual review workflow until authorized credentials are configured (docs/FAYDA_INTEGRATION.md).",
    };
  }

  const missing = (["FAYDA_ISSUER", "FAYDA_CLIENT_ID", "FAYDA_REDIRECT_URI", "FAYDA_CLIENT_PRIVATE_KEY_JWK"] as const).filter(
    (k) => !env[k],
  );
  if (missing.length) {
    issues.push(`FAYDA_MODE=${env.FAYDA_MODE} requires ${missing.join(", ")}`);
    return { enabled: false, mode: "disabled", reason: "Fayda configuration incomplete" };
  }

  try {
    const jwk = JSON.parse(env.FAYDA_CLIENT_PRIVATE_KEY_JWK!) as Record<string, unknown>;
    if (typeof jwk.kty !== "string" || typeof jwk.d !== "string") issues.push("FAYDA_CLIENT_PRIVATE_KEY_JWK must be a private JWK");
  } catch {
    issues.push("FAYDA_CLIENT_PRIVATE_KEY_JWK must be valid JSON");
  }

  const isProd = env.APP_ENV === "production";
  const issuerUrl = new URL(env.FAYDA_ISSUER!);
  const loopback = ["127.0.0.1", "localhost", "[::1]"].includes(issuerUrl.hostname);
  const allowInsecureLoopback = env.APP_ENV === "test" && loopback && issuerUrl.protocol === "http:";
  if (issuerUrl.protocol !== "https:" && !allowInsecureLoopback) issues.push("FAYDA_ISSUER must use https");
  const userinfoClaims = env.FAYDA_USERINFO_CLAIMS.split(/\s+/).filter(Boolean);
  const forbiddenClaims = userinfoClaims.filter((c) => /picture|photo|face|biometric|individual_id|uin|vid|fin|fan/i.test(c));
  if (forbiddenClaims.length) issues.push(`FAYDA_USERINFO_CLAIMS must not request biometric data or ID numbers (${forbiddenClaims.join(", ")})`);
  if (isProd && env.FAYDA_MODE !== "production") {
    issues.push("APP_ENV=production must not use FAYDA_MODE=sandbox (sandbox credentials are never allowed in production)");
  }
  if (!isProd && env.FAYDA_MODE === "production") {
    issues.push("FAYDA_MODE=production is only allowed when APP_ENV=production");
  }
  if (env.FAYDA_MODE === "production") {
    if (looksLikeSandbox(env.FAYDA_ISSUER!)) issues.push("FAYDA_ISSUER looks like a sandbox/test issuer but FAYDA_MODE=production");
    if (!env.FAYDA_REDIRECT_URI!.startsWith("https://")) issues.push("FAYDA_REDIRECT_URI must use https in production");
  }

  return {
    enabled: true,
    mode: env.FAYDA_MODE,
    issuer: env.FAYDA_ISSUER!,
    clientId: env.FAYDA_CLIENT_ID!,
    redirectUri: env.FAYDA_REDIRECT_URI!,
    clientPrivateKeyJwk: env.FAYDA_CLIENT_PRIVATE_KEY_JWK!,
    scopes: env.FAYDA_SCOPES.split(/\s+/).filter(Boolean),
    acrValues: env.FAYDA_ACR_VALUES,
    claimsLocales: env.FAYDA_CLAIMS_LOCALES,
    userinfoClaims,
    allowInsecureLoopback,
  };
}

/** Well-known development placeholders from .env.example that must never reach production. */
const DEV_PLACEHOLDER_MARKERS = ["dev-only", "change-me", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="];

export function loadConfig(source: Record<string, string | undefined> = process.env): AppConfig {
  const parsed = EnvSchema.safeParse(source);
  if (!parsed.success) {
    throw new ConfigError(parsed.error.issues.map((i) => `${i.path.join(".") || "(root)"}: ${i.message}`));
  }
  const env = parsed.data;
  const issues: string[] = [];
  const isProduction = env.APP_ENV === "production";

  const corsOrigins = env.CORS_ORIGINS.split(",").map((s) => s.trim()).filter(Boolean);
  for (const o of corsOrigins) {
    if (o === "*") issues.push("CORS_ORIGINS must list exact origins, not *");
    else if (!URL.canParse(o)) issues.push(`CORS_ORIGINS contains an invalid origin`);
  }

  if (isProduction) {
    for (const key of ["FIELD_ENCRYPTION_KEYS", "HMAC_PEPPER", "DATABASE_URL"] as const) {
      if (DEV_PLACEHOLDER_MARKERS.some((m) => env[key].includes(m))) issues.push(`${key} still holds a development placeholder`);
    }
    if (!/[?&]sslmode=(verify-full|verify-ca)\b/.test(env.DATABASE_URL)) {
      issues.push("DATABASE_URL must use sslmode=verify-full (or verify-ca) in production");
    }
    if (env.OPENAPI_ENABLED) issues.push("OPENAPI_ENABLED must be false in production (publish the exported spec instead)");
    if (corsOrigins.some((o) => o.startsWith("http://"))) issues.push("CORS_ORIGINS must be https in production");
    if (!env.REDIS_URL) issues.push("REDIS_URL is required in production (rate limiting must be shared across instances)");
    if (env.ALLOW_TEST_PHONE_NUMBERS) issues.push("ALLOW_TEST_PHONE_NUMBERS must be false in production");
  }
  if (env.SMS_PROVIDER === "console" && !["development", "test"].includes(env.APP_ENV)) {
    issues.push("SMS_PROVIDER=console prints OTP codes and is only allowed in development/test");
  }
  const devOrTest = env.APP_ENV === "development" || env.APP_ENV === "test";
  if (env.STORAGE_DRIVER === "local" && !devOrTest) {
    issues.push("STORAGE_DRIVER=local is only for development/test; production needs Ethiopia-hosted object storage");
  }
  if (env.SCANNER === "eicar-only" && !devOrTest) {
    issues.push("SCANNER=eicar-only is not malware protection and is only allowed in development/test");
  }
  if (env.STORAGE_DRIVER !== "disabled" && env.SCANNER === "disabled" && !devOrTest) {
    issues.push("Document storage requires SCANNER=clamav outside development/test");
  }
  if (env.SMS_PROVIDER === "test" && env.APP_ENV !== "test") {
    issues.push("SMS_PROVIDER=test is only allowed when APP_ENV=test");
  }

  try {
    createFieldCipher({ keys: parseKeyRing(env.FIELD_ENCRYPTION_KEYS), activeKeyId: env.FIELD_ENCRYPTION_ACTIVE_KEY_ID });
  } catch (e) {
    issues.push(`FIELD_ENCRYPTION_KEYS / FIELD_ENCRYPTION_ACTIVE_KEY_ID: ${(e as Error).message}`);
  }

  const fayda = faydaConfig(env, issues);
  if (issues.length) throw new ConfigError(issues);

  return {
    appEnv: env.APP_ENV,
    isProduction,
    port: env.PORT,
    corsOrigins,
    publicWebUrl: env.PUBLIC_WEB_URL,
    databaseUrl: env.DATABASE_URL,
    redisUrl: env.REDIS_URL,
    fieldEncryption: { keys: env.FIELD_ENCRYPTION_KEYS, activeKeyId: env.FIELD_ENCRYPTION_ACTIVE_KEY_ID },
    hmacPepper: Buffer.from(env.HMAC_PEPPER, "base64"),
    openApiEnabled: env.OPENAPI_ENABLED,
    smsProvider: env.SMS_PROVIDER,
    storage: { driver: env.STORAGE_DRIVER, localDir: env.STORAGE_LOCAL_DIR },
    scanner: { kind: env.SCANNER, host: env.CLAMAV_HOST, port: env.CLAMAV_PORT },
    allowTestPhoneNumbers: env.ALLOW_TEST_PHONE_NUMBERS,
    otpSendLimitPerIp: env.OTP_SEND_LIMIT_PER_IP,
    privacy: { dsrResponseDays: env.DSR_RESPONSE_DAYS, deletionGraceDays: env.DELETION_GRACE_DAYS },
    secureCookies: env.APP_ENV === "production" || env.APP_ENV === "staging",
    trustProxyHops: env.TRUST_PROXY_HOPS,
    fayda,
  };
}
