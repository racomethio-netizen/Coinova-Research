import { notFound } from "next/navigation";
import { PhoneAuthForm } from "@/components/phone-auth-form";
import { SiteHeader } from "@/components/site-header";
import { Card } from "@/components/ui/card";
import { getDictionary, hasLocale } from "@/i18n/dictionaries";

export default async function Page({ params }: PageProps<"/[lang]/register">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  const t = getDictionary(lang);
  return (
    <>
      <SiteHeader lang={lang} t={t} path="/register" />
      <main className="mx-auto max-w-md px-4 py-10">
        <h1 className="text-2xl font-bold">{t.auth.registerTitle}</h1>
        <p className="mt-2 text-muted">{t.auth.registerLead}</p>
        <Card className="mt-6">
          <PhoneAuthForm mode="register" lang={lang} t={t.auth} legal={t.legal} />
        </Card>
      </main>
    </>
  );
}
