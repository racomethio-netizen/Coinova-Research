import { z } from "zod";
import { requireAdminApi } from "@/lib/auth/session";
import { getEnv } from "@/lib/env";
import { ApiError, assertSameOrigin, handler, ok, parseJsonBody } from "@/lib/http/api";
import { ModelStatusSchema, setModelStatus } from "@/server/nexus-admin";

export const dynamic = "force-dynamic";

export const PATCH = handler("PATCH /api/admin/models/[id]", async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  assertSameOrigin(req, getEnv().NEXT_PUBLIC_SITE_URL);
  const v = await requireAdminApi();
  const id = z.uuid().safeParse((await ctx.params).id);
  if (!id.success) throw new ApiError("not_found", "Not found.");
  const { status } = await parseJsonBody(req, ModelStatusSchema);
  await setModelStatus(v.supabase, v.user.id, id.data, status);
  return ok({ status });
});
