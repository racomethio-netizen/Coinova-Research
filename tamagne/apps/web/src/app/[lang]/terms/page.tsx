import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Markdown } from "@/components/markdown";
import { SiteHeader } from "@/components/site-header";
import { Alert, Card } from "@/components/ui/card";
import { termsEn as body } from "@/content/terms";
import { getDictionary, hasLocale } from "@/i18n/dictionaries";

export const metadata: Metadata = { title: "Terms of Service · TAMAGNE" };

export default async function Page({ params }: PageProps<"/[lang]/terms">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  const t = getDictionary(lang);
  return (
    <>
      <SiteHeader lang={lang} t={t} path="/terms" />
      <main className="mx-auto max-w-3xl space-y-4 px-4 py-10">
        <Alert tone="info">{t.legal.draft}</Alert>
        {lang === "am" ? <Alert tone="info" lang="am">{t.legal.translationPending}</Alert> : null}
        <Card lang="en">
          <Markdown source={body} />
        </Card>
      </main>
    </>
  );
}
