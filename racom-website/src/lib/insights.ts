import "server-only";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { z } from "zod";
import { readingTimeMinutes } from "./reading-time";

const INSIGHTS_DIR = path.join(process.cwd(), "src/content/insights");

/** Metadata every article must export: `export const metadata = { ... }`. */
export const articleMetaSchema = z.object({
  title: z.string().min(5),
  description: z.string().min(20).max(200),
  /** ISO date (YYYY-MM-DD) the article was published. */
  publishedAt: z.iso.date(),
  author: z.string().min(2),
  tags: z.array(z.string()).min(1),
});

export type ArticleMeta = z.infer<typeof articleMetaSchema>;
export type Article = ArticleMeta & { slug: string; readingMinutes: number };

const slugPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

async function loadArticle(slug: string): Promise<Article> {
  const [source, mod] = await Promise.all([
    readFile(path.join(INSIGHTS_DIR, `${slug}.mdx`), "utf8"),
    import(`@/content/insights/${slug}.mdx`) as Promise<{ metadata?: unknown }>,
  ]);
  const parsed = articleMetaSchema.safeParse(mod.metadata);
  if (!parsed.success) {
    throw new Error(`Invalid metadata in src/content/insights/${slug}.mdx: ${parsed.error.message}`);
  }
  return { ...parsed.data, slug, readingMinutes: readingTimeMinutes(source) };
}

/** All articles, newest first. Runs at build time for static pages. */
export async function getArticles(): Promise<Article[]> {
  const files = await readdir(INSIGHTS_DIR);
  const slugs = files.filter((f) => f.endsWith(".mdx")).map((f) => f.slice(0, -4));
  for (const slug of slugs) {
    if (!slugPattern.test(slug)) throw new Error(`Article filename must be kebab-case: ${slug}.mdx`);
  }
  const articles = await Promise.all(slugs.map(loadArticle));
  return articles.sort(
    (a, b) => b.publishedAt.localeCompare(a.publishedAt) || a.title.localeCompare(b.title),
  );
}

export async function getArticle(slug: string): Promise<Article | undefined> {
  if (!slugPattern.test(slug)) return undefined;
  return (await getArticles()).find((a) => a.slug === slug);
}

/** Articles sharing the most tags with the given one, then newest. */
export function relatedArticles(article: Article, all: Article[], limit = 2): Article[] {
  return all
    .filter((a) => a.slug !== article.slug)
    .map((a) => ({ a, score: a.tags.filter((t) => article.tags.includes(t)).length }))
    .sort((x, y) => y.score - x.score || y.a.publishedAt.localeCompare(x.a.publishedAt))
    .slice(0, limit)
    .map((x) => x.a);
}

export { formatDate } from "./insights-date";
