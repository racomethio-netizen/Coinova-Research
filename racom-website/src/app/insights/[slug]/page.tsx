import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getSiteUrl, site } from "@/config/site";
import { formatDate, getArticle, getArticles, relatedArticles } from "@/lib/insights";
import { jsonLd, pageMetadata } from "@/lib/seo";
import { Breadcrumbs } from "@/components/ui/page-hero";
import { ArticleCard } from "@/components/content/article-card";
import { ShareLinks } from "@/components/content/share-links";
import { FinalCta } from "@/components/home/final-cta";

export const dynamicParams = false;

export async function generateStaticParams() {
  return (await getArticles()).map((a) => ({ slug: a.slug }));
}

export async function generateMetadata({ params }: PageProps<"/insights/[slug]">): Promise<Metadata> {
  const { slug } = await params;
  const article = await getArticle(slug);
  if (!article) return {};
  return {
    ...pageMetadata({
      title: article.title,
      description: article.description,
      path: `/insights/${article.slug}`,
      type: "article",
      publishedTime: article.publishedAt,
    }),
    authors: [{ name: article.author }],
    keywords: article.tags,
  };
}

export default async function ArticlePage({ params }: PageProps<"/insights/[slug]">) {
  const { slug } = await params;
  const article = await getArticle(slug);
  if (!article) notFound();
  const { default: Content } = (await import(`@/content/insights/${slug}.mdx`)) as {
    default: React.ComponentType;
  };
  const related = relatedArticles(article, await getArticles());
  const siteUrl = getSiteUrl();
  const url = siteUrl ? `${siteUrl}/insights/${article.slug}` : null;

  const structuredData = {
    "@context": "https://schema.org",
    "@type": "Article",
    headline: article.title,
    description: article.description,
    datePublished: article.publishedAt,
    author: { "@type": "Organization", name: article.author },
    publisher: { "@type": "Organization", name: site.name },
    keywords: article.tags.join(", "),
    ...(url ? { url, mainEntityOfPage: url } : {}),
  };

  return (
    <>
      <article className="relative isolate">
        <div aria-hidden="true" className="bg-blueprint absolute inset-x-0 top-0 -z-10 h-[28rem]" />
        <header className="container-page max-w-3xl pt-14 pb-10 sm:pt-20">
          <Breadcrumbs items={[{ label: "Insights", href: "/insights" }, { label: article.title }]} />
          <ul className="flex flex-wrap gap-2" aria-label="Topics">
            {article.tags.map((t) => (
              <li
                key={t}
                className="rounded-full border border-violet/40 bg-violet/10 px-2.5 py-0.5 text-xs text-violet-soft"
              >
                {t}
              </li>
            ))}
          </ul>
          <h1 className="mt-5 font-display text-[2.2rem] leading-[1.08] font-semibold tracking-[-0.03em] text-balance text-ink sm:text-5xl">
            {article.title}
          </h1>
          <p className="mt-5 text-lg leading-relaxed text-muted">{article.description}</p>
          <div className="mt-8 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line pt-6 text-sm text-subtle">
            <span className="text-muted">{article.author}</span>
            <span aria-hidden="true">·</span>
            <time dateTime={article.publishedAt}>{formatDate(article.publishedAt)}</time>
            <span aria-hidden="true">·</span>
            <span>{article.readingMinutes} min read</span>
          </div>
        </header>
        <div className="container-page max-w-3xl pb-16">
          <div className="prose-racom">
            <Content />
          </div>
          <div className="mt-14 border-t border-line pt-8">
            <ShareLinks title={article.title} url={url} />
          </div>
        </div>
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLd(structuredData) }} />
      </article>

      {related.length > 0 && (
        <section aria-labelledby="related-title" className="border-t border-line py-16 sm:py-20">
          <div className="container-page">
            <h2 id="related-title" className="font-display text-2xl font-semibold tracking-tight text-ink">
              Related insights
            </h2>
            <div className="mt-10 grid gap-x-10 gap-y-12 md:grid-cols-2">
              {related.map((a) => (
                <ArticleCard key={a.slug} article={a} headingLevel="h3" />
              ))}
            </div>
          </div>
        </section>
      )}
      <FinalCta />
    </>
  );
}
