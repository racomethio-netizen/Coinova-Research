import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { CoinIdentity, FreshnessBadge, PriceChange, ProviderUnavailable } from "@/components/market/bits";
import { PriceChart } from "@/components/market/price-chart";
import { ScoreBreakdown } from "@/components/research/score";
import { Badge, Card, CardBody, CardHeader, Container, Notice, Stat } from "@/components/ui";
import { getViewer, hasTier } from "@/lib/auth/session";
import { fmtDate, fmtNumber, fmtPct, fmtUsd } from "@/lib/format";
import { getMarketService } from "@/lib/market";
import { isValidCoinId } from "@/lib/market/coingecko";
import { listPublishedArticles } from "@/lib/research/articles";
import { getScoreForCoin, getScoreHistory } from "@/lib/research/service";
import { computeSupplyMetrics, summarizeUnlocks, type Provenance, type Sourced } from "@/lib/research/tokenomics";
import { getUnlockEvents, getSupplyHistory } from "@/lib/research/unlocks";
import { WatchButton } from "./watch-button";

export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  return { title: id.replace(/-/g, " ") };
}

const PROVENANCE: Record<Provenance, { label: string; tone: "neutral" | "accent" | "warning" }> = {
  provider: { label: "Provider", tone: "neutral" },
  project_reported: { label: "Project-reported", tone: "accent" },
  estimated: { label: "Estimated", tone: "warning" },
  missing: { label: "Not available", tone: "neutral" },
};

function SupplyRow({ label, v, format }: { label: string; v: Sourced<number>; format: (n: number) => string }) {
  const p = PROVENANCE[v.provenance];
  return (
    <div className="flex items-start justify-between gap-4 py-2.5 text-sm">
      <dt className="text-muted">
        {label}
        {v.note ? <span className="block text-xs text-faint">{v.note}</span> : null}
      </dt>
      <dd className="flex items-center gap-2 text-right">
        <span className="tabular font-medium">{v.value === null ? "—" : format(v.value)}</span>
        {v.provenance !== "provider" ? <Badge tone={p.tone}>{p.label}</Badge> : null}
      </dd>
    </div>
  );
}

