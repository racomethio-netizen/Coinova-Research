import { products } from "@/content/products";
import { ProductCard } from "@/components/content/product-card";
import { ArrowLink } from "@/components/ui/button";
import { Section, SectionHeading } from "@/components/ui/section";

export function ProductsPreview() {
  return (
    <Section
      labelledBy="products-title"
      className="border-t border-line bg-[linear-gradient(180deg,transparent,rgb(20_24_39/0.6))]"
    >
      <div className="flex flex-col justify-between gap-6 md:flex-row md:items-end">
        <SectionHeading
          id="products-title"
          index="03"
          eyebrow="Digital products"
          title="Products of our own, built with the same care."
          description="Alongside client work, we develop our own software products. The entries below are examples of the product directions we are exploring — none is launched yet."
        />
        <ArrowLink href="/products" className="shrink-0">
          All products
        </ArrowLink>
      </div>
      <div className="mt-14 grid gap-6 md:grid-cols-2 lg:grid-cols-3">
        {products.map((p) => (
          <ProductCard key={p.slug} product={p} />
        ))}
      </div>
    </Section>
  );
}
