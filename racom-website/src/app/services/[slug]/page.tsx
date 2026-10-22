import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Check } from "lucide-react";
import { getService, services } from "@/content/services";
import { pageMetadata } from "@/lib/seo";
import { Icon } from "@/components/ui/icon";
import { ArrowLink, ButtonLink } from "@/components/ui/button";
import { Breadcrumbs, PageHero } from "@/components/ui/page-hero";
import { Eyebrow } from "@/components/ui/section";
import { FinalCta } from "@/components/home/final-cta";

export const dynamicParams = false;

export function generateStaticParams() {
  return services.map((s) => ({ slug: s.slug }));
}

export async function generateMetadata({ params }: PageProps<"/services/[slug]">): Promise<Metadata> {
  const { slug } = await params;
  const service = getService(slug);
  if (!service) return {};
  return pageMetadata({
    title: service.title,
    description: service.summary,
    path: `/services/${service.slug}`,
  });
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-line py-12 first:border-t-0 first:pt-0 sm:py-14">
      <h2 className="font-display text-2xl font-semibold tracking-tight text-ink">{title}</h2>
      <div className="mt-6">{children}</div>
    </section>
  );
}

export default async function ServicePage({ params }: PageProps<"/services/[slug]">) {
  const { slug } = await params;
  const service = getService(slug);
  if (!service) notFound();
  const others = services.filter((s) => s.slug !== service.slug);

  return (
    <>
      <PageHero eyebrow="Service" title={service.title} description={service.intro}>
        <ButtonLink href={`/contact?service=${service.slug}`} size="lg" arrow>
          Discuss your project
        </ButtonLink>
      </PageHero>
      <div className="container-page grid gap-14 py-16 sm:py-20 lg:grid-cols-12">
        <div className="lg:col-span-8">
          <Breadcrumbs items={[{ label: "Services", href: "/services" }, { label: service.title }]} />
          <Block title="When it fits">
            <ul className="grid gap-3 sm:grid-cols-2">
              {service.useCases.map((u) => (
                <li key={u} className="surface rounded-xl p-5 text-[0.95rem] leading-relaxed text-muted">
                  {u}
                </li>
              ))}
            </ul>
          </Block>
          <Block title="How the engagement runs">
            <ol className="space-y-0">
              {service.process.map((step, i) => (
                <li
                  key={step.title}
                  className="grid grid-cols-[2.5rem_1fr] gap-4 border-b border-line py-5 last:border-b-0"
                >
                  <span className="font-mono text-sm text-violet-soft">{String(i + 1).padStart(2, "0")}</span>
                  <div>
                    <h3 className="font-display font-semibold text-ink">{step.title}</h3>
                    <p className="mt-1 leading-relaxed text-muted">{step.description}</p>
                  </div>
                </li>
              ))}
            </ol>
          </Block>
          <Block title="What you receive">
            <ul className="space-y-3">
              {service.deliverables.map((d) => (
                <li key={d} className="flex gap-3 text-muted">
                  <Check aria-hidden="true" className="mt-1 h-4 w-4 shrink-0 text-cyan" />
                  {d}
                </li>
              ))}
            </ul>
          </Block>
          <Block title="Typical tools">
            <p className="mb-4 text-sm text-subtle">
              Chosen per project based on your requirements and existing systems.
            </p>
            <ul className="flex flex-wrap gap-2">
              {service.typicalStack.map((t) => (
                <li key={t} className="rounded-full border border-line-strong px-3 py-1 text-sm text-muted">
                  {t}
                </li>
              ))}
            </ul>
          </Block>
        </div>
        <aside className="lg:col-span-4" aria-label="Next step">
          <div className="surface rounded-2xl p-7 lg:sticky lg:top-28">
            <span className="inline-flex h-11 w-11 items-center justify-center rounded-xl border border-line-strong bg-elevated text-violet-soft">
              <Icon name={service.icon} className="h-5 w-5" />
            </span>
            <Eyebrow className="mt-6">Next step</Eyebrow>
            <p className="mt-3 leading-relaxed text-ink">{service.nextStep}</p>
            <ButtonLink href={`/contact?service=${service.slug}`} className="mt-6 w-full" arrow>
              Start a Project
            </ButtonLink>
            <div className="mt-8 border-t border-line pt-6">
              <p className="text-sm font-medium text-ink">Other services</p>
              <ul className="mt-3 space-y-2">
                {others.map((o) => (
                  <li key={o.slug}>
                    <ArrowLink href={`/services/${o.slug}`} className="text-muted">
                      {o.title}
                    </ArrowLink>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </aside>
      </div>
      <FinalCta title="Ready when you are." description={service.nextStep} cta="Tell Us About Your Project" />
    </>
  );
}
