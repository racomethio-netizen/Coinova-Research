import { z } from "zod";
import { requireStaffApi } from "@/lib/auth/session";
import { getEnv } from "@/lib/env";
import { ApiError, assertSameOrigin, handler, ok } from "@/lib/http/api";
import { deleteUnlockEvent } from "@/server/admin";

export const dynamic = "force-dynamic";

export const DELETE = handler("DELETE /api/admin/unlocks/[id]", async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  assertSameOrigin(req, getEnv().NEXT_PUBLIC_SITE_URL);
  const v = await requireStaffApi();
  const id = z.uuid().safeParse((await ctx.params).id);
  if (!id.success) throw new ApiError("not_found", "Not found.");
  await deleteUnlockEvent(v.supabase, v.user.id, id.data);
  return ok({ deleted: true });
});
