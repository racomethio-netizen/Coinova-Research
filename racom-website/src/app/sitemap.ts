import type { MetadataRoute } from "next";
import { getSiteUrl } from "@/config/site";
import { services } from "@/content/services";
import { projects } from "@/content/projects";
import { getArticles } from "@/lib/insights";

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  // Without a configured origin (development), URLs fall back to localhost.
  const base = getSiteUrl() ?? "http://localhost:3000";
  const articles = await getArticles();
  const staticPaths = [
    "",
    "/services",
    "/work",
    "/products",
    "/about",
    "/insights",
    "/contact",
    "/privacy",
    "/terms",
  ];
  return [
    ...staticPaths.map((p) => ({
      url: `${base}${p}`,
      changeFrequency: "monthly" as const,
      priority: p === "" ? 1 : p === "/privacy" || p === "/terms" ? 0.3 : 0.8,
    })),
    ...services.map((s) => ({
      url: `${base}/services/${s.slug}`,
      changeFrequency: "monthly" as const,
      priority: 0.7,
    })),
    ...projects.map((p) => ({
      url: `${base}/work/${p.slug}`,
      changeFrequency: "yearly" as const,
      priority: 0.6,
    })),
    ...articles.map((a) => ({
      url: `${base}/insights/${a.slug}`,
      lastModified: a.publishedAt,
      changeFrequency: "yearly" as const,
      priority: 0.6,
    })),
  ];
}
