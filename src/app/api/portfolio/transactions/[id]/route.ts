import { z } from "zod";
import { authed } from "@/lib/http/authed";
import { ApiError, ok, parseJsonBody } from "@/lib/http/api";
import { TransactionPatchSchema } from "@/lib/portfolio/schemas";
import { deleteTransaction, updateTransaction } from "@/server/portfolio";

export const dynamic = "force-dynamic";

const txId = (v: string) => {
  const r = z.uuid().safeParse(v);
  if (!r.success) throw new ApiError("not_found", "Transaction not found.");
  return r.data;
};

export const PATCH = authed<{ id: string }>("PATCH /api/portfolio/transactions/[id]", { tier: "premium", perMinute: 60 }, async ({ req, viewer, params }) => {
  const patch = await parseJsonBody(req, TransactionPatchSchema);
  return ok(await updateTransaction(viewer.supabase, viewer.user.id, txId(params.id), patch));
});

export const DELETE = authed<{ id: string }>("DELETE /api/portfolio/transactions/[id]", { perMinute: 60 }, async ({ viewer, params }) => {
  await deleteTransaction(viewer.supabase, viewer.user.id, txId(params.id));
  return ok({ deleted: true });
});
