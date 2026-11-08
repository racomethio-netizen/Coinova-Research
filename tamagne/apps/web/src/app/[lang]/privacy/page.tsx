import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Markdown } from "@/components/markdown";
import { SiteHeader } from "@/components/site-header";
import { Alert, Card } from "@/components/ui/card";
import { privacyPolicyEn as body } from "@/content/privacy-policy";
import { getDictionary, hasLocale } from "@/i18n/dictionaries";

export const metadata: Metadata = { title: "Privacy Policy · TAMAGNE" };

export default async function Page({ params }: PageProps<"/[lang]/privacy">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  const t = getDictionary(lang);
  return (
    <>
      <SiteHeader lang={lang} t={t} path="/privacy" />
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
