import "server-only";
import { z } from "zod";
import type { MembershipTier } from "@/config/plans";
import { hasTier, type Viewer } from "@/lib/auth/session";
import { logger } from "@/lib/logger";

export const ARTICLE_CATEGORIES = ["market", "tokenomics", "fundamentals", "risk", "methodology", "education"] as const;
export type ArticleCategory = (typeof ARTICLE_CATEGORIES)[number];

export const CATEGORY_NAMES: Record<ArticleCategory, string> = {
  market: "Market analysis",
  tokenomics: "Tokenomics",
  fundamentals: "Fundamentals",
  risk: "Risk",
  methodology: "Methodology",
  education: "Education",
};

export interface ArticleSummary {
  id: string;
  slug: string;
  title: string;
  summary: string;
  category: ArticleCategory;
  tags: string[];
  accessLevel: MembershipTier;
  status: "draft" | "published" | "archived";
  authorName: string;
  publishedAt: string | null;
  relatedCoinIds: string[];
  isAiAssisted: boolean;
}

export interface ArticleDetail extends ArticleSummary {
  dataSources: { name: string; url: string | null }[];
  /** Null when the viewer is not entitled (RLS returned no body). */
  body: string | null;
  updatedAt: string;
}

const SourcesSchema = z.array(z.object({ name: z.string().max(200), url: z.string().max(500).nullable().optional() })).catch([]);

const COLUMNS = "id, slug, title, summary, category, tags, access_level, status, author_name, published_at, related_coin_ids, is_ai_assisted";

function mapSummary(r: Record<string, unknown>): ArticleSummary {
  return {
    id: r.id as string,
    slug: r.slug as string,
    title: r.title as string,
    summary: r.summary as string,
    category: r.category as ArticleCategory,
    tags: (r.tags as string[]) ?? [],
    accessLevel: r.access_level as MembershipTier,
    status: r.status as ArticleSummary["status"],
    authorName: r.author_name as string,
    publishedAt: (r.published_at as string | null) ?? null,
    relatedCoinIds: (r.related_coin_ids as string[]) ?? [],
    isAiAssisted: Boolean(r.is_ai_assisted),
  };
}

export const ListArticlesQuery = z.object({
  q: z.string().trim().max(100).optional(),
  category: z.enum(ARTICLE_CATEGORIES).optional(),
  coin: z.string().regex(/^[a-z0-9-]{1,100}$/).optional(),
  page: z.coerce.number().int().min(1).max(1000).default(1),
  perPage: z.coerce.number().int().min(1).max(50).default(12),
});
export type ListArticlesQuery = z.infer<typeof ListArticlesQuery>;

export async function listPublishedArticles(viewer: Viewer, query: ListArticlesQuery): Promise<{ items: ArticleSummary[]; total: number; available: boolean }> {
  if (!viewer.supabase) return { items: [], total: 0, available: false };
  let req = viewer.supabase
    .from("research_articles")
    .select(COLUMNS, { count: "exact" })
    .eq("status", "published")
    .lte("published_at", new Date().toISOString())
    .order("published_at", { ascending: false })
    .range((query.page - 1) * query.perPage, query.page * query.perPage - 1);
  if (query.category) req = req.eq("category", query.category);
  if (query.coin) req = req.contains("related_coin_ids", [query.coin]);
  if (query.q) {
    // Escape PostgREST filter metacharacters and LIKE wildcards in user input.
    const term = query.q.replace(/[%_\\]/g, (c) => `\\${c}`).replace(/[,()]/g, " ");
    req = req.or(`title.ilike.%${term}%,summary.ilike.%${term}%`);
  }
  const { data, error, count } = await req;
  if (error) {
    logger.warn("articles_list_failed", { code: error.code });
    return { items: [], total: 0, available: false };
  }
  return { items: (data ?? []).map(mapSummary), total: count ?? 0, available: true };
}

export async function getArticleBySlug(viewer: Viewer, slug: string): Promise<ArticleDetail | null> {
  if (!viewer.supabase || !/^[a-z0-9-]{1,120}$/.test(slug)) return null;
  const { data, error } = await viewer.supabase.from("research_articles").select(`${COLUMNS}, data_sources, updated_at`).eq("slug", slug).maybeSingle();
  if (error || !data) return null;
  const summary = mapSummary(data);
  // Body access is enforced by RLS; we only query it if the viewer could be entitled.
  let body: string | null = null;
  if (summary.status !== "published" || hasTier(viewer, summary.accessLevel) || viewer.isStaff) {
    const b = await viewer.supabase.from("research_article_bodies").select("body_markdown").eq("article_id", summary.id).maybeSingle();
    body = (b.data?.body_markdown as string | undefined) ?? null;
  }
  return { ...summary, dataSources: SourcesSchema.parse(data.data_sources).map((s) => ({ name: s.name, url: s.url ?? null })), body, updatedAt: data.updated_at as string };
}
