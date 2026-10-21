import "server-only";
import type { Viewer } from "@/lib/auth/session";
import { getEnv } from "@/lib/env";
import { logger } from "@/lib/logger";
import type { SupplyPoint, UnlockEvent } from "./tokenomics";

/**
 * Token-unlock data.
 *
 * Two sources, both explicit about provenance:
 * 1. A TokenUnlockProvider adapter (none is configured yet; see
 *    docs/MARKET_DATA.md#token-unlocks for how to add one).
 * 2. Events entered by staff from a cited project source, stored in
 *    `token_unlock_events` with source_url, provenance and confidence.
 *
 * Unlock schedules are never estimated or fabricated.
 */

export interface TokenUnlockProvider {
  readonly id: string;
  getUpcomingUnlocks(coinId: string): Promise<UnlockEvent[]>;
}

export type UnlockLookup = { status: "ok"; events: UnlockEvent[] } | { status: "not_configured" };

/** Provider adapter registry. Empty until a real provider is integrated and tested. */
export function getUnlockProvider(): TokenUnlockProvider | null {
  const env = getEnv();
  switch (env.TOKEN_UNLOCK_PROVIDER) {
    case "none":
      return null;
  }
}

export async function getUnlockEvents(viewer: Viewer, coinId: string): Promise<UnlockLookup> {
  const provider = getUnlockProvider();
  const events: UnlockEvent[] = [];
  let anySource = false;

  if (viewer.supabase) {
    anySource = true;
    const { data, error } = await viewer.supabase
      .from("token_unlock_events")
      .select("id, unlock_at, amount, allocation, source_type, source_url, confidence")
      .eq("coin_id", coinId)
      .eq("status", "scheduled")
      .gte("unlock_at", new Date().toISOString())
      .order("unlock_at")
      .limit(100);
    if (error) logger.warn("unlock_events_read_failed", { code: error.code });
    for (const r of data ?? []) {
      events.push({
        id: r.id as string,
        unlockAt: r.unlock_at as string,
        amount: Number(r.amount),
        allocation: (r.allocation as string | null) ?? null,
        sourceType: r.source_type as UnlockEvent["sourceType"],
        sourceUrl: (r.source_url as string | null) ?? null,
        confidence: r.confidence as UnlockEvent["confidence"],
      });
    }
  }
  if (provider) {
    anySource = true;
    try {
      events.push(...(await provider.getUpcomingUnlocks(coinId)));
    } catch (err) {
      logger.warn("unlock_provider_failed", { provider: provider.id, err });
    }
  }
  return anySource ? { status: "ok", events } : { status: "not_configured" };
}

/** Recorded daily circulating-supply observations (for inflation estimates). */
export async function getSupplyHistory(viewer: Viewer, coinId: string): Promise<SupplyPoint[]> {
  if (!viewer.supabase) return [];
  const since = new Date(Date.now() - 400 * 86_400_000).toISOString().slice(0, 10);
  const { data, error } = await viewer.supabase
    .from("tokenomics_snapshots")
    .select("snapshot_date, circulating_supply")
    .eq("coin_id", coinId)
    .gte("snapshot_date", since)
    .order("snapshot_date")
    .limit(400);
  if (error) return [];
  return (data ?? [])
    .filter((r) => r.circulating_supply !== null)
    .map((r) => ({ at: r.snapshot_date as string, circulatingSupply: Number(r.circulating_supply) }));
}
