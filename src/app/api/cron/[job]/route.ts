import { timingSafeEqual } from "node:crypto";
import { getEnv, isSupabaseConfigured } from "@/lib/env";
import { ApiError, handler, ok } from "@/lib/http/api";
import { isJobName, runNamedJob } from "@/server/jobs";

export const dynamic = "force-dynamic";
export const maxDuration = 900;

function authorized(req: Request): boolean {
  const secret = getEnv().CRON_SECRET;
  if (!secret) return false;
  const header = req.headers.get("authorization") ?? "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : "";
  const a = Buffer.from(secret);
  const b = Buffer.from(token);
  return a.length === b.length && timingSafeEqual(a, b);
}

/**
 * Trigger a background job: POST /api/cron/<job> with `Authorization: Bearer $CRON_SECRET`.
 * Called by the hosting platform's scheduler (see docs/DEPLOYMENT.md). Jobs
 * are idempotent per time window, so duplicate or retried calls are safe.
 */
export const POST = handler("POST /api/cron/[job]", async (req: Request, ctx: { params: Promise<{ job: string }> }) => {
  if (!getEnv().CRON_SECRET) throw new ApiError("not_configured", "CRON_SECRET is not configured.");
  if (!authorized(req)) throw new ApiError("unauthorized", "Invalid cron credentials.");
  if (!isSupabaseConfigured()) throw new ApiError("not_configured", "Database is not configured.");
  const { job } = await ctx.params;
  if (!isJobName(job)) throw new ApiError("not_found", "Unknown job.");
  const result = await runNamedJob(job);
  if (result.status === "failed") throw new ApiError("internal_error", `Job ${job} failed. See logs and background_jobs.`);
  return ok({ job, ...result });
});
