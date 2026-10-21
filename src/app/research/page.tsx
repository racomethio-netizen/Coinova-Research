import type { Metadata } from "next";
import Link from "next/link";
import { ArticleCard } from "@/components/research/article-card";
import { buttonClass, Container, EmptyState, inputClass, PageHeader, Pagination } from "@/components/ui";
import { getViewer } from "@/lib/auth/session";
import { ARTICLE_CATEGORIES, CATEGORY_NAMES, ListArticlesQuery, listPublishedArticles } from "@/lib/research/articles";

export const metadata: Metadata = { title: "Research", description: "Cryptocurrency research articles covering markets, tokenomics, fundamentals, and risk." };
export const dynamic = "force-dynamic";

export default async function ResearchPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const raw = await searchParams;
  const parsed = ListArticlesQuery.safeParse(Object.fromEntries(Object.entries(raw).map(([k, v]) => [k, Array.isArray(v) ? v[0] : v])));
  const query = parsed.success ? parsed.data : ListArticlesQuery.parse({});
  const viewer = await getViewer();
  const { items, total, available } = await listPublishedArticles(viewer, query);
  const totalPages = Math.max(1, Math.ceil(total / query.perPage));
  const href = (patch: Record<string, string | number | undefined>) => {
    const p = new URLSearchParams();
    const next = { q: query.q, category: query.category, page: query.page, ...patch };
    if (next.q) p.set("q", String(next.q));
    if (next.category) p.set("category", String(next.category));
    if (next.page && Number(next.page) > 1) p.set("page", String(next.page));
    const s = p.toString();
    return s ? `/research?${s}` : "/research";
  };

  return (
    <Container className="py-8">
      <PageHeader title="Research" description="Analysis from our research team. Premium articles show a summary to everyone and the full text to members." />
      <form method="get" className="mb-4 flex flex-wrap items-end gap-3" role="search">
        <div className="min-w-[220px] flex-1">
          <label htmlFor="q" className="mb-1 block text-xs text-faint">
            Search articles
          </label>
          <input id="q" name="q" defaultValue={query.q} className={inputClass} maxLength={100} />
        </div>
        {query.category ? <input type="hidden" name="category" value={query.category} /> : null}
        <button className={buttonClass("secondary")} type="submit">
          Search
        </button>
      </form>
      <nav aria-label="Categories" className="mb-6 flex flex-wrap gap-2">
        <Link href={href({ category: undefined, page: 1 })} aria-current={!query.category ? "page" : undefined} className={buttonClass(!query.category ? "primary" : "secondary", "sm")}>
          All
        </Link>
        {ARTICLE_CATEGORIES.map((c) => (
          <Link key={c} href={href({ category: c, page: 1 })} aria-current={query.category === c ? "page" : undefined} className={buttonClass(query.category === c ? "primary" : "secondary", "sm")}>
            {CATEGORY_NAMES[c]}
          </Link>
        ))}
      </nav>
      {!available ? (
        <EmptyState title="Research is not available right now" description="The research database is not connected or could not be reached." />
      ) : items.length === 0 ? (
        <EmptyState title="No articles found" description={query.q || query.category ? "Try a different search or category." : "No research has been published yet."} />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {items.map((a) => (
              <ArticleCard key={a.id} a={a} />
            ))}
          </div>
          <Pagination page={query.page} totalPages={totalPages} hrefFor={(p) => href({ page: p })} />
        </>
      )}
    </Container>
  );
}
