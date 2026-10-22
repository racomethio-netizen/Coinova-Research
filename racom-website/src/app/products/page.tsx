import type { Metadata } from "next";
import { productStatusLabel, products, type ProductStatus } from "@/content/products";
import { pageMetadata } from "@/lib/seo";
import { PageHero } from "@/components/ui/page-hero";
import { Section, SectionHeading } from "@/components/ui/section";
import { ProductCard } from "@/components/content/product-card";
import { FinalCta } from "@/components/home/final-cta";

export const metadata: Metadata = pageMetadata({
  title: "Products",
  description:
    "Racom Technology's own software products. Example product directions are clearly labelled with their current status — none is presented as available before it launches.",
  path: "/products",
});

const statusExplainer: Record<ProductStatus, string> = {
  concept: "An idea we are researching and validating. Not yet being built.",
  "in-development": "Actively being built. Early access may be possible on request.",
  available: "Launched and available to use.",
};

export default function ProductsPage() {
  const allExamples = products.every((p) => p.example);
  return (
    <>
      <PageHero
        eyebrow="Products"
        title="Our own products, engineered with the same discipline."
        description="Beyond client work, Racom develops its own software products for businesses. Each product shows its real status, so you always know what exists today."
      />
      <Section className="pt-14 sm:pt-16">
        {allExamples && (
          <p className="mb-10 rounded-xl border border-warning/30 bg-warning/[0.06] px-5 py-4 text-sm leading-relaxed text-muted">
            <strong className="font-medium text-warning">Note:</strong> the products below are labelled{" "}
            <em>Example</em>. They illustrate product directions we are exploring and are not available to buy
            or use yet.
          </p>
        )}
        <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
          {products.map((p) => (
            <ProductCard key={p.slug} product={p} detailed />
          ))}
        </div>
      </Section>
      <Section labelledBy="status-title" className="border-t border-line">
        <SectionHeading id="status-title" eyebrow="Status labels" title="What each status means." />
        <dl className="mt-10 grid gap-px overflow-hidden rounded-2xl border border-line bg-line md:grid-cols-3">
          {(Object.keys(statusExplainer) as ProductStatus[]).map((s) => (
            <div key={s} className="bg-obsidian p-6">
              <dt className="font-display font-semibold text-ink">{productStatusLabel[s]}</dt>
              <dd className="mt-1.5 text-sm leading-relaxed text-muted">{statusExplainer[s]}</dd>
            </div>
          ))}
        </dl>
      </Section>
      <FinalCta
        title="Need a product built for your own market?"
        description="We apply the same product engineering to client products — from a focused first release to a scalable platform."
        cta="Talk About Your Product"
      />
    </>
  );
}
