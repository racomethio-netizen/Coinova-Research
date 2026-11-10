import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { z } from "zod";
import { mapDbError } from "@/lib/db/errors";
import { ApiError } from "@/lib/http/api";
import { audit } from "./admin";

/**
 * NEXUS admin actions. Callers must pass requireAdminApi() (MFA-verified admin).
 * Each action uses the admin's own session, so RLS and database rules (insert-only
 * ledger, model lifecycle, terms-review authorship) still apply, and each write
 * is audited.
 */

export const AnnotationSchema = z.object({ kind: z.enum(["note", "correction", "retraction"]), body: z.string().trim().min(10).max(4000) }).strict();
export const ModelStatusSchema = z.object({ status: z.enum(["shadow", "approved", "retired"]) }).strict();
export const ReportHandleSchema = z.object({ status: z.enum(["reviewing", "resolved", "dismissed"]), note: z.string().trim().max(2000).optional() }).strict();
export const PairPatchSchema = z.object({ enabled: z.boolean() }).strict();
export const TermsReviewSchema = z
  .object({ notes: z.string().trim().min(20, "Describe the permitted use, regions, and the evidence (at least 20 characters).").max(4000), confirm: z.literal(true) })
  .strict();

export async function annotateSignal(sb: SupabaseClient, actorId: string, signalId: string, input: z.infer<typeof AnnotationSchema>) {
  const { error } = await sb.from("signal_annotations").insert({ signal_id: signalId, kind: input.kind, body: input.body });
  if (error) throw mapDbError(error, "annotate signal");
  await audit(sb, actorId, `signal.${input.kind}`, "signal", signalId, { length: input.body.length });
}

export async function setModelStatus(sb: SupabaseClient, actorId: string, modelId: string, status: z.infer<typeof ModelStatusSchema>["status"]) {
  const patch: Record<string, unknown> = { status };
  if (status === "approved") Object.assign(patch, { approved_by: actorId, approved_at: new Date().toISOString() });
  if (status === "retired") patch.retired_at = new Date().toISOString();
  const { data, error } = await sb.from("model_versions").update(patch).eq("id", modelId).select("id");
  if (error) {
    // Lifecycle rules live in the database; surface them (they contain no sensitive data).
    if (/model|transition|approval|validation report|checksum/i.test(error.message)) throw new ApiError("validation_error", error.message.slice(0, 200));
    throw mapDbError(error, "set model status");
  }
  if (!data?.length) throw new ApiError("not_found", "Model version not found.");
  await audit(sb, actorId, `model.${status}`, "model_version", modelId);
}

export async function handleReport(sb: SupabaseClient, actorId: string, id: string, input: z.infer<typeof ReportHandleSchema>) {
  const closed = input.status !== "reviewing";
  const { data, error } = await sb
    .from("abuse_reports")
    .update({ status: input.status, handled_by: actorId, handled_at: closed ? new Date().toISOString() : null, resolution_note: input.note ?? null })
    .eq("id", id)
    .select("id");
  if (error) throw mapDbError(error, "handle report");
  if (!data?.length) throw new ApiError("not_found", "Report not found.");
  await audit(sb, actorId, `abuse_report.${input.status}`, "abuse_report", id);
}

export async function setPairEnabled(sb: SupabaseClient, actorId: string, pairId: number, enabled: boolean) {
  const { data, error } = await sb.from("market_pairs").update({ enabled }).eq("id", pairId).select("id, symbol");
  if (error) throw mapDbError(error, "set pair");
  if (!data?.length) throw new ApiError("not_found", "Pair not found.");
  await audit(sb, actorId, enabled ? "pair.enable" : "pair.disable", "market_pair", String(pairId), { symbol: data[0]!.symbol });
}

/** Gate G-4: records that the owner reviewed the source's terms. Enables production ingestion. */
export async function recordTermsReview(sb: SupabaseClient, actorId: string, key: string, notes: string) {
  const { data, error } = await sb
    .from("market_sources")
    .update({ terms_reviewed_at: new Date().toISOString(), terms_reviewed_by: actorId, terms_notes: notes })
    .eq("key", key)
    .select("key");
  if (error) throw mapDbError(error, "terms review");
  if (!data?.length) throw new ApiError("not_found", "Source not found.");
  await audit(sb, actorId, "market_source.terms_review", "market_source", key, { notesLength: notes.length });
}
