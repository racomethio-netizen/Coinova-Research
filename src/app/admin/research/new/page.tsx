import { Card, CardBody, PageHeader } from "@/components/ui";
import { requireStaffPage } from "@/lib/auth/session";
import { ArticleEditor } from "../../client";

export default async function NewArticlePage() {
  await requireStaffPage();
  return (
    <div className="space-y-6">
      <PageHeader title="New article" description="Articles are created as drafts. Publish when reviewed." />
      <Card>
        <CardBody>
          <ArticleEditor initial={{ slug: "", title: "", summary: "", category: "market", tags: "", accessLevel: "free", authorName: "CryptoScope Research", relatedCoinIds: "", dataSources: "", isAiAssisted: false, body: "" }} />
        </CardBody>
      </Card>
    </div>
  );
}
