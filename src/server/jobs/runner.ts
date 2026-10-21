import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { logger } from "@/lib/logger";

/**
 * Idempotent job runner.
 *
 * Each run has an idempotency key (usually job name + time bucket). The
 * database function `acquire_job` grants the run only if that key has not
 * already succeeded and is not locked by another live worker, so overlapping
 * cron invocations and retries are safe.
 */

export interface JobContext {
  db: SupabaseClient;
  now: Date;
  log: ReturnType<typeof logger.child>;
}

export type JobResult = { status: "succeeded"; summary: Record<string, unknown> } | { status: "skipped"; reason: string } | { status: "failed"; error: string };

export function bucketKey(name: string, now: Date, bucketMinutes: number): string {
  const ms = bucketMinutes * 60_000;
  return `${name}:${new Date(Math.floor(now.getTime() / ms) * ms).toISOString()}`;
}

export async function runJob(
  db: SupabaseClient,
  name: string,
  key: string,
  lockSeconds: number,
  fn: (ctx: JobContext) => Promise<Record<string, unknown>>,
): Promise<JobResult> {
  const log = logger.child({ job: name, key });
  const { data: jobId, error } = await db.rpc("acquire_job", { p_job_name: name, p_key: key, p_lock_seconds: lockSeconds });
  if (error) {
    log.error("job_acquire_failed", { code: error.code, message: error.message });
    return { status: "failed", error: "Could not acquire job lock." };
  }
  if (!jobId) {
    log.info("job_skipped_already_done_or_locked");
    return { status: "skipped", reason: "Already completed for this window or running elsewhere." };
  }
  const started = Date.now();
  try {
    const summary = await fn({ db, now: new Date(), log });
    await db.from("background_jobs").update({ status: "succeeded", finished_at: new Date().toISOString(), summary: { ...summary, durationMs: Date.now() - started } }).eq("id", jobId);
    log.info("job_succeeded", { durationMs: Date.now() - started, ...summary });
    return { status: "succeeded", summary };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    await db.from("background_jobs").update({ status: "failed", finished_at: new Date().toISOString(), error: message.slice(0, 2000) }).eq("id", jobId);
    log.error("job_failed", { err });
    return { status: "failed", error: message };
  }
}

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