export default async function CoinPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!isValidCoinId(id)) notFound();
  const svc = getMarketService();
  if (!svc.isSupported(id)) notFound();

  const viewer = await getViewer();
  const [coinRes, historyRes, score, scoreHistory, articles, unlocks, supplyHistory] = await Promise.all([
    svc.getCoin(id).catch(() => null),
    svc.getHistory(id, "30d").catch(() => null),
    getScoreForCoin(viewer, id).catch(() => ({ kind: "unavailable" }) as const),
    getScoreHistory(viewer, id, 12).catch(() => null),
    listPublishedArticles(viewer, { coin: id, page: 1, perPage: 4 }).catch(() => ({ items: [], total: 0, available: false })),
    getUnlockEvents(viewer, id),
    getSupplyHistory(viewer, id),
  ]);

  if (!coinRes) {
    return (
      <Container className="py-8">
        <ProviderUnavailable what="Data for this asset" />
      </Container>
    );
  }
  const coin = coinRes.data;
  if (!coin) notFound();

  const supply = computeSupplyMetrics(coin, supplyHistory);
  const unlockSummary = unlocks.status === "ok" ? summarizeUnlocks(unlocks.events, coin, new Date().toISOString(), 180) : null;
  const premium = hasTier(viewer, "premium");

  return (
    <Container className="space-y-6 py-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-3">
          <nav aria-label="Breadcrumb" className="text-xs text-faint">
            <Link href="/markets" className="hover:text-fg">
              Markets
            </Link>{" "}
            / <span>{coin.name}</span>
          </nav>
          <h1 className="text-2xl font-semibold">
            <CoinIdentity name={coin.name} symbol={coin.symbol} imageUrl={coin.imageUrl} size={36} />
          </h1>
          <div className="flex flex-wrap items-baseline gap-3">
            <span className="tabular text-3xl font-semibold">{fmtUsd(coin.priceUsd)}</span>
            <PriceChange value={coin.change24hPct} className="text-sm" />
            <span className="text-xs text-faint">24h</span>
            {coin.marketCapRank ? <Badge>Rank #{coin.marketCapRank}</Badge> : null}
          </div>
          <FreshnessBadge source={coinRes.source} fetchedAt={coinRes.fetchedAt} stale={coinRes.stale} />
          {coin.lastUpdated ? <p className="text-xs text-faint">Provider last updated {fmtDate(coin.lastUpdated, { time: true })}</p> : null}
        </div>
        <WatchButton coinId={coin.id} signedIn={Boolean(viewer.user)} />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Price history" description="USD. Granularity is set by the data provider and varies by period." />
          <CardBody>
            {historyRes ? (
              <PriceChart coinId={coin.id} initial={historyRes.data.map((p) => ({ t: p.t, v: p.priceUsd }))} />
            ) : (
              <p className="text-sm text-muted">Historical data is temporarily unavailable.</p>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Market statistics" />
          <CardBody>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-5">
              <Stat label="Market cap" value={fmtUsd(coin.marketCapUsd, { compact: true })} />
              <Stat label="Fully diluted val." value={fmtUsd(coin.fullyDilutedValuationUsd, { compact: true })} />
              <Stat label="Volume 24h" value={fmtUsd(coin.volume24hUsd, { compact: true })} />
              <Stat label="24h range" value={coin.low24hUsd && coin.high24hUsd ? `${fmtUsd(coin.low24hUsd)} – ${fmtUsd(coin.high24hUsd)}` : "—"} />
              <Stat label="7d change" value={<PriceChange value={coin.change7dPct} />} />
              <Stat label="30d change" value={<PriceChange value={coin.change30dPct} />} />
              <Stat label="All-time high" value={fmtUsd(coin.athUsd)} sub={coin.athChangePct !== null ? `${fmtPct(coin.athChangePct)} from ATH` : undefined} />
            </dl>
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Research score" description="Transparent, rules-based heuristic. Not investment advice." />
          <CardBody>
            <ScoreBreakdown access={score} />
            {premium && scoreHistory && scoreHistory.length > 0 ? (
              <div className="mt-5 border-t border-line pt-4">
                <h3 className="text-sm font-medium">Score history</h3>
                <ul className="mt-2 space-y-1 text-sm">
                  {scoreHistory.map((h) => (
                    <li key={h.calculatedAt} className="flex justify-between text-muted">
                      <span>{fmtDate(h.calculatedAt, { time: true })}</span>
                      <span className="tabular text-fg">{h.overallScore === null ? "Insufficient data" : h.overallScore.toFixed(1)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Supply & tokenomics" description="Badges mark values that are estimated or not from the provider." />
          <CardBody>
            {!supply.hasFixedMaxSupply ? (
              <Notice tone="warning" className="mb-3">
                No fixed maximum supply is reported for this asset. Issuance may be uncapped or governed by protocol rules.
              </Notice>
            ) : null}
            <dl className="divide-y divide-line">
              <SupplyRow label="Circulating supply" v={supply.circulatingSupply} format={(n) => fmtNumber(n)} />
              <SupplyRow label="Total supply" v={supply.totalSupply} format={(n) => fmtNumber(n)} />
              <SupplyRow label="Maximum supply" v={supply.maxSupply} format={(n) => fmtNumber(n)} />
              <SupplyRow label="Circulating / max" v={supply.circulatingPctOfMax} format={(n) => fmtPct(n, { digits: 1 })} />
              <SupplyRow label="FDV / market cap" v={supply.fdvToMarketCap} format={(n) => `${n.toFixed(2)}×`} />
              <SupplyRow label="Remaining to max supply" v={supply.remainingToMaxSupply} format={(n) => fmtNumber(n, { compact: true })} />
              <SupplyRow label="Estimated annual inflation" v={supply.estimatedAnnualInflationPct} format={(n) => fmtPct(n, { digits: 2 })} />
            </dl>
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader title="Token unlocks" description="Only events from a configured provider or a cited project source are shown. We never estimate unlock schedules." />
        <CardBody>
          {unlocks.status === "not_configured" ? (
            <p className="text-sm text-muted">No unlock data source is connected yet. Unlock schedules will appear here once verified data is available.</p>
          ) : !unlockSummary || unlockSummary.events.length === 0 ? (
            <p className="text-sm text-muted">No scheduled unlocks are recorded for the next 180 days. This does not guarantee none exist.</p>
          ) : !premium ? (
            <p className="text-sm text-muted">
              {unlockSummary.events.length} recorded unlock event(s) in the next 180 days.{" "}
              <Link href="/pricing" className="text-accent underline">
                Upgrade to Premium
              </Link>{" "}
              for dates, amounts, and supply impact.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px] text-sm">
                <thead className="text-left text-xs uppercase text-faint">
                  <tr>
                    <th className="py-2">Date</th>
                    <th className="py-2 text-right">Amount</th>
                    <th className="py-2 text-right">% circulating</th>
                    <th className="py-2">Allocation</th>
                    <th className="py-2">Source</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {unlockSummary.events.map((e) => (
                    <tr key={e.id}>
                      <td className="py-2">{fmtDate(e.unlockAt)}</td>
                      <td className="tabular py-2 text-right">{fmtNumber(e.amount, { compact: true })}</td>
                      <td className="tabular py-2 text-right">{fmtPct(e.pctOfCirculating)}</td>
                      <td className="py-2">{e.allocation ?? "—"}</td>
                      <td className="py-2">
                        <Badge tone={PROVENANCE[e.sourceType].tone}>{PROVENANCE[e.sourceType].label}</Badge>{" "}
                        {e.sourceUrl ? (
                          <a href={e.sourceUrl} target="_blank" rel="noopener noreferrer nofollow" className="text-xs text-accent underline">
                            source
                          </a>
                        ) : null}{" "}
                        <span className="text-xs text-faint">{e.confidence} confidence</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardBody>
      </Card>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title={`About ${coin.name}`} />
          <CardBody className="space-y-3 text-sm text-muted">
            <p className="whitespace-pre-line">{coin.description ?? "No description available from the data provider."}</p>
            <div className="flex flex-wrap gap-2">
              {coin.categories.slice(0, 8).map((c) => (
                <Badge key={c}>{c}</Badge>
              ))}
            </div>
            <ul className="flex flex-wrap gap-4 text-xs">
              {coin.homepageUrl ? (
                <li>
                  <a className="text-accent underline" href={coin.homepageUrl} target="_blank" rel="noopener noreferrer nofollow">
                    Website
                  </a>
                </li>
              ) : null}
              {coin.whitepaperUrl ? (
                <li>
                  <a className="text-accent underline" href={coin.whitepaperUrl} target="_blank" rel="noopener noreferrer nofollow">
                    Whitepaper
                  </a>
                </li>
              ) : null}
              {coin.githubRepos[0] ? (
                <li>
                  <a className="text-accent underline" href={coin.githubRepos[0]} target="_blank" rel="noopener noreferrer nofollow">
                    Source code
                  </a>
                </li>
              ) : null}
            </ul>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Related research" />
          <CardBody>
            {articles.items.length === 0 ? (
              <p className="text-sm text-muted">No published research for this asset yet.</p>
            ) : (
              <ul className="space-y-3">
                {articles.items.map((a) => (
                  <li key={a.id}>
                    <Link href={`/research/${a.slug}`} className="text-sm font-medium hover:text-accent">
                      {a.title}
                    </Link>
                    <p className="text-xs text-faint">
                      {fmtDate(a.publishedAt)} {a.accessLevel !== "free" ? "· Premium" : ""}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>
    </Container>
  );
}
