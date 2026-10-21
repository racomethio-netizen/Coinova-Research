import { z } from "zod";
import { requireStaffApi } from "@/lib/auth/session";
import { getEnv } from "@/lib/env";
import { ApiError, assertSameOrigin, handler, ok, parseJsonBody } from "@/lib/http/api";
import { ArticlePatchSchema, updateArticle } from "@/server/admin";

export const dynamic = "force-dynamic";

export const PATCH = handler("PATCH /api/admin/research/[id]", async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  assertSameOrigin(req, getEnv().NEXT_PUBLIC_SITE_URL);
  const v = await requireStaffApi();
  const id = z.uuid().safeParse((await ctx.params).id);
  if (!id.success) throw new ApiError("not_found", "Article not found.");
  await updateArticle(v.supabase, v.user.id, id.data, await parseJsonBody(req, ArticlePatchSchema));
  return ok({ updated: true });
});
