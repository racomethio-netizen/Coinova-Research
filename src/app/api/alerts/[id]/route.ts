import { z } from "zod";
import { CHANNELS } from "@/lib/alerts/evaluate";
import { authed } from "@/lib/http/authed";
import { ApiError, ok, parseJsonBody } from "@/lib/http/api";
import { deleteAlert, updateAlert } from "@/server/alerts";

export const dynamic = "force-dynamic";

const PatchSchema = z
  .object({
    threshold: z.number().finite().optional(),
    channels: z.array(z.enum(CHANNELS)).min(1).max(3).transform((c) => [...new Set(c)]).optional(),
    triggerMode: z.enum(["once", "recurring"]).optional(),
    cooldownMinutes: z.number().int().min(5).max(10_080).optional(),
    isActive: z.boolean().optional(),
  })
  .refine((v) => Object.keys(v).length > 0, { message: "No fields to update." });

const aid = (v: string) => {
  const r = z.uuid().safeParse(v);
  if (!r.success) throw new ApiError("not_found", "Alert not found.");
  return r.data;
};

export const PATCH = authed<{ id: string }>("PATCH /api/alerts/[id]", { perMinute: 60 }, async ({ req, viewer, params }) => {
  const patch = await parseJsonBody(req, PatchSchema);
  // Re-enabling or editing alerts requires an active Premium tier; disabling is always allowed.
  if (!(Object.keys(patch).length === 1 && patch.isActive === false) && viewer.tier === "free") {
    throw new ApiError("payment_required", "Editing alerts requires a Premium membership.");
  }
  return ok(await updateAlert(viewer.supabase, viewer.user.id, aid(params.id), patch));
});

export const DELETE = authed<{ id: string }>("DELETE /api/alerts/[id]", { perMinute: 60 }, async ({ viewer, params }) => {
  await deleteAlert(viewer.supabase, viewer.user.id, aid(params.id));
  return ok({ deleted: true });
});
