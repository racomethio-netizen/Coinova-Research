import { notFound, redirect } from "next/navigation";
import { SellerSetupForm } from "@/components/seller-setup-form";
import { SiteHeader } from "@/components/site-header";
import { Card } from "@/components/ui/card";
import { getDictionary, hasLocale } from "@/i18n/dictionaries";
import { getMySeller, getViewer } from "@/lib/server-api";

export default async function SetupPage({ params }: PageProps<"/[lang]/dashboard/setup">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  const viewer = await getViewer();
  if (!viewer) redirect(`/${lang}/login`);
  if (await getMySeller()) redirect(`/${lang}/dashboard`);
  const t = getDictionary(lang);
  return (
    <>
      <SiteHeader lang={lang} t={t} path="/dashboard/setup" signedIn />
      <main className="mx-auto max-w-xl px-4 py-10">
        <h1 className="text-2xl font-bold">{t.setup.title}</h1>
        <p className="mt-2 text-muted">{t.setup.lead}</p>
        <Card className="mt-6">
          <SellerSetupForm lang={lang} t={t.setup} errors={t.auth.errors} />
        </Card>
      </main>
    </>
  );
}
