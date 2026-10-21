import { Notice, PageHeader } from "@/components/ui";
import { TIER_LIMITS } from "@/config/plans";
import { getAIProvider } from "@/lib/ai/provider";
import { requireUserPage } from "@/lib/auth/session";
import { getMarketService } from "@/lib/market";
import { listPublishedArticles } from "@/lib/research/articles";
import { AssistantForm } from "./client";

export default async function AssistantPage() {
  const viewer = await requireUserPage("/dashboard/assistant");
  const [markets, articles] = await Promise.all([
    getMarketService().getUniverseMarkets().catch(() => null),
    listPublishedArticles(viewer, { page: 1, perPage: 50 }).catch(() => ({ items: [], total: 0, available: false })),
  ]);
  const configured = Boolean(getAIProvider());
  return (
    <div className="space-y-6">
      <PageHeader
        title="AI research assistant"
        description={`Summaries and explanations generated from the data we retrieve. Your plan includes ${TIER_LIMITS[viewer.tier].aiRequestsPerDay} requests per day (UTC).`}
      />
      <Notice tone="warning">
        Answers are AI-generated from the data listed under each answer. They are not independently verified research and are not financial advice. The
        assistant does not have live prices beyond the timestamps shown.
      </Notice>
      {!configured ? (
        <Notice tone="neutral" title="Not configured">
          The AI provider is not configured on this server (AI_PROVIDER, ANTHROPIC_API_KEY, AI_MODEL). The assistant is unavailable until it is.
        </Notice>
      ) : (
        <AssistantForm
          coins={(markets?.data ?? []).map((c) => ({ id: c.id, label: `${c.name} (${c.symbol})` }))}
          articles={articles.items.map((a) => ({ slug: a.slug, title: a.title }))}
        />
      )}
    </div>
  );
}
