import { authed } from "@/lib/http/authed";
import { ok, parseJsonBody } from "@/lib/http/api";
import { TransactionInputSchema } from "@/lib/portfolio/schemas";
import { addTransaction } from "@/server/portfolio";

export const dynamic = "force-dynamic";

export const POST = authed("POST /api/portfolio/transactions", { tier: "premium", perMinute: 60 }, async ({ req, viewer }) => {
  const input = await parseJsonBody(req, TransactionInputSchema);
  return ok(await addTransaction(viewer.supabase, viewer.user.id, input), { status: 201 });
});
