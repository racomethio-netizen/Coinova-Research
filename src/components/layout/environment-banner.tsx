import { getEnv } from "@/lib/env";

/**
 * Persistent banner whenever non-production data or billing is active, so mock
 * values can never be mistaken for live market data.
 */
export function EnvironmentBanner() {
  const env = getEnv();
  const notes: string[] = [];
  if (env.MARKET_DATA_PROVIDER === "mock") notes.push("Market data is MOCK DATA for development — fictional assets, not real prices.");
  if (env.BILLING_PROVIDER === "dev_simulator") notes.push("Billing runs in the development simulator — no real payments are processed.");
  if (!notes.length) return null;
  return (
    <div role="status" className="border-b border-warning/40 bg-warning-soft px-4 py-2 text-center text-xs font-medium text-warning">
      {notes.map((n) => (
        <p key={n}>{n}</p>
      ))}
    </div>
  );
}
