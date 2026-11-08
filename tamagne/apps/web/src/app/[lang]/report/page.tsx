import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ReportForm } from "@/components/report-form";
import { SiteHeader } from "@/components/site-header";
import { Card } from "@/components/ui/card";
import { getDictionary, hasLocale } from "@/i18n/dictionaries";

export const metadata: Metadata = { robots: { index: false } };

export default async function ReportPage({ params, searchParams }: PageProps<"/[lang]/report">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  const sp = await searchParams;
  const seller = typeof sp.seller === "string" ? sp.seller.slice(0, 64) : "";
  const t = getDictionary(lang);
  return (
    <>
      <SiteHeader lang={lang} t={t} path="/report" />
      <main className="mx-auto max-w-xl px-4 py-10">
        <h1 className="text-2xl font-bold">{t.report.title}</h1>
        <p className="mt-2 text-muted">{t.report.lead}</p>
        <Card className="mt-6">
          <ReportForm t={t.report} seller={seller} />
        </Card>
        <p className="mt-4 text-sm text-muted">{t.report.emergency}</p>
      </main>
    </>
  );
}
