import { z } from "zod";
import { requireAdminApi } from "@/lib/auth/session";
import { getEnv } from "@/lib/env";
import { ApiError, assertSameOrigin, handler, ok, parseJsonBody } from "@/lib/http/api";
import { recordTermsReview, TermsReviewSchema } from "@/server/nexus-admin";

export const dynamic = "force-dynamic";

/** Gate G-4: record the market-data terms review (enables production ingestion for this source). */
export const POST = handler("POST /api/admin/sources/[key]", async (req: Request, ctx: { params: Promise<{ key: string }> }) => {
  assertSameOrigin(req, getEnv().NEXT_PUBLIC_SITE_URL);
  const v = await requireAdminApi();
  const key = z.string().regex(/^[a-z][a-z0-9_]{1,39}$/).safeParse((await ctx.params).key);
  if (!key.success) throw new ApiError("not_found", "Not found.");
  const { notes } = await parseJsonBody(req, TermsReviewSchema);
  await recordTermsReview(v.supabase, v.user.id, key.data, notes);
  return ok({ reviewed: true });
});
