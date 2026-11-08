import type { Metadata } from "next";
import { headers } from "next/headers";
import { notFound } from "next/navigation";
import { getDictionary, hasLocale, locales } from "@/i18n/dictionaries";
import "../globals.css";

export const dynamicParams = false;
// Every page is rendered per request so each response carries a fresh CSP nonce (src/proxy.ts).
export const dynamic = "force-dynamic";

export function generateStaticParams() {
  return locales.map((lang) => ({ lang }));
}

export async function generateMetadata({ params }: LayoutProps<"/[lang]">): Promise<Metadata> {
  const { lang } = await params;
  if (!hasLocale(lang)) return {};
  const t = getDictionary(lang);
  return {
    title: t.meta.title,
    description: t.meta.description,
    alternates: { languages: { en: "/en", am: "/am" } },
  };
}

export default async function RootLayout({ children, params }: LayoutProps<"/[lang]">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  // Reading the nonce opts into dynamic rendering; Next applies it to its own scripts.
  await headers();
  return (
    <html lang={lang}>
      <body className="min-h-dvh antialiased">{children}</body>
    </html>
  );
}
