import { Check } from "lucide-react";
import { type Product, productStatusLabel } from "@/content/products";
import { ProductArt } from "@/components/visuals/project-art";
import { Badge } from "@/components/ui/badge";
import { ArrowLink, ButtonLink } from "@/components/ui/button";

/** Product card. CTAs never imply availability: only "available" products link out. */
export function ProductCard({ product, detailed = false }: { product: Product; detailed?: boolean }) {
  const interestHref = `/contact?service=saas-product-development&product=${encodeURIComponent(product.slug)}`;
  return (
    <article
      id={product.slug}
      className="surface flex h-full scroll-mt-28 flex-col overflow-hidden rounded-2xl"
    >
      <div className="relative aspect-[16/10] border-b border-line">
        <ProductArt variant={product.art} className="h-full w-full" />
        <div className="absolute top-3 left-3 flex flex-wrap gap-2">
          <Badge
            tone={product.status === "available" ? "cyan" : "violet"}
            className="bg-obsidian/80 backdrop-blur"
          >
            Status: {productStatusLabel[product.status]}
          </Badge>
          {product.example && (
            <Badge tone="warning" className="bg-obsidian/80 backdrop-blur">
              Example
            </Badge>
          )}
        </div>
      </div>
      <div className="flex flex-1 flex-col p-6">
        <h3 className="font-display text-xl font-semibold tracking-tight text-ink">{product.name}</h3>
        <p className="mt-1 text-sm text-violet-soft">{product.tagline}</p>
        <p className="mt-3 text-[0.95rem] leading-relaxed text-muted">{product.description}</p>
        {detailed && (
          <>
            <p className="mt-4 text-sm text-subtle">
              <span className="text-muted">For:</span> {product.audience}
            </p>
            <ul className="mt-4 space-y-2">
              {product.features.map((f) => (
                <li key={f} className="flex gap-2.5 text-sm text-muted">
                  <Check aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-cyan" />
                  {f}
                </li>
              ))}
            </ul>
          </>
        )}
        <div className="mt-auto pt-6">
          {product.status === "available" && product.url ? (
            <ButtonLink href={product.url} variant="secondary" arrow>
              Visit {product.name}
            </ButtonLink>
          ) : (
            <ArrowLink href={interestHref}>
              {product.status === "in-development" ? "Request early access" : "Discuss this idea"}
            </ArrowLink>
          )}
        </div>
      </div>
    </article>
  );
}
