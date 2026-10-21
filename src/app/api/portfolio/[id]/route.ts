import { z } from "zod";
import { authed } from "@/lib/http/authed";
import { ApiError, ok, parseJsonBody } from "@/lib/http/api";
import { PortfolioInputSchema } from "@/lib/portfolio/schemas";
import { deletePortfolio, renamePortfolio } from "@/server/portfolio";

export const dynamic = "force-dynamic";

const id = (v: string) => {
  const r = z.uuid().safeParse(v);
  if (!r.success) throw new ApiError("not_found", "Portfolio not found.");
  return r.data;
};

export const PATCH = authed<{ id: string }>("PATCH /api/portfolio/[id]", { perMinute: 30 }, async ({ req, viewer, params }) => {
  const body = await parseJsonBody(req, PortfolioInputSchema);
  await renamePortfolio(viewer.supabase, viewer.user.id, id(params.id), body.name);
  return ok({ id: params.id, name: body.name });
});

export const DELETE = authed<{ id: string }>("DELETE /api/portfolio/[id]", { perMinute: 20 }, async ({ viewer, params }) => {
  await deletePortfolio(viewer.supabase, viewer.user.id, id(params.id));
  return ok({ deleted: true });
});
