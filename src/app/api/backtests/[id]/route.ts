import { z } from "zod";
import { mapDbError } from "@/lib/db/errors";
import { authed } from "@/lib/http/authed";
import { ApiError, ok } from "@/lib/http/api";

export const dynamic = "force-dynamic";

/** One backtest with its results, assumptions, and trades (RLS: owner or staff). */
export const GET = authed<{ id: string }>("GET /api/backtests/[id]", { feature: "backtesting" }, async ({ viewer, params }) => {
  const id = z.uuid().safeParse(params.id);
  if (!id.success) throw new ApiError("not_found", "Not found.");
  const [run, result, trades] = await Promise.all([
    viewer.supabase.from("backtest_runs").select("*").eq("id", id.data).maybeSingle(),
    viewer.supabase.from("backtest_results").select("metrics, equity_curve, baselines, assumptions").eq("run_id", id.data).maybeSingle(),
    viewer.supabase.from("backtest_trades").select("*").eq("run_id", id.data).order("seq").limit(1000),
  ]);
  for (const r of [run, result, trades]) if (r.error) throw mapDbError(r.error, "get backtest");
  if (!run.data) throw new ApiError("not_found", "Not found.");
  return ok({ run: run.data, result: result.data, trades: trades.data });
});
