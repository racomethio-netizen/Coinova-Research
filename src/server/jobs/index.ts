import "server-only";
import { PAID_PLANS, TIER_LIMITS } from "@/config/plans";
import { requireServiceClient } from "@/lib/supabase/server";
import { deliverNotifications, evaluateAlerts } from "./alerts";
import { refreshFundamentals, refreshHistory, refreshMarkets } from "./market";
import { bucketKey, runJob, type JobContext, type JobResult } from "./runner";
import { computeScores } from "./scores";

/**
 * Job registry. `bucketMinutes` defines the idempotency window: at most one
 * successful run per window. Recommended schedules are in docs/DEPLOYMENT.md.
 */
interface JobDef {
  bucketMinutes: number;
  lockSeconds: number;
  run: (ctx: JobContext) => Promise<Record<string, unknown>>;
}

async function syncPlans({ db }: JobContext) {
  const rows = [
    { id: "free", tier: "free", name: "Free", billing_interval: null, price_cents: 0, limits: TIER_LIMITS.free, sort_order: 0, is_active: true },
    ...PAID_PLANS.map((p, i) => ({
      id: p.id,
      tier: p.tier,
      name: p.name,
      billing_interval: p.interval,
      price_cents: p.priceCents,
      limits: TIER_LIMITS[p.tier],
      provider_price_ids: p.providerPriceIds,
      sort_order: i + 1,
      is_active: p.isActive,
    })),
  ];
  const { error } = await db.from("membership_plans").upsert(rows, { onConflict: "id" });
  if (error) throw new Error(error.message);
  return { plans: rows.length };
}

async function cleanup({ db }: JobContext) {
  const { data, error } = await db.rpc("run_retention_cleanup");
  if (error) throw new Error(error.message);
  return data as Record<string, unknown>;
}

export const JOBS: Record<string, JobDef> = {
  "refresh-markets": { bucketMinutes: 5, lockSeconds: 240, run: refreshMarkets },
  "evaluate-alerts": { bucketMinutes: 5, lockSeconds: 240, run: evaluateAlerts },
  "deliver-notifications": { bucketMinutes: 1, lockSeconds: 110, run: (c) => deliverNotifications(c) },
  "compute-scores": { bucketMinutes: 60, lockSeconds: 1800, run: computeScores },
  "refresh-history": { bucketMinutes: 60, lockSeconds: 1800, run: (c) => refreshHistory(c) },
  "refresh-fundamentals": { bucketMinutes: 60, lockSeconds: 1800, run: (c) => refreshFundamentals(c) },
  cleanup: { bucketMinutes: 1440, lockSeconds: 600, run: cleanup },
  "sync-plans": { bucketMinutes: 1, lockSeconds: 60, run: syncPlans },
};

export type JobName = keyof typeof JOBS;

export function isJobName(name: string): name is JobName {
  return Object.prototype.hasOwnProperty.call(JOBS, name);
}

export async function runNamedJob(name: JobName, now = new Date()): Promise<JobResult> {
  const def = JOBS[name]!;
  const db = requireServiceClient();
  return runJob(db, name, bucketKey(name, now, def.bucketMinutes), def.lockSeconds, def.run);
}
