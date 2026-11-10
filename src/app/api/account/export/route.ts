import { authed } from "@/lib/http/authed";
import { ApiError } from "@/lib/http/api";
import { mapDbError } from "@/lib/db/errors";
import { logger } from "@/lib/logger";

export const dynamic = "force-dynamic";

/**
 * Download all of the signed-in user's data as JSON. The SQL function runs
 * as the caller (SECURITY INVOKER), so RLS guarantees it contains only rows
 * the user may already read.
 */
export const GET = authed("GET /api/account/export", { perMinute: 3 }, async ({ viewer }) => {
  const [main, paper] = await Promise.all([viewer.supabase.rpc("export_my_data"), viewer.supabase.rpc("export_my_paper_data")]);
  if (main.error) throw mapDbError(main.error, "account export");
  if (paper.error) throw mapDbError(paper.error, "account export (paper)");
  if (!main.data) throw new ApiError("internal_error", "Export failed.");
  const data = { ...(main.data as Record<string, unknown>), paper_trading: paper.data };
  logger.info("account_exported");
  const date = new Date().toISOString().slice(0, 10);
  return new Response(JSON.stringify(data, null, 2), {
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Content-Disposition": `attachment; filename="account-export-${date}.json"`,
      "Cache-Control": "private, no-store",
    },
  });
});
