import { escapeCsvText } from "@/lib/portfolio/csv";
import { authed } from "@/lib/http/authed";
import { listSignals } from "@/server/nexus";

export const dynamic = "force-dynamic";

const COLUMNS = ["signal_id", "symbol", "timeframe", "direction", "decided_at", "published_at", "entry_low", "entry_high", "stop_loss", "take_profit_1",
  "net_reward_risk", "visibility", "outcome", "net_return", "r_multiple", "closed_at"] as const;

/**
 * Performance report (Pro+): the full signal ledger the caller can see, including losses,
 * expirations and open signals, with exact decimal values. Formula-injection safe.
 */
export const GET = authed("GET /api/performance/export", { feature: "performance.reports", perMinute: 5 }, async ({ viewer }) => {
  const signals = await listSignals(viewer.supabase, 1000);
  const rows = signals.map((s) => [
    s.id, s.symbol, s.timeframe, s.direction, s.asOf, s.publishedAt, s.entryLow, s.entryHigh, s.stopLoss, s.takeProfits[0] ?? "",
    s.riskReward, s.visibility, s.outcome?.outcome ?? "open", s.outcome?.netReturn ?? "", s.outcome?.rMultiple ?? "", s.outcome?.closedAt ?? "",
  ].map((v) => escapeCsvText(String(v))).join(","));
  const body = [COLUMNS.join(","), ...rows].join("\r\n") + "\r\n";
  return new Response(body, {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="signal-ledger-${new Date().toISOString().slice(0, 10)}.csv"`,
      "Cache-Control": "private, no-store",
    },
  });
});
