import { BacktestRequestSchema } from "@/lib/backtests/schema";
import { mapDbError } from "@/lib/db/errors";
import { authed } from "@/lib/http/authed";
import { ok, parseJsonBody } from "@/lib/http/api";

export const dynamic = "force-dynamic";

/** Your recent backtests (RLS: own runs only). */
export const GET = authed("GET /api/backtests", { feature: "backtesting" }, async ({ viewer }) => {
  const { data, error } = await viewer.supabase
    .from("backtest_runs")
    .select("id, pair_id, timeframe, params, period_start, period_end, status, error, created_at, finished_at")
    .eq("requested_by", viewer.user.id)
    .order("created_at", { ascending: false })
    .limit(20);
  if (error) throw mapDbError(error, "list backtests");
  return ok({ backtests: data });
});

/**
 * Queue a backtest (Premium). Feature, quota (10/day), one active run, and bounds are
 * enforced in SQL by request_backtest(); results are computed by the engine.
 */
export const POST = authed("POST /api/backtests", { feature: "backtesting", perMinute: 5 }, async ({ req, viewer }) => {
  const body = await parseJsonBody(req, BacktestRequestSchema);
  const { data, error } = await viewer.supabase.rpc("request_backtest", {
    p_pair_id: body.pairId,
    p_timeframe: body.timeframe,
    p_start: body.start,
    p_end: body.end,
    p_params: body.params,
  });
  if (error) throw mapDbError(error, "request backtest");
  return ok({ id: data as string, status: "queued" }, { status: 202 });
});
