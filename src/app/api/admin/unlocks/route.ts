import { requireStaffApi } from "@/lib/auth/session";
import { getEnv } from "@/lib/env";
import { assertSameOrigin, handler, ok, parseJsonBody } from "@/lib/http/api";
import { createUnlockEvent, UnlockInputSchema } from "@/server/admin";

export const dynamic = "force-dynamic";

/** Record a token unlock from a cited source. Never enter estimated schedules. */
export const POST = handler("POST /api/admin/unlocks", async (req: Request) => {
  assertSameOrigin(req, getEnv().NEXT_PUBLIC_SITE_URL);
  const v = await requireStaffApi();
  return ok(await createUnlockEvent(v.supabase, v.user.id, await parseJsonBody(req, UnlockInputSchema)), { status: 201 });
});
