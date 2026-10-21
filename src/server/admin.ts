import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { z } from "zod";
import { getPaidPlan } from "@/config/plans";
import { SCORE_CATEGORIES } from "@/config/scoring";
import { mapDbError } from "@/lib/db/errors";
import { ApiError } from "@/lib/http/api";
import { logger } from "@/lib/logger";
import { ARTICLE_CATEGORIES } from "@/lib/research/articles";
import { validateScoringConfig } from "@/lib/research/scoring";

/**
 * Admin operations. All writes use the ADMIN'S OWN session client so that
 * RLS (is_staff()/is_admin()) is enforced by the database in addition to the
 * server-side role check in each route. Every write appends an audit entry.
 */

export async function audit(sb: SupabaseClient, actorId: string, action: string, targetType: string | null, targetId: string | null, details: Record<string, unknown> = {}) {
  const { error } = await sb.from("admin_audit_logs").insert({ actor_id: actorId, action, target_type: targetType, target_id: targetId, details });
  if (error) logger.error("audit_log_write_failed", { action, code: error.code });
}

// ---------------------------------------------------------------- articles

const slug = z.string().trim().toLowerCase().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/, "Use lowercase letters, numbers, and hyphens.").max(120);
const SourceSchema = z.object({ name: z.string().trim().min(1).max(200), url: z.url().refine((u) => u.startsWith("https://"), "Source URLs must use https.").nullable().optional() });

export const ArticleInputSchema = z.object({
  slug,
  title: z.string().trim().min(3).max(200),
  summary: z.string().trim().min(10).max(600),
  category: z.enum(ARTICLE_CATEGORIES),
  tags: z.array(z.string().trim().min(1).max(40)).max(12).default([]),
  accessLevel: z.enum(["free", "premium", "founding"]),
  authorName: z.string().trim().min(1).max(80),
  relatedCoinIds: z.array(z.string().regex(/^[a-z0-9][a-z0-9-]{0,99}$/)).max(20).default([]),
  dataSources: z.array(SourceSchema).max(30).default([]),
  isAiAssisted: z.boolean().default(false),
  body: z.string().max(200_000),
});
export type ArticleInput = z.infer<typeof ArticleInputSchema>;

export async function createArticle(sb: SupabaseClient, actorId: string, input: ArticleInput) {
  const { data, error } = await sb
    .from("research_articles")
    .insert({
      slug: input.slug,
      title: input.title,
      summary: input.summary,
      category: input.category,
      tags: input.tags,
      access_level: input.accessLevel,
      author_name: input.authorName,
      author_id: actorId,
      related_coin_ids: input.relatedCoinIds,
      data_sources: input.dataSources,
      is_ai_assisted: input.isAiAssisted,
      status: "draft",
    })
    .select("id")
    .single();
  if (error) throw mapDbError(error, "createArticle");
  const body = await sb.from("research_article_bodies").insert({ article_id: data.id, body_markdown: input.body });
  if (body.error) throw mapDbError(body.error, "createArticleBody");
  await audit(sb, actorId, "article.create", "research_article", data.id, { slug: input.slug });
  return { id: data.id as string };
}

export const ArticlePatchSchema = ArticleInputSchema.partial().extend({ action: z.enum(["save", "publish", "unpublish", "archive"]).default("save") });

export async function updateArticle(sb: SupabaseClient, actorId: string, id: string, patch: z.infer<typeof ArticlePatchSchema>) {
  const { data: current, error: curErr } = await sb.from("research_articles").select("id, status, published_at").eq("id", id).maybeSingle();
  if (curErr) throw mapDbError(curErr, "updateArticle.load");
  if (!current) throw new ApiError("not_found", "Article not found.");
  const update: Record<string, unknown> = {};
  const map: [keyof ArticleInput, string][] = [
    ["slug", "slug"], ["title", "title"], ["summary", "summary"], ["category", "category"], ["tags", "tags"], ["accessLevel", "access_level"],
    ["authorName", "author_name"], ["relatedCoinIds", "related_coin_ids"], ["dataSources", "data_sources"], ["isAiAssisted", "is_ai_assisted"],
  ];
  for (const [k, col] of map) if (patch[k] !== undefined) update[col] = patch[k];
  if (patch.action === "publish") {
    update.status = "published";
    update.published_at = current.published_at ?? new Date().toISOString();
  } else if (patch.action === "unpublish") update.status = "draft";
  else if (patch.action === "archive") update.status = "archived";

  if (Object.keys(update).length) {
    const { error } = await sb.from("research_articles").update(update).eq("id", id);
    if (error) throw mapDbError(error, "updateArticle");
  }
  if (patch.body !== undefined) {
    const { error } = await sb.from("research_article_bodies").upsert({ article_id: id, body_markdown: patch.body }, { onConflict: "article_id" });
    if (error) throw mapDbError(error, "updateArticleBody");
  }
  await audit(sb, actorId, `article.${patch.action}`, "research_article", id, { fields: Object.keys(update) });
}

