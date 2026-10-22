import type { Metadata } from "next";
import { getSiteUrl, site } from "@/config/site";

type PageMetaInput = {
  title: string;
  description: string;
  /** Route path beginning with "/". */
  path: string;
  type?: "website" | "article";
  publishedTime?: string;
  /** Absolute path of a page-specific OG image, if any. */
  image?: string;
  noIndex?: boolean;
};

/**
 * Consistent per-page metadata. Canonical URLs are emitted only when
 * NEXT_PUBLIC_SITE_URL is configured, so development and preview builds never
 * advertise a wrong canonical origin.
 */
export function pageMetadata({
  title,
  description,
  path,
  type = "website",
  publishedTime,
  image,
  noIndex,
}: PageMetaInput): Metadata {
  const siteUrl = getSiteUrl();
  return {
    title,
    description,
    alternates: siteUrl ? { canonical: path } : undefined,
    openGraph: {
      type,
      title,
      description,
      url: path,
      siteName: site.name,
      locale: "en",
      ...(publishedTime ? { publishedTime } : {}),
      ...(image ? { images: [{ url: image, width: 1200, height: 630, alt: title }] } : {}),
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      ...(image ? { images: [image] } : {}),
    },
    ...(noIndex ? { robots: { index: false, follow: true } } : {}),
  };
}

/** metadataBase for absolute OG URLs: the configured origin, or localhost in development. */
export function metadataBase(): URL {
  return new URL(getSiteUrl() ?? `http://localhost:${process.env.PORT ?? 3000}`);
}

/** Serialise JSON-LD safely for embedding in a <script> tag. */
export function jsonLd(data: Record<string, unknown>): string {
  return JSON.stringify(data).replace(/</g, "\\u003c");
}
