import type { Metadata } from "next";
import { Card, CardBody, Container, PageHeader } from "@/components/ui";
import { CATEGORY_LABELS, DEFAULT_SCORING_CONFIG, SCORE_CATEGORIES } from "@/config/scoring";
import { siteConfig } from "@/config/site";

export const metadata: Metadata = { title: "Methodology & disclosures", description: "How our research works, its data limitations, and our disclosures." };

const CATEGORY_DETAIL: Record<(typeof SCORE_CATEGORIES)[number], string> = {
  tokenomics: "Circulating share of maximum (or total) supply, fully diluted valuation relative to market cap, and estimated annual supply inflation when enough recorded history exists.",
  liquidity: "Absolute 24-hour trading volume on a log scale and volume-to-market-cap turnover. Very high turnover is penalized as a possible sign of inorganic volume.",
  fundamentals: "Public developer activity (recent commits, contributors, repository stars), project age, and network size. Repository metrics miss private or off-GitHub development.",
  momentum: "7- and 30-day price change mapped through a smoothing function where 50 is neutral. Momentum describes the past; it does not predict the future.",
  unlockPressure: "Verified scheduled unlocks over the next 90 days relative to circulating supply. When no verified schedule exists, this category is reported as missing — not as zero risk.",
  riskVolatility: "Annualized volatility of daily log returns over recent history. Higher volatility scores lower.",
};

export default function AboutPage() {
  return (
    <Container className="max-w-4xl py-10">
      <PageHeader title="Methodology & disclosures" description={`What ${siteConfig.name} does, how scores are produced, and the limits of our data.`} />

      <div className="space-y-10 text-muted">
        <section aria-labelledby="purpose">
          <h2 id="purpose" className="mb-3 text-xl font-semibold text-fg">
            Purpose
          </h2>
          <p>
            We provide structured, transparent research on a defined set of cryptocurrencies: market data, supply analysis, a rules-based research score, and
            written research. Our goal is to make the evidence behind an assessment visible so you can judge it yourself.
          </p>
        </section>

        <section id="methodology" aria-labelledby="methodology-h">
          <h2 id="methodology-h" className="mb-3 text-xl font-semibold text-fg">
            Research score methodology (v{DEFAULT_SCORING_CONFIG.methodologyVersion})
          </h2>
          <p>
            The score ranges from 0 to 100 and is the weighted average of six category subscores. It is deterministic: the same inputs and methodology version
            always produce the same score, and every stored score records its methodology version, calculation time, data timestamp, and a hash of its inputs.
          </p>
          <div className="mt-5 grid gap-3">
            {SCORE_CATEGORIES.map((c) => (
              <Card key={c}>
                <CardBody>
                  <div className="flex items-baseline justify-between gap-4">
                    <h3 className="font-medium text-fg">{CATEGORY_LABELS[c]}</h3>
                    <span className="tabular text-sm">{(DEFAULT_SCORING_CONFIG.weights[c] * 100).toFixed(0)}% weight</span>
                  </div>
                  <p className="mt-1 text-sm">{CATEGORY_DETAIL[c]}</p>
                </CardBody>
              </Card>
            ))}
          </div>
          <h3 className="mt-6 font-medium text-fg">Missing and unreliable data</h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
            <li>Missing inputs are never treated as poor quality. A category without data is excluded and the remaining weights are renormalized.</li>
            <li>
              An overall score is produced only when at least {(DEFAULT_SCORING_CONFIG.minWeightCoverage * 100).toFixed(0)}% of total weight is available and the
              required categories ({DEFAULT_SCORING_CONFIG.requiredCategories.map((c) => CATEGORY_LABELS[c]).join(", ")}) are present. Otherwise the result is
              “Insufficient data”.
            </li>
            <li>Market inputs older than {DEFAULT_SCORING_CONFIG.maxMarketDataAgeMinutes / 60} hours, or with invalid values, are flagged as unreliable and excluded.</li>
            <li>Stablecoins are not scored.</li>
            <li>The weights are provisional and may change. Changes publish a new methodology configuration; historical scores keep their original version.</li>
          </ul>
        </section>

        <section aria-labelledby="limits">
          <h2 id="limits" className="mb-3 text-xl font-semibold text-fg">
            Data sources and limitations
          </h2>
          <ul className="list-disc space-y-1 pl-5">
            <li>Market data comes from third-party providers (currently CoinGecko when configured) and can be delayed, revised, or wrong.</li>
            <li>We cover a limited universe of roughly 100 assets. Coverage depends on what our provider reports.</li>
            <li>Every page shows when data was retrieved. If our provider is unavailable we show the last retrieved data clearly labeled as stale, or nothing.</li>
            <li>Supply figures are marked as provider-reported, project-reported, or estimated. Not every asset has a fixed maximum supply.</li>
            <li>Token unlock schedules are shown only when they come from a verified provider or a cited project source.</li>
            <li>Portfolio data is entered by you and is not verified against any blockchain.</li>
            <li>AI-assisted summaries are generated from data we retrieve, labeled as AI-generated, and are not independently verified research.</li>
          </ul>
        </section>

        <section aria-labelledby="disclosures">
          <h2 id="disclosures" className="mb-3 text-xl font-semibold text-fg">
            Disclosures
          </h2>
          <p>{siteConfig.disclaimer}</p>
          <p className="mt-3">
            Research is not personalized to your circumstances. Consider seeking advice from a licensed professional before making investment decisions. Staff
            holdings and conflicts of interest policies will be published before launch.
          </p>
          <p className="mt-3 text-sm">Operator: {siteConfig.legalEntity}</p>
        </section>
      </div>
    </Container>
  );
}
