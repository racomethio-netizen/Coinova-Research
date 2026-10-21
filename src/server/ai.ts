import "server-only";
import { z } from "zod";
import type { Viewer } from "@/lib/auth/session";
import { AI_FEATURES, buildSystemPrompt, buildUserMessage, type AIFeature, type ContextBlock } from "@/lib/ai/prompt";
import { AIProviderError, getAIProvider, type AIProvider } from "@/lib/ai/provider";
import { mapDbError } from "@/lib/db/errors";
import { ApiError } from "@/lib/http/api";
import { logger } from "@/lib/logger";
import { getMarketService } from "@/lib/market";
import { getArticleBySlug } from "@/lib/research/articles";
import { getScoreForCoin } from "@/lib/research/service";
import { computeSupplyMetrics } from "@/lib/research/tokenomics";
import { getServiceClient } from "@/lib/supabase/server";

export const AssistantRequestSchema = z
  .object({
    feature: z.enum(AI_FEATURES),
    coinIds: z.array(z.string().regex(/^[a-z0-9][a-z0-9-]{0,99}$/)).max(4).default([]),
    articleSlug: z.string().regex(/^[a-z0-9-]{1,120}$/).optional(),
    question: z.string().max(1000).default(""),
  })
  .superRefine((v, ctx) => {
    if (["summary", "explain_score", "tokenomics"].includes(v.feature) && v.coinIds.length !== 1) ctx.addIssue({ code: "custom", path: ["coinIds"], message: "Select one coin." });
    if (v.feature === "compare" && v.coinIds.length < 2) ctx.addIssue({ code: "custom", path: ["coinIds"], message: "Select at least two coins to compare." });
    if (v.feature === "article_qa" && !v.articleSlug) ctx.addIssue({ code: "custom", path: ["articleSlug"], message: "Choose an article." });
  });
export type AssistantRequest = z.infer<typeof AssistantRequestSchema>;

export interface AssistantAnswer {
  answer: string;
  aiGenerated: true;
  model: string;
  generatedAt: string;
  servedByFallback: boolean;
  /** Exactly the data the model received — shown to the user as sources. */
  sources: { label: string; source: string; asOf: string | null }[];
}

export interface AssistantDeps {
  provider: AIProvider | null;
  /** Reserve one request against the daily quota; returns a usage record id. */
  reserve: (feature: AIFeature) => Promise<string>;
  /** Record completion metadata (never the prompt or answer text). */
  complete: (id: string, update: { status: "success" | "error"; model?: string; inputTokens?: number; outputTokens?: number; latencyMs: number; errorCode?: string }) => Promise<void>;
  gather: (req: AssistantRequest) => Promise<ContextBlock[]>;
  now?: () => Date;
}

export async function runAssistant(deps: AssistantDeps, req: AssistantRequest): Promise<AssistantAnswer> {
  if (!deps.provider) throw new ApiError("not_configured", "The AI research assistant is not configured on this server.");
  const blocks = await deps.gather(req);
  if (blocks.length === 0) {
    throw new ApiError("unavailable", "We could not retrieve the data needed to answer. The assistant only answers from retrieved data.");
  }
  const usageId = await deps.reserve(req.feature);
  const started = Date.now();
  const now = (deps.now ?? (() => new Date()))();
  try {
    const result = await deps.provider.complete({ system: buildSystemPrompt(now.toISOString()), user: buildUserMessage(req.feature, req.question, blocks) });
    await deps.complete(usageId, { status: "success", model: result.model, inputTokens: result.inputTokens, outputTokens: result.outputTokens, latencyMs: Date.now() - started });
    return {
      answer: result.text,
      aiGenerated: true,
      model: result.model,
      generatedAt: now.toISOString(),
      servedByFallback: result.servedByFallback,
      sources: blocks.map((b) => ({ label: b.label, source: b.source, asOf: b.asOf })),
    };
  } catch (err) {
    const kind = err instanceof AIProviderError ? err.kind : "unknown";
    await deps.complete(usageId, { status: "error", latencyMs: Date.now() - started, errorCode: kind }).catch(() => undefined);
    if (err instanceof AIProviderError) {
      logger.warn("ai_provider_error", { kind });
      if (err.kind === "rate_limited") throw new ApiError("rate_limited", err.message);
      if (err.kind === "refused") throw new ApiError("bad_request", "The AI model declined this request. Try rephrasing your question.");
      throw new ApiError("unavailable", "The AI research assistant is temporarily unavailable. Please try again later.");
    }
    throw err;
  }
}

