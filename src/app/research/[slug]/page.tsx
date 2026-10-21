import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Markdown } from "@/components/research/markdown";
import { Badge, ButtonLink, Container, Notice } from "@/components/ui";
import { siteConfig } from "@/config/site";
import { getViewer } from "@/lib/auth/session";
import { fmtDate } from "@/lib/format";
import { CATEGORY_NAMES, getArticleBySlug } from "@/lib/research/articles";

export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  const article = await getArticleBySlug(await getViewer(), slug);
  return article ? { title: article.title, description: article.summary } : { title: "Research" };
}

export default async function ArticlePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const viewer = await getViewer();
  const a = await getArticleBySlug(viewer, slug);
  if (!a) notFound();

  return (
    <Container className="max-w-3xl py-10">
      <nav aria-label="Breadcrumb" className="mb-6 text-xs text-faint">
        <Link href="/research" className="hover:text-fg">
          Research
        </Link>{" "}
        / {CATEGORY_NAMES[a.category]}
      </nav>
      {a.status !== "published" ? (
        <Notice tone="warning" title={`Preview: this article is ${a.status}`} className="mb-6">
          Only staff can see unpublished articles.
        </Notice>
      ) : null}
      <div className="mb-3 flex flex-wrap gap-2">
        <Badge>{CATEGORY_NAMES[a.category]}</Badge>
        {a.accessLevel !== "free" ? <Badge tone="accent">{a.accessLevel === "founding" ? "Founding" : "Premium"}</Badge> : null}
        {a.isAiAssisted ? <Badge tone="warning">AI-assisted draft, reviewed by staff</Badge> : null}
      </div>
      <h1 className="text-3xl font-semibold tracking-tight">{a.title}</h1>
      <p className="mt-3 text-lg text-muted">{a.summary}</p>
      <p className="mt-4 text-sm text-faint">
        By {a.authorName} · Published <time dateTime={a.publishedAt ?? undefined}>{fmtDate(a.publishedAt)}</time>
        {a.updatedAt && a.publishedAt && Date.parse(a.updatedAt) - Date.parse(a.publishedAt) > 86_400_000 ? <> · Updated {fmtDate(a.updatedAt)}</> : null}
      </p>
      {a.relatedCoinIds.length ? (
        <p className="mt-2 flex flex-wrap gap-2 text-xs">
          {a.relatedCoinIds.map((c) => (
            <Link key={c} href={`/coins/${c}`} className="text-accent hover:underline">
              {c}
            </Link>
          ))}
        </p>
      ) : null}

      <hr className="my-8 border-line" />

      {a.body !== null ? (
        <Markdown source={a.body} />
      ) : (
        <div className="rounded-lg border border-line bg-elevated px-6 py-10 text-center">
          <p className="font-medium">This is {a.accessLevel === "founding" ? "Founding Member" : "Premium"} research.</p>
          <p className="mt-2 text-sm text-muted">
            {viewer.user ? "Upgrade your membership to read the full article." : "Sign in or create an account, then upgrade to read the full article."}
          </p>
          <div className="mt-5 flex justify-center gap-3">
            <ButtonLink href="/pricing">View plans</ButtonLink>
            {!viewer.user ? (
              <ButtonLink href={`/login?next=/research/${a.slug}`} variant="secondary">
                Sign in
              </ButtonLink>
            ) : null}
          </div>
        </div>
      )}

      {a.dataSources.length ? (
        <section className="mt-10 rounded-lg border border-line p-5">
          <h2 className="text-sm font-semibold">Data sources</h2>
          <ul className="mt-2 space-y-1 text-sm">
            {a.dataSources.map((s) => (
              <li key={s.name}>
                {s.url && s.url.startsWith("https://") ? (
                  <a className="text-accent underline" href={s.url} target="_blank" rel="noopener noreferrer nofollow">
                    {s.name}
                  </a>
                ) : (
                  s.name
                )}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <p className="mt-8 text-xs leading-relaxed text-faint">{siteConfig.disclaimer}</p>
    </Container>
  );
}
