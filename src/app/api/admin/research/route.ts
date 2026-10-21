import { requireStaffApi } from "@/lib/auth/session";
import { getEnv } from "@/lib/env";
import { assertSameOrigin, handler, ok, parseJsonBody } from "@/lib/http/api";
import { ArticleInputSchema, createArticle } from "@/server/admin";

export const dynamic = "force-dynamic";

export const POST = handler("POST /api/admin/research", async (req: Request) => {
  assertSameOrigin(req, getEnv().NEXT_PUBLIC_SITE_URL);
  const v = await requireStaffApi();
  const input = await parseJsonBody(req, ArticleInputSchema);
  return ok(await createArticle(v.supabase, v.user.id, input), { status: 201 });
});
