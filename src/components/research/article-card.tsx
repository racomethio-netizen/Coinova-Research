import Link from "next/link";
import { fmtDate } from "@/lib/format";
import { CATEGORY_NAMES, type ArticleSummary } from "@/lib/research/articles";
import { Badge } from "../ui";

export function ArticleCard({ a }: { a: ArticleSummary }) {
  return (
    <article className="flex h-full flex-col rounded-lg border border-line bg-elevated p-5 transition-colors hover:border-line-strong">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <Badge>{CATEGORY_NAMES[a.category]}</Badge>
        {a.accessLevel !== "free" ? <Badge tone="accent">{a.accessLevel === "founding" ? "Founding" : "Premium"}</Badge> : <Badge tone="positive">Free</Badge>}
        {a.isAiAssisted ? <Badge tone="warning">AI-assisted</Badge> : null}
      </div>
      <h3 className="text-base font-semibold leading-snug">
        <Link href={`/research/${a.slug}`} className="hover:text-accent">
          {a.title}
        </Link>
      </h3>
      <p className="mt-2 line-clamp-3 flex-1 text-sm text-muted">{a.summary}</p>
      <p className="mt-4 text-xs text-faint">
        {a.authorName} · <time dateTime={a.publishedAt ?? undefined}>{fmtDate(a.publishedAt)}</time>
      </p>
    </article>
  );
}
