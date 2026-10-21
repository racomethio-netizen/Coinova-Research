import Link from "next/link";
import { Badge, ButtonLink, Card, CardBody, PageHeader } from "@/components/ui";
import { requireStaffPage } from "@/lib/auth/session";
import { fmtDate } from "@/lib/format";

export default async function AdminResearchPage() {
  const viewer = await requireStaffPage();
  const { data } = await viewer.supabase.from("research_articles").select("id, slug, title, status, access_level, published_at, updated_at").order("updated_at", { ascending: false }).limit(200);
  return (
    <div className="space-y-6">
      <PageHeader title="Research articles" actions={<ButtonLink href="/admin/research/new">New article</ButtonLink>} />
      <Card>
        <CardBody className="overflow-x-auto">
          <table className="w-full min-w-[600px] text-sm">
            <thead className="text-left text-xs uppercase text-faint"><tr><th className="py-2">Title</th><th>Status</th><th>Access</th><th>Published</th><th>Updated</th></tr></thead>
            <tbody className="divide-y divide-line">
              {(data ?? []).length === 0 ? <tr><td colSpan={5} className="py-3 text-muted">No articles yet.</td></tr> : null}
              {(data ?? []).map((a) => (
                <tr key={a.id}>
                  <td className="py-2"><Link href={`/admin/research/${a.id}`} className="hover:text-accent">{a.title}</Link></td>
                  <td><Badge tone={a.status === "published" ? "positive" : a.status === "draft" ? "warning" : "neutral"}>{a.status}</Badge></td>
                  <td>{a.access_level}</td>
                  <td className="text-xs">{fmtDate(a.published_at)}</td>
                  <td className="text-xs">{fmtDate(a.updated_at, { time: true })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardBody>
      </Card>
    </div>
  );
}
