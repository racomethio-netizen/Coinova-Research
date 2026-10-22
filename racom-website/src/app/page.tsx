import type { Metadata } from "next";
import { site } from "@/config/site";
import { pageMetadata } from "@/lib/seo";
import { Hero } from "@/components/home/hero";
import { Capabilities } from "@/components/home/capabilities";
import { SelectedWork } from "@/components/home/selected-work";
import { ProductsPreview } from "@/components/home/products-preview";
import { Approach } from "@/components/home/approach";
import { WhyRacom } from "@/components/home/why-racom";
import { FinalCta } from "@/components/home/final-cta";

export const metadata: Metadata = {
  ...pageMetadata({ title: `${site.name} — ${site.slogan}`, description: site.description, path: "/" }),
  title: { absolute: `${site.name} — ${site.slogan}` },
};

export default function HomePage() {
  return (
    <>
      <Hero />
      <Capabilities />
      <SelectedWork />
      <ProductsPreview />
      <Approach />
      <WhyRacom />
      <FinalCta />
    </>
  );
}
