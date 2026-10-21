import "server-only";
import { z } from "zod";

/**
 * Server-side environment, validated with Zod on first access.
 *
 * Nothing in this module may be imported by client components. Only
 * NEXT_PUBLIC_* variables are ever exposed to the browser, and this app uses
 * just one: NEXT_PUBLIC_SITE_URL.
 */

const emptyToUndefined = (v: unknown) => (typeof v === "string" && v.trim() === "" ? undefined : v);
const optionalString = z.preprocess(emptyToUndefined, z.string().min(1).optional());
const optionalUrl = z.preprocess(emptyToUndefined, z.url().optional());
const intWithDefault = (def: number, min = 0, max = Number.MAX_SAFE_INTEGER) =>
  z.preprocess(emptyToUndefined, z.coerce.number().int().min(min).max(max).default(def));

const EnvSchema = z
  .object({
    APP_ENV: z.enum(["development", "test", "staging", "production"]).default("development"),
    NEXT_PUBLIC_SITE_URL: z.preprocess(emptyToUndefined, z.url().default("http://localhost:3000")),
    LOG_LEVEL: z.enum(["debug", "info", "warn", "error"]).default("info"),

    // Supabase
    SUPABASE_URL: optionalUrl,
    SUPABASE_ANON_KEY: optionalString,
    SUPABASE_SERVICE_ROLE_KEY: optionalString,

    // Market data
    MARKET_DATA_PROVIDER: z.enum(["coingecko", "mock"]).default("mock"),
    COINGECKO_API_PLAN: z.enum(["public", "demo", "pro"]).default("demo"),
    COINGECKO_API_KEY: optionalString,
    MARKET_DATA_TIMEOUT_MS: intWithDefault(10_000, 1_000, 60_000),
    HISTORY_REFRESH_DELAY_MS: intWithDefault(2_500, 0, 120_000),

    // Token unlocks
    TOKEN_UNLOCK_PROVIDER: z.enum(["none"]).default("none"),

    // Billing
    BILLING_PROVIDER: z.enum(["none", "dev_simulator"]).default("none"),
    DEV_BILLING_WEBHOOK_SECRET: optionalString,

    // Email
    EMAIL_PROVIDER: z.enum(["console", "resend"]).default("console"),
    RESEND_API_KEY: optionalString,
    EMAIL_FROM: optionalString,

    // Telegram
    TELEGRAM_BOT_TOKEN: optionalString,
    TELEGRAM_BOT_USERNAME: optionalString,
    TELEGRAM_WEBHOOK_SECRET: optionalString,

    // AI
    AI_PROVIDER: z.enum(["none", "anthropic"]).default("none"),
    ANTHROPIC_API_KEY: optionalString,
    AI_MODEL: optionalString,
    AI_MAX_OUTPUT_TOKENS: intWithDefault(8_000, 1_000, 16_000),
    AI_TIMEOUT_MS: intWithDefault(60_000, 5_000, 180_000),

    // Jobs
    CRON_SECRET: optionalString,
  })
  .superRefine((env, ctx) => {
    const prod = env.APP_ENV === "production";
    if (prod && env.MARKET_DATA_PROVIDER === "mock") {
      ctx.addIssue({ code: "custom", path: ["MARKET_DATA_PROVIDER"], message: "The mock market-data provider cannot be used when APP_ENV=production." });
    }
    if (prod && env.BILLING_PROVIDER === "dev_simulator") {
      ctx.addIssue({ code: "custom", path: ["BILLING_PROVIDER"], message: "The development billing simulator cannot be used when APP_ENV=production." });
    }
    if (env.BILLING_PROVIDER === "dev_simulator" && (!env.DEV_BILLING_WEBHOOK_SECRET || env.DEV_BILLING_WEBHOOK_SECRET.length < 32)) {
      ctx.addIssue({ code: "custom", path: ["DEV_BILLING_WEBHOOK_SECRET"], message: "Must be at least 32 characters when BILLING_PROVIDER=dev_simulator." });
    }
    if (env.MARKET_DATA_PROVIDER === "coingecko" && env.COINGECKO_API_PLAN !== "public" && !env.COINGECKO_API_KEY) {
      ctx.addIssue({ code: "custom", path: ["COINGECKO_API_KEY"], message: "Required for the CoinGecko demo/pro plans (or set COINGECKO_API_PLAN=public)." });
    }
    if (env.EMAIL_PROVIDER === "resend" && (!env.RESEND_API_KEY || !env.EMAIL_FROM)) {
      ctx.addIssue({ code: "custom", path: ["RESEND_API_KEY"], message: "RESEND_API_KEY and EMAIL_FROM are required when EMAIL_PROVIDER=resend." });
    }
    if (prod && env.EMAIL_PROVIDER === "console") {
      ctx.addIssue({ code: "custom", path: ["EMAIL_PROVIDER"], message: "The console email adapter cannot be used when APP_ENV=production." });
    }
    if (env.AI_PROVIDER === "anthropic" && (!env.ANTHROPIC_API_KEY || !env.AI_MODEL)) {
      ctx.addIssue({ code: "custom", path: ["ANTHROPIC_API_KEY"], message: "ANTHROPIC_API_KEY and AI_MODEL are required when AI_PROVIDER=anthropic." });
    }
    if (env.TELEGRAM_BOT_TOKEN && (!env.TELEGRAM_WEBHOOK_SECRET || env.TELEGRAM_WEBHOOK_SECRET.length < 24)) {
      ctx.addIssue({ code: "custom", path: ["TELEGRAM_WEBHOOK_SECRET"], message: "A TELEGRAM_WEBHOOK_SECRET of at least 24 characters is required when TELEGRAM_BOT_TOKEN is set." });
    }
    if (env.CRON_SECRET && env.CRON_SECRET.length < 32) {
      ctx.addIssue({ code: "custom", path: ["CRON_SECRET"], message: "Must be at least 32 characters." });
    }
    if (prod && (!env.SUPABASE_URL || !env.SUPABASE_ANON_KEY || !env.SUPABASE_SERVICE_ROLE_KEY)) {
      ctx.addIssue({ code: "custom", path: ["SUPABASE_URL"], message: "Supabase configuration is required when APP_ENV=production." });
    }
  });

export type ServerEnv = z.infer<typeof EnvSchema>;

let cached: ServerEnv | undefined;

export class EnvValidationError extends Error {
  constructor(public readonly issues: string[]) {
    super(`Invalid server environment:\n- ${issues.join("\n- ")}`);
    this.name = "EnvValidationError";
  }
}

export function parseEnv(source: Record<string, string | undefined>): ServerEnv {
  const result = EnvSchema.safeParse(source);
  if (!result.success) {
    // Issue messages include variable names only, never values.
    throw new EnvValidationError(result.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`));
  }
  return result.data;
}

export function getEnv(): ServerEnv {
  if (!cached) cached = parseEnv(process.env);
  return cached;
}

/** Test helper: clear the memoized environment. */
export function resetEnvCache(): void {
  cached = undefined;
}

export function isSupabaseConfigured(env: ServerEnv = getEnv()): boolean {
  return Boolean(env.SUPABASE_URL && env.SUPABASE_ANON_KEY);
}

export function isProduction(env: ServerEnv = getEnv()): boolean {
  return env.APP_ENV === "production";
}