// ---------------------------------------------------------------- scoring

export const ScoringConfigInputSchema = z.object({
  methodologyVersion: z.string().regex(/^\d+\.\d+\.\d+$/),
  weights: z.object(Object.fromEntries(SCORE_CATEGORIES.map((c) => [c, z.number().min(0).max(1)])) as Record<(typeof SCORE_CATEGORIES)[number], z.ZodNumber>),
  minWeightCoverage: z.number().min(0.3).max(1),
  requiredCategories: z.array(z.enum(SCORE_CATEGORIES)).max(6),
  maxMarketDataAgeMinutes: z.number().int().min(10).max(1440),
  minHistoryDays: z.number().int().min(7).max(365),
  notes: z.string().max(1000).optional(),
});

export async function publishScoringConfig(sb: SupabaseClient, input: z.infer<typeof ScoringConfigInputSchema>) {
  const { notes, ...config } = input;
  try {
    validateScoringConfig(config);
  } catch (err) {
    if (err instanceof z.ZodError) throw new ApiError("validation_error", "Invalid scoring configuration.", err.issues.map((i) => ({ path: i.path.join("."), message: i.message })));
    throw err;
  }
  // Atomic: deactivates the old config, inserts the new one, writes the audit entry.
  const { data, error } = await sb.rpc("publish_scoring_config", { p_methodology_version: config.methodologyVersion, p_config: config, p_notes: notes ?? null });
  if (error) throw mapDbError(error, "publishScoringConfig");
  return { version: data as number };
}

// ---------------------------------------------------------------- unlocks

export const UnlockInputSchema = z.object({
  coinId: z.string().regex(/^[a-z0-9][a-z0-9-]{0,99}$/),
  unlockAt: z.iso.datetime({ offset: true }),
  amount: z.number().positive().finite(),
  unlockType: z.enum(["cliff", "linear", "other"]).default("cliff"),
  allocation: z.string().trim().max(80).optional(),
  sourceType: z.enum(["project_reported", "provider"]),
  sourceName: z.string().trim().max(120).optional(),
  sourceUrl: z.url().refine((u) => u.startsWith("https://"), "Source URL must use https."),
  confidence: z.enum(["high", "medium", "low"]),
  notes: z.string().max(1000).optional(),
});

export async function createUnlockEvent(sb: SupabaseClient, actorId: string, input: z.infer<typeof UnlockInputSchema>) {
  const { data, error } = await sb
    .from("token_unlock_events")
    .insert({
      coin_id: input.coinId,
      unlock_at: input.unlockAt,
      amount: input.amount,
      unlock_type: input.unlockType,
      allocation: input.allocation ?? null,
      source_type: input.sourceType,
      source_name: input.sourceName ?? null,
      source_url: input.sourceUrl,
      confidence: input.confidence,
      notes: input.notes ?? null,
      created_by: actorId,
    })
    .select("id")
    .single();
  if (error) throw mapDbError(error, "createUnlockEvent");
  await audit(sb, actorId, "unlock.create", "token_unlock_event", data.id, { coinId: input.coinId, unlockAt: input.unlockAt });
  return { id: data.id as string };
}

export async function deleteUnlockEvent(sb: SupabaseClient, actorId: string, id: string) {
  const { data, error } = await sb.from("token_unlock_events").delete().eq("id", id).select("id, coin_id").maybeSingle();
  if (error) throw mapDbError(error, "deleteUnlockEvent");
  if (!data) throw new ApiError("not_found", "Unlock event not found.");
  await audit(sb, actorId, "unlock.delete", "token_unlock_event", id, { coinId: data.coin_id });
}

// ---------------------------------------------------------------- flags & coins

