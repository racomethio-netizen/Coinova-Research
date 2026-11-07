import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { SiteHeader } from "@/components/site-header";
import { VerifyLookupForm } from "@/components/verify-lookup-form";
import { Card } from "@/components/ui/card";
import { getDictionary, hasLocale } from "@/i18n/dictionaries";

export const metadata: Metadata = { robots: { index: false } };

/** Works without JavaScript: the form submits ?id=… and this page redirects to the result. */
export default async function VerifyPage({ params, searchParams }: PageProps<"/[lang]/verify">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  const { id } = await searchParams;
  if (typeof id === "string" && id.trim()) redirect(`/${lang}/verify/${encodeURIComponent(id.trim().slice(0, 64))}`);
  const t = getDictionary(lang);
  return (
    <>
      <SiteHeader lang={lang} t={t} path="/verify" />
      <main className="mx-auto max-w-xl px-4 py-10">
        <h1 className="text-2xl font-bold">{t.verify.title}</h1>
        <p className="mt-2 text-muted">{t.verify.lead}</p>
        <Card className="mt-6">
          <VerifyLookupForm lang={lang} t={t.verify} />
        </Card>
      </main>
    </>
  );
}
