import { requireAdminApi } from "@/lib/auth/session";
import { getEnv } from "@/lib/env";
import { assertSameOrigin, handler, ok, parseJsonBody } from "@/lib/http/api";
import { publishScoringConfig, ScoringConfigInputSchema } from "@/server/admin";

export const dynamic = "force-dynamic";

/** Publish a new active scoring configuration (admins only; audited in SQL). */
export const POST = handler("POST /api/admin/scoring-config", async (req: Request) => {
  assertSameOrigin(req, getEnv().NEXT_PUBLIC_SITE_URL);
  const v = await requireAdminApi();
  return ok(await publishScoringConfig(v.supabase, await parseJsonBody(req, ScoringConfigInputSchema)), { status: 201 });
});