export async function setFeatureFlag(sb: SupabaseClient, actorId: string, key: string, enabled: boolean) {
  const { data, error } = await sb.from("feature_flags").update({ enabled, updated_by: actorId }).eq("key", key).select("key").maybeSingle();
  if (error) throw mapDbError(error, "setFeatureFlag");
  if (!data) throw new ApiError("not_found", "Unknown feature flag.");
  await audit(sb, actorId, "feature_flag.set", "feature_flag", key, { enabled });
}

export const CoinPatchSchema = z
  .object({ isActive: z.boolean().optional(), isScoreSample: z.boolean().optional(), editorialNotes: z.string().max(2000).nullable().optional() })
  .refine((v) => Object.keys(v).length > 0, "No fields to update.");

export async function updateCoin(sb: SupabaseClient, actorId: string, id: string, patch: z.infer<typeof CoinPatchSchema>) {
  const update: Record<string, unknown> = {};
  if (patch.isActive !== undefined) update.is_active = patch.isActive;
  if (patch.isScoreSample !== undefined) update.is_score_sample = patch.isScoreSample;
  if (patch.editorialNotes !== undefined) update.editorial_notes = patch.editorialNotes;
  const { data, error } = await sb.from("cryptocurrencies").update(update).eq("id", id).select("id").maybeSingle();
  if (error) throw mapDbError(error, "updateCoin");
  if (!data) throw new ApiError("not_found", "Coin not found.");
  await audit(sb, actorId, "coin.update", "cryptocurrency", id, update);
}

// ---------------------------------------------------------------- overview

export async function getAdminOverview(sb: SupabaseClient) {
  const since30 = new Date(Date.now() - 30 * 86_400_000).toISOString();
  const [profiles, subs, events, jobs, deliveries, ai, audits] = await Promise.all([
    sb.from("profiles").select("id", { count: "exact", head: true }),
    sb.from("subscriptions").select("plan_id, tier, status, livemode, current_period_end, cancel_at_period_end"),
    sb.from("billing_events").select("id, provider, event_type, status, processing_note, occurred_at, received_at, livemode").order("received_at", { ascending: false }).limit(20),
    sb.from("background_jobs").select("id, job_name, status, attempts, started_at, finished_at, summary, error").order("started_at", { ascending: false }).limit(30),
    sb.from("notification_deliveries").select("channel, status").gte("created_at", since30).limit(10_000),
    sb.from("ai_usage_records").select("status, input_tokens, output_tokens").gte("created_at", since30).limit(10_000),
    sb.from("admin_audit_logs").select("id, actor_id, action, target_type, target_id, created_at").order("created_at", { ascending: false }).limit(25),
  ]);
  const now = Date.now();
  const activeSubs = (subs.data ?? []).filter((s) => ["active", "trialing", "past_due"].includes(s.status) && s.current_period_end && Date.parse(s.current_period_end) > now);
  const live = activeSubs.filter((s) => s.livemode);
  // Monthly recurring revenue estimate from live subscriptions at configured plan prices.
  const mrrCents = live.reduce((sum, s) => {
    const plan = getPaidPlan(s.plan_id);
    if (!plan) return sum;
    return sum + (plan.interval === "month" ? plan.priceCents : plan.priceCents / 12);
  }, 0);
  const byTier = { premium: 0, founding: 0 };
  for (const s of live) byTier[s.tier as "premium" | "founding"]++;
  const deliveryCounts: Record<string, Record<string, number>> = {};
  for (const d of deliveries.data ?? []) {
    deliveryCounts[d.channel] ??= {};
    deliveryCounts[d.channel]![d.status] = (deliveryCounts[d.channel]![d.status] ?? 0) + 1;
  }
  return {
    users: profiles.count ?? 0,
    subscribers: { live: live.length, test: activeSubs.length - live.length, byTier, pastDue: live.filter((s) => s.status === "past_due").length, cancelingAtPeriodEnd: live.filter((s) => s.cancel_at_period_end).length },
    mrrCents: Math.round(mrrCents),
    billingEvents: events.data ?? [],
    jobs: jobs.data ?? [],
    deliveries: deliveryCounts,
    ai: {
      requests30d: (ai.data ?? []).length,
      errors30d: (ai.data ?? []).filter((r) => r.status === "error").length,
      outputTokens30d: (ai.data ?? []).reduce((s, r) => s + (r.output_tokens ?? 0), 0),
    },
    audit: audits.data ?? [],
  };
}
