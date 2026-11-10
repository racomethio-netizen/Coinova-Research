import { z } from "zod";
import { authed } from "@/lib/http/authed";
import { ok, parseJsonBody } from "@/lib/http/api";
import { deleteOwnAccount } from "@/server/account";

export const dynamic = "force-dynamic";

/** Account deletion for API clients (the mobile app; required by app-store rules). */
export const DELETE = authed("DELETE /api/account", { perMinute: 3 }, async ({ req, viewer }) => {
  await parseJsonBody(req, z.object({ confirm: z.literal("DELETE") }).strict());
  await deleteOwnAccount(viewer.supabase, viewer.user.id);
  return ok({ deleted: true });
});
