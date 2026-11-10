import { mapDbError } from "@/lib/db/errors";
import { authed } from "@/lib/http/authed";
import { ok, parseJsonBody } from "@/lib/http/api";
import { PaperAccountActionSchema } from "@/lib/paper/schema";

export const dynamic = "force-dynamic";

/** Open a virtual account, or reset it (the current one is archived, never deleted). */
export const POST = authed("POST /api/paper/account", { perMinute: 5 }, async ({ req, viewer }) => {
  const body = await parseJsonBody(req, PaperAccountActionSchema);
  const fn = body.action === "open" ? "open_paper_account" : "reset_paper_account";
  const { data, error } = await viewer.supabase.rpc(fn, { p_starting_balance: body.startingBalance });
  if (error) throw mapDbError(error, fn);
  return ok({ accountId: data as string, virtual: true }, { status: 201 });
});
