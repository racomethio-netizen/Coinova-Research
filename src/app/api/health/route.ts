import { NextResponse } from "next/server";
import { getEnv, isSupabaseConfigured } from "@/lib/env";
import { getServiceClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

/**
 * Liveness/readiness probe. Cheap: one indexed database read with a timeout,
 * no market-provider call. Returns 503 when a configured dependency fails.
 * Never includes secrets or configuration values.
 */
export async function GET() {
  const started = Date.now();
  let env;
  try {
    env = getEnv();
  } catch {
    return NextResponse.json({ status: "error", checks: { config: "invalid" } }, { status: 503, headers: { "Cache-Control": "no-store" } });
  }

  let database: "ok" | "not_configured" | "error" = "not_configured";
  if (isSupabaseConfigured(env)) {
    const client = getServiceClient();
    if (client) {
      try {
        const res = await Promise.race([
          client.from("feature_flags").select("key").limit(1),
          new Promise<{ error: Error }>((resolve) => setTimeout(() => resolve({ error: new Error("timeout") }), 3000)),
        ]);
        database = res.error ? "error" : "ok";
      } catch {
        database = "error";
      }
    }
  }

  const healthy = database !== "error";
  return NextResponse.json(
    {
      status: healthy ? "ok" : "degraded",
      time: new Date().toISOString(),
      environment: env.APP_ENV,
      version: process.env.RENDER_GIT_COMMIT?.slice(0, 7) ?? process.env.npm_package_version ?? "dev",
      checks: {
        database,
        marketData: env.MARKET_DATA_PROVIDER,
        billing: env.BILLING_PROVIDER,
        ai: env.AI_PROVIDER,
        email: env.EMAIL_PROVIDER,
        telegram: env.TELEGRAM_BOT_TOKEN ? "configured" : "not_configured",
      },
      latencyMs: Date.now() - started,
    },
    { status: healthy ? 200 : 503, headers: { "Cache-Control": "no-store" } },
  );
}
