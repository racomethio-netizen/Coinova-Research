import { requireAdminApi } from "@/lib/auth/session";
import { handler, ok } from "@/lib/http/api";
import { getAdminOverview } from "@/server/admin";

export const dynamic = "force-dynamic";

export const GET = handler("GET /api/admin/overview", async () => {
  const v = await requireAdminApi();
  return ok(await getAdminOverview(v.supabase));
});
