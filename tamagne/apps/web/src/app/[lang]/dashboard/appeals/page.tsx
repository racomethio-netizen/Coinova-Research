import { notFound, redirect } from "next/navigation";
import { AppealForm } from "@/components/appeal-form";
import { SiteHeader } from "@/components/site-header";
import { Card, CardTitle } from "@/components/ui/card";
import { getDictionary, hasLocale } from "@/i18n/dictionaries";
import { adminGet, getMySeller, getViewer } from "@/lib/server-api";

interface Appeal { id: string; subjectType: keyof ReturnType<typeof getDictionary>["appeals"]["subjects"]; reason: string; status: keyof ReturnType<typeof getDictionary>["appeals"]["statuses"]; decisionNote: string | null; createdAt: string }

export default async function AppealsPage({ params }: PageProps<"/[lang]/dashboard/appeals">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  if (!(await getViewer())) redirect(`/${lang}/login`);
  const seller = await getMySeller();
  if (!seller) redirect(`/${lang}/dashboard/setup`);
  const t = getDictionary(lang);
  const a = t.appeals;
  const list = await adminGet<Appeal[]>("/appeals/me");
  const fmt = new Intl.DateTimeFormat(lang === "am" ? "am-ET" : "en-GB", { dateStyle: "medium", timeZone: "Africa/Addis_Ababa" });
  return (
    <>
      <SiteHeader lang={lang} t={t} path="/dashboard/appeals" signedIn />
      <main className="mx-auto max-w-3xl space-y-6 px-4 py-10">
        <div>
          <h1 className="text-2xl font-bold">{a.title}</h1>
          <p className="mt-2 text-muted">{a.lead}</p>
        </div>
        <Card>
          <AppealForm t={a} suspended={seller.accountStatus === "SUSPENDED"} />
        </Card>
        <Card>
          <CardTitle>{a.manage}</CardTitle>
          {list.ok && list.data.length ? (
            <ul className="mt-3 divide-y divide-line" data-testid="my-appeals">
              {list.data.map((x) => (
                <li key={x.id} className="py-3 text-sm">
                  <p><strong>{a.subjects[x.subjectType]}</strong> · {a.statuses[x.status]} · {fmt.format(new Date(x.createdAt))}</p>
                  {x.decisionNote ? <p className="mt-1">{x.decisionNote}</p> : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-sm text-muted">{a.none}</p>
          )}
        </Card>
      </main>
    </>
  );
}
