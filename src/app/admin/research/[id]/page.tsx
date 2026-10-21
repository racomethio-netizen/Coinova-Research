import { notFound } from "next/navigation";
import { z } from "zod";
import { Badge, Card, CardBody, PageHeader } from "@/components/ui";
import { requireStaffPage } from "@/lib/auth/session";
import { ArticleEditor } from "../../client";

export default async function EditArticlePage({ params }: { params: Promise<{ id: string }> }) {
  const viewer = await requireStaffPage();
  const id = z.uuid().safeParse((await params).id);
  if (!id.success) notFound();
  const [{ data: a }, { data: body }] = await Promise.all([
    viewer.supabase.from("research_articles").select("*").eq("id", id.data).maybeSingle(),
    viewer.supabase.from("research_article_bodies").select("body_markdown").eq("article_id", id.data).maybeSingle(),
  ]);
  if (!a) notFound();
  const sources = ((a.data_sources as { name: string; url?: string | null }[]) ?? []).map((s) => (s.url ? `${s.name} | ${s.url}` : s.name)).join("\n");
  return (
    <div className="space-y-6">
      <PageHeader title="Edit article" actions={<Badge tone={a.status === "published" ? "positive" : "warning"}>{a.status}</Badge>} />
      <Card>
        <CardBody>
          <ArticleEditor
            initial={{
              id: a.id, slug: a.slug, title: a.title, summary: a.summary, category: a.category, tags: (a.tags ?? []).join(", "),
              accessLevel: a.access_level, authorName: a.author_name, relatedCoinIds: (a.related_coin_ids ?? []).join(", "),
              dataSources: sources, isAiAssisted: a.is_ai_assisted, body: body?.body_markdown ?? "", status: a.status,
            }}
          />
        </CardBody>
      </Card>
    </div>
  );
}
