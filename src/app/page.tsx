import Link from "next/link";
import { FreshnessBadge, PriceChange, ProviderUnavailable } from "@/components/market/bits";
import { MarketsTable } from "@/components/market/markets-table";
import { ArticleCard } from "@/components/research/article-card";
import { ButtonLink, Card, CardBody, Container, Stat } from "@/components/ui";
import { CATEGORY_LABELS, DEFAULT_SCORING_CONFIG, SCORE_CATEGORIES } from "@/config/scoring";
import { siteConfig } from "@/config/site";
import { getViewer, hasTier } from "@/lib/auth/session";
import { fmtPct, fmtUsd } from "@/lib/format";
import { getMarketService } from "@/lib/market";
import { MarketsQuerySchema } from "@/lib/market/query";
import { listPublishedArticles } from "@/lib/research/articles";
import { getVisibleScores } from "@/lib/research/service";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const viewer = await getViewer();
  const svc = getMarketService();
  const [markets, global, scores, articles] = await Promise.all([
    svc.getUniverseMarkets().catch(() => null),
    svc.getGlobal().catch(() => null),
    getVisibleScores(viewer).catch(() => new Map()),
    listPublishedArticles(viewer, { page: 1, perPage: 3 }).catch(() => ({ items: [], total: 0, available: false })),
  ]);
  const query = MarketsQuerySchema.parse({});
  const top = markets?.data.slice(0, 10) ?? [];

  return (
    <>
      <section className="border-b border-line">
        <Container className="grid gap-10 py-14 lg:grid-cols-2 lg:py-20">
          <div>
            <p className="text-sm font-medium text-accent">{siteConfig.name}</p>
            <h1 className="mt-3 text-3xl font-semibold tracking-tight sm:text-5xl">Crypto research you can audit.</h1>
            <p className="mt-5 max-w-xl text-lg text-muted">
              Transparent research scores, supply and unlock analysis, portfolio tracking, and alerts — with every data source and timestamp shown, and
              missing data reported rather than guessed.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <ButtonLink href={viewer.user ? "/dashboard" : "/register"}>{viewer.user ? "Go to dashboard" : "Create a free account"}</ButtonLink>
              <ButtonLink href="/markets" variant="secondary">
                Explore markets
              </ButtonLink>
            </div>
          </div>
          <Card className="self-start">
            <CardBody>
              <div className="mb-4 flex items-center justify-between">
                <h2 className="text-sm font-semibold">Market overview</h2>
                {global ? <FreshnessBadge source={global.source} fetchedAt={global.fetchedAt} stale={global.stale} /> : null}
              </div>
              {global ? (
                <dl className="grid grid-cols-2 gap-6">
                  <Stat label="Total market cap" value={fmtUsd(global.data.totalMarketCapUsd, { compact: true })} sub={<PriceChange value={global.data.marketCapChange24hPct} />} />
                  <Stat label="24h volume" value={fmtUsd(global.data.totalVolume24hUsd, { compact: true })} />
                  <Stat label="BTC dominance" value={fmtPct(global.data.btcDominancePct, { digits: 1 })} />
                  <Stat label="ETH dominance" value={fmtPct(global.data.ethDominancePct, { digits: 1 })} />
                </dl>
              ) : (
                <p className="text-sm text-muted">Global market data is temporarily unavailable.</p>
              )}
            </CardBody>
          </Card>
        </Container>
      </section>

      <Container className="space-y-14 py-12">
        <section aria-labelledby="top-heading">
          <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 id="top-heading" className="text-xl font-semibold">
                Top assets
              </h2>
              <p className="text-sm text-muted">
                {hasTier(viewer, "premium") ? "Research scores for all covered assets." : "Sample research scores are shown for a few assets. Premium unlocks all."}
              </p>
            </div>
            <Link href="/markets" className="text-sm text-accent hover:underline">
              View all markets →
            </Link>
          </div>
          {markets ? (
            <MarketsTable rows={top} query={query} hrefFor={() => "/markets"} scores={scores} premium={hasTier(viewer, "premium")} />
          ) : (
            <ProviderUnavailable />
          )}
        </section>

        <section aria-labelledby="research-heading">
          <div className="mb-4 flex items-end justify-between">
            <h2 id="research-heading" className="text-xl font-semibold">
              Featured research
            </h2>
            <Link href="/research" className="text-sm text-accent hover:underline">
              All research →
            </Link>
          </div>
          {articles.items.length ? (
            <div className="grid gap-4 md:grid-cols-3">
              {articles.items.map((a) => (
                <ArticleCard key={a.id} a={a} />
              ))}
            </div>
          ) : (
            <p className="rounded-lg border border-dashed border-line-strong px-6 py-10 text-center text-sm text-muted">
              {articles.available ? "No research has been published yet." : "Research articles will appear here once the database is connected."}
            </p>
          )}
        </section>

        <section aria-labelledby="method-heading" className="grid gap-8 lg:grid-cols-2">
          <div>
            <h2 id="method-heading" className="text-xl font-semibold">
              A methodology you can inspect
            </h2>
            <p className="mt-3 text-muted">
              Every score is calculated by deterministic, versioned rules from documented inputs. When inputs are missing or stale we say so and withhold the
              overall score instead of filling the gap. Scores are a research aid — not predictions and not financial advice.
            </p>
            <Link href="/about#methodology" className="mt-4 inline-block text-sm text-accent hover:underline">
              Read the full methodology →
            </Link>
          </div>
          <Card>
            <CardBody>
              <h3 className="mb-3 text-sm font-semibold">Score categories (v{DEFAULT_SCORING_CONFIG.methodologyVersion}, provisional weights)</h3>
              <ul className="space-y-2 text-sm">
                {SCORE_CATEGORIES.map((c) => (
                  <li key={c} className="flex justify-between gap-4">
                    <span className="text-muted">{CATEGORY_LABELS[c]}</span>
                    <span className="tabular font-medium">{(DEFAULT_SCORING_CONFIG.weights[c] * 100).toFixed(0)}%</span>
                  </li>
                ))}
              </ul>
            </CardBody>
          </Card>
        </section>

        <section className="rounded-lg border border-line bg-elevated px-6 py-10 text-center">
          <h2 className="text-xl font-semibold">Go deeper with Premium</h2>
          <p className="mx-auto mt-2 max-w-xl text-muted">Full research scores and history, premium articles, portfolios with P&amp;L, configurable alerts, and CSV export.</p>
          <ButtonLink href="/pricing" className="mt-6">
            See membership plans
          </ButtonLink>
        </section>
      </Container>
    </>
  );
}
