import type { Metadata } from "next";
import { getArticles } from "@/lib/insights";
import { pageMetadata } from "@/lib/seo";
import { PageHero } from "@/components/ui/page-hero";
import { Section } from "@/components/ui/section";
import { ArticleCard } from "@/components/content/article-card";
import { FinalCta } from "@/components/home/final-cta";

export const metadata: Metadata = pageMetadata({
  title: "Insights",
  description:
    "Practical writing from Racom Technology on software engineering, AI automation, product scoping and performance.",
  path: "/insights",
});

export default async function InsightsPage() {
  const articles = await getArticles();
  return (
    <>
      <PageHero
        eyebrow="Insights"
        title="Notes on building software that works."
        description="Practical, experience-based writing on engineering, product and automation — without the hype."
      />
      <Section className="pt-14 sm:pt-16">
        {articles.length === 0 ? (
          <p className="text-muted">No articles have been published yet.</p>
        ) : (
          <div className="grid gap-x-10 gap-y-14 md:grid-cols-2 lg:grid-cols-3">
            {articles.map((a) => (
              <ArticleCard key={a.slug} article={a} />
            ))}
          </div>
        )}
      </Section>
      <FinalCta />
    </>
  );
}
