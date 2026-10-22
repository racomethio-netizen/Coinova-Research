import Link from "next/link";
import type { Article } from "@/lib/insights";
import { formatDate } from "@/lib/insights-date";

export function ArticleCard({
  article,
  headingLevel = "h2",
}: {
  article: Article;
  headingLevel?: "h2" | "h3";
}) {
  const Heading = headingLevel;
  return (
    <article className="group relative flex h-full flex-col border-t border-line pt-6">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[0.72rem] tracking-[0.12em] text-subtle uppercase">
        <time dateTime={article.publishedAt}>{formatDate(article.publishedAt)}</time>
        <span aria-hidden="true">·</span>
        <span>{article.readingMinutes} min read</span>
      </div>
      <Heading className="mt-4 font-display text-xl leading-snug font-semibold tracking-tight text-balance text-ink transition-colors group-hover:text-violet-soft sm:text-2xl">
        <Link href={`/insights/${article.slug}`} className="after:absolute after:inset-0 after:content-['']">
          {article.title}
        </Link>
      </Heading>
      <p className="mt-3 leading-relaxed text-muted">{article.description}</p>
      <ul className="mt-5 flex flex-wrap gap-2" aria-label="Topics">
        {article.tags.map((t) => (
          <li key={t} className="rounded-full border border-line-strong px-2.5 py-0.5 text-xs text-muted">
            {t}
          </li>
        ))}
      </ul>
    </article>
  );
}
