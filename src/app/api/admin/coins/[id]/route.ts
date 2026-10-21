import { requireStaffApi } from "@/lib/auth/session";
import { getEnv } from "@/lib/env";
import { ApiError, assertSameOrigin, handler, ok, parseJsonBody } from "@/lib/http/api";
import { isValidCoinId } from "@/lib/market/coingecko";
import { CoinPatchSchema, updateCoin } from "@/server/admin";

export const dynamic = "force-dynamic";

export const PATCH = handler("PATCH /api/admin/coins/[id]", async (req: Request, ctx: { params: Promise<{ id: string }> }) => {
  assertSameOrigin(req, getEnv().NEXT_PUBLIC_SITE_URL);
  const v = await requireStaffApi();
  const { id } = await ctx.params;
  if (!isValidCoinId(id)) throw new ApiError("not_found", "Coin not found.");
  await updateCoin(v.supabase, v.user.id, id, await parseJsonBody(req, CoinPatchSchema));
  return ok({ updated: true });
});
