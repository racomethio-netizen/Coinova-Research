import type { Metadata } from "next";
import Link from "next/link";
import { FreshnessBadge, ProviderUnavailable } from "@/components/market/bits";
import { MarketsTable } from "@/components/market/markets-table";
import { Container, inputClass, PageHeader, Pagination, buttonClass } from "@/components/ui";
import { getViewer, hasTier } from "@/lib/auth/session";
import { getMarketService } from "@/lib/market";
import { CHANGE_PERIODS, MarketsQuerySchema, queryMarkets, type MarketsQuery } from "@/lib/market/query";
import { getVisibleScores } from "@/lib/research/service";

export const metadata: Metadata = { title: "Markets", description: "Prices, market capitalization, volume, and research scores for supported cryptocurrencies." };
export const dynamic = "force-dynamic";

export default async function MarketsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const raw = await searchParams;
  const query = MarketsQuerySchema.parse(Object.fromEntries(Object.entries(raw).map(([k, v]) => [k, Array.isArray(v) ? v[0] : v])));
  const viewer = await getViewer();
  const svc = getMarketService();
  const [markets, scores] = await Promise.all([svc.getUniverseMarkets().catch(() => null), getVisibleScores(viewer).catch(() => new Map())]);

  const hrefFor = (patch: Partial<MarketsQuery>) => {
    const next = { ...query, ...patch };
    const p = new URLSearchParams();
    if (next.q) p.set("q", next.q);
    if (next.sort !== "rank") p.set("sort", next.sort);
    if (next.dir !== "asc") p.set("dir", next.dir);
    if (next.page > 1) p.set("page", String(next.page));
    if (next.period !== "24h") p.set("period", next.period);
    if (next.filter !== "all") p.set("filter", next.filter);
    const s = p.toString();
    return s ? `/markets?${s}` : "/markets";
  };

  return (
    <Container className="py-8">
      <PageHeader
        title="Markets"
        description="Supported cryptocurrencies ranked by market capitalization. Coverage is limited to our configured research universe."
        actions={markets ? <FreshnessBadge source={markets.source} fetchedAt={markets.fetchedAt} stale={markets.stale} /> : null}
      />

      <form method="get" action="/markets" className="mb-4 flex flex-wrap items-end gap-3" role="search">
        <div className="min-w-[200px] flex-1">
          <label htmlFor="q" className="mb-1 block text-xs text-faint">
            Search
          </label>
          <input id="q" name="q" defaultValue={query.q} placeholder="Name or symbol" className={inputClass} maxLength={50} />
        </div>
        <div>
          <label htmlFor="period" className="mb-1 block text-xs text-faint">
            Change period
          </label>
          <select id="period" name="period" defaultValue={query.period} className={inputClass}>
            {CHANGE_PERIODS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="filter" className="mb-1 block text-xs text-faint">
            Show
          </label>
          <select id="filter" name="filter" defaultValue={query.filter} className={inputClass}>
            <option value="all">All</option>
            <option value="gainers">Gainers</option>
            <option value="losers">Losers</option>
          </select>
        </div>
        <input type="hidden" name="sort" value={query.sort} />
        <input type="hidden" name="dir" value={query.dir} />
        <button type="submit" className={buttonClass("secondary")}>
          Apply
        </button>
        {query.q || query.filter !== "all" || query.period !== "24h" ? (
          <Link href="/markets" className={buttonClass("ghost")}>
            Reset
          </Link>
        ) : null}
      </form>

      {!markets ? (
        <ProviderUnavailable />
      ) : (
        (() => {
          const result = queryMarkets(markets.data, query);
          if (result.total === 0) {
            return <p className="rounded-lg border border-dashed border-line-strong px-6 py-12 text-center text-muted">No coins match your search.</p>;
          }
          return (
            <>
              <MarketsTable rows={result.items} query={{ ...query, page: result.page }} hrefFor={hrefFor} scores={scores} premium={hasTier(viewer, "premium")} />
              <Pagination page={result.page} totalPages={result.totalPages} hrefFor={(p) => hrefFor({ page: p })} />
              <p className="mt-4 text-xs text-faint">
                {result.total} assets. Prices in USD. Data may be delayed; it is not suitable for trading decisions.
              </p>
            </>
          );
        })()
      )}
    </Container>
  );
}