/** Gather only real, retrieved data for the request. */
export async function gatherContext(viewer: Viewer, req: AssistantRequest): Promise<ContextBlock[]> {
  const svc = getMarketService();
  const blocks: ContextBlock[] = [];
  const source = svc.source === "mock" ? "Mock data (development — not real prices)" : "CoinGecko via CryptoScope";

  if (req.feature === "market_brief") {
    const [global, markets] = await Promise.all([svc.getGlobal().catch(() => null), svc.getUniverseMarkets().catch(() => null)]);
    if (global) blocks.push({ kind: "global_market", source, asOf: global.fetchedAt, label: "Global market overview", content: { ...global.data, stale: global.stale } });
    if (markets) {
      const top = markets.data.slice(0, 15).map((m) => ({ id: m.id, name: m.name, symbol: m.symbol, priceUsd: m.priceUsd, change24hPct: m.change24hPct, change7dPct: m.change7dPct, marketCapUsd: m.marketCapUsd }));
      blocks.push({ kind: "market_data", source, asOf: markets.fetchedAt, label: "Top assets by market cap", content: { assets: top, stale: markets.stale } });
    }
    return blocks;
  }

  if (req.feature === "article_qa" && req.articleSlug) {
    const a = await getArticleBySlug(viewer, req.articleSlug);
    // RLS returns no body when the viewer is not entitled; the assistant cannot bypass the paywall.
    if (a && a.body) {
      blocks.push({ kind: "article", source: `${a.authorName}, published ${a.publishedAt ?? "unpublished"}`, asOf: a.updatedAt, label: a.title, content: `${a.title}\n\n${a.summary}\n\n${a.body}`, untrusted: true });
    }
    if (a && !a.body) throw new ApiError("payment_required", "This article requires a membership tier you do not have.");
  }

  for (const id of req.coinIds) {
    if (!svc.isSupported(id)) continue;
    const [coin, score] = await Promise.all([svc.getCoin(id).catch(() => null), getScoreForCoin(viewer, id).catch(() => null)]);
    if (coin?.data) {
      const c = coin.data;
      blocks.push({
        kind: "market_data",
        source,
        asOf: c.lastUpdated ?? coin.fetchedAt,
        label: `${c.name} (${c.symbol}) market data`,
        content: {
          priceUsd: c.priceUsd, marketCapUsd: c.marketCapUsd, fullyDilutedValuationUsd: c.fullyDilutedValuationUsd, volume24hUsd: c.volume24hUsd,
          change24hPct: c.change24hPct, change7dPct: c.change7dPct, change30dPct: c.change30dPct, athUsd: c.athUsd, athChangePct: c.athChangePct,
          categories: c.categories.slice(0, 6), stale: coin.stale,
        },
      });
      blocks.push({ kind: "supply", source, asOf: c.lastUpdated ?? coin.fetchedAt, label: `${c.name} supply metrics`, content: computeSupplyMetrics(c) });
      if (c.description) blocks.push({ kind: "article", source: "Provider-supplied project description", asOf: coin.fetchedAt, label: `${c.name} description`, content: c.description, untrusted: true });
    }
    if (score?.kind === "visible") {
      const s = score.score;
      blocks.push({
        kind: "research_score",
        source: `${"CryptoScope"} research score methodology v${s.methodologyVersion}`,
        asOf: s.calculatedAt,
        label: `${id} research score`,
        content: { status: s.status, overallScore: s.overallScore, previousOverallScore: s.previousOverallScore, coverage: s.coverage, flags: s.flags, subscores: s.subscores },
      });
    }
  }
  return blocks;
}

export function defaultAssistantDeps(viewer: Viewer & { supabase: NonNullable<Viewer["supabase"]> }): AssistantDeps {
  return {
    provider: getAIProvider(),
    gather: (req) => gatherContext(viewer, req),
    reserve: async (feature) => {
      const { data, error } = await viewer.supabase.rpc("reserve_ai_request", { p_feature: feature });
      if (error) throw mapDbError(error, "reserve_ai_request");
      return data as string;
    },
    complete: async (id, u) => {
      const svc = getServiceClient();
      if (!svc) return;
      await svc
        .from("ai_usage_records")
        .update({ status: u.status, provider: "anthropic", model: u.model ?? null, input_tokens: u.inputTokens ?? null, output_tokens: u.outputTokens ?? null, latency_ms: u.latencyMs, error_code: u.errorCode ?? null, completed_at: new Date().toISOString() })
        .eq("id", id);
    },
  };
}
