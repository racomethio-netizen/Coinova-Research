import { z } from "zod";
import { requireAdminApi } from "@/lib/auth/session";
import { getEnv } from "@/lib/env";
import { assertSameOrigin, handler, ok, parseJsonBody } from "@/lib/http/api";
import { setFeatureFlag } from "@/server/admin";

export const dynamic = "force-dynamic";

const Body = z.object({ key: z.string().regex(/^[a-z][a-z0-9_]{1,62}$/), enabled: z.boolean() });

export const PATCH = handler("PATCH /api/admin/feature-flags", async (req: Request) => {
  assertSameOrigin(req, getEnv().NEXT_PUBLIC_SITE_URL);
  const v = await requireAdminApi();
  const { key, enabled } = await parseJsonBody(req, Body);
  await setFeatureFlag(v.supabase, v.user.id, key, enabled);
  return ok({ key, enabled });
});
