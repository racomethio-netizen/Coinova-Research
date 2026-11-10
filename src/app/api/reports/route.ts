import { z } from "zod";
import { mapDbError } from "@/lib/db/errors";
import { authed } from "@/lib/http/authed";
import { ok, parseJsonBody } from "@/lib/http/api";

export const dynamic = "force-dynamic";

const ReportSchema = z.object({
  targetType: z.enum(["article", "signal", "user", "other"]),
  targetId: z.string().trim().min(1).max(200).optional(),
  reason: z.enum(["spam", "misleading", "abusive", "copyright", "security", "other"]),
  details: z.string().trim().max(2000).optional(),
});

/**
 * File an abuse report. The reporter is the authenticated user (set by the
 * database default); status and handling fields cannot be supplied. A daily
 * per-user cap is enforced in SQL.
 */
export const POST = authed("POST /api/reports", { perMinute: 5 }, async ({ req, viewer }) => {
  const body = await parseJsonBody(req, ReportSchema);
  const { data, error } = await viewer.supabase
    .from("abuse_reports")
    .insert({ target_type: body.targetType, target_id: body.targetId ?? null, reason: body.reason, details: body.details || null })
    .select("id, status, created_at")
    .single();
  if (error) throw mapDbError(error, "abuse report");
  return ok({ id: data.id, status: data.status, createdAt: data.created_at }, { status: 201 });
});
