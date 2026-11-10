import { z } from "zod";
import { requireAdminApi } from "@/lib/auth/session";
import { getEnv } from "@/lib/env";
import { ApiError, assertSameOrigin, handler, ok, parseJsonBody } from "@/lib/http/api";
import { annotateSignal, AnnotationSchema } from "@/server/nexus-admin";

export const dynamic = "force-dynamic";

/** Add a public note, correction or retraction. Signals themselves are never edited. */
export const POST = handler("POST /api/admin/signals/[id]/annotations", async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  assertSameOrigin(req, getEnv().NEXT_PUBLIC_SITE_URL);
  const v = await requireAdminApi();
  const id = z.uuid().safeParse((await ctx.params).id);
  if (!id.success) throw new ApiError("not_found", "Not found.");
  await annotateSignal(v.supabase, v.user.id, id.data, await parseJsonBody(req, AnnotationSchema));
  return ok({ annotated: true }, { status: 201 });
});
