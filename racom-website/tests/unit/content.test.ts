import { readdirSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { products } from "@/content/products";
import { projects } from "@/content/projects";
import { capabilities, services } from "@/content/services";
import { mainNav } from "@/config/site";

const unique = (values: string[]) => new Set(values).size === values.length;

describe("content integrity", () => {
  it("uses unique, URL-safe slugs", () => {
    for (const list of [services, projects, products]) {
      const slugs = list.map((x) => x.slug);
      expect(unique(slugs)).toBe(true);
      for (const s of slugs) expect(s).toMatch(/^[a-z0-9]+(?:-[a-z0-9]+)*$/);
    }
  });

  it("never reports results for concept projects", () => {
    for (const p of projects.filter((x) => x.kind === "concept")) {
      expect(p.results, p.slug).toEqual([]);
      expect(p.client, p.slug).toBeUndefined();
    }
  });

  it("only links available products externally, and never marks examples as available", () => {
    for (const p of products) {
      if (p.status === "available") {
        expect(p.example, p.slug).toBe(false);
        expect(p.url, p.slug).toMatch(/^https:\/\//);
      } else {
        expect(p.url, p.slug).toBeUndefined();
      }
    }
  });

  it("points every capability to an existing service page", () => {
    const routes = new Set(services.map((s) => `/services/${s.slug}`));
    for (const c of capabilities) expect(routes.has(c.href), c.href).toBe(true);
  });

  it("has a page for every main navigation item", () => {
    const appDirs = new Set(readdirSync("src/app"));
    for (const item of mainNav) expect(appDirs.has(item.href.slice(1)), item.href).toBe(true);
  });

  it('has no unresolved placeholder links (href="#")', () => {
    const files = readdirSync("src", { recursive: true, withFileTypes: true })
      .filter((d) => d.isFile() && /\.(tsx|mdx)$/.test(d.name))
      .map((d) => `${d.parentPath}/${d.name}`);
    for (const f of files) expect(readFileSync(f, "utf8"), f).not.toMatch(/href=["']#["']/);
  });
});
