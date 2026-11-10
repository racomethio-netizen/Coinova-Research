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
  const { data, error } = await viewer.supabase.rpc("export_my_data");
  if (error) throw mapDbError(error, "account export");
  if (!data) throw new ApiError("internal_error", "Export failed.");
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
