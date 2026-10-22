import type { Metadata } from "next";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { services } from "@/content/services";
import { pageMetadata } from "@/lib/seo";
import { Icon } from "@/components/ui/icon";
import { PageHero } from "@/components/ui/page-hero";
import { Section, SectionHeading } from "@/components/ui/section";
import { ApproachSteps } from "@/components/home/approach";
import { FinalCta } from "@/components/home/final-cta";

export const metadata: Metadata = pageMetadata({
  title: "Services",
  description:
    "Custom software, websites and web applications, AI automation, SaaS product development, API integration and technical consulting from Racom Technology.",
  path: "/services",
});

export default function ServicesPage() {
  return (
    <>
      <PageHero
        eyebrow="Services"
        title="Software engineering for every stage of your product."
        description="Whether you need a focused web application, an automated workflow or a complete digital product, we bring design and engineering together to build it properly."
      />
      <Section labelledBy="services-list" className="pt-16 sm:pt-20">
        <h2 id="services-list" className="sr-only">
          All services
        </h2>
        <ul className="divide-y divide-line border-y border-line">
          {services.map((s, i) => (
            <li key={s.slug} className="group relative">
              <div className="grid gap-4 py-8 transition-colors sm:grid-cols-[4rem_1fr_auto] sm:items-center sm:gap-8 sm:py-10">
                <span className="hidden font-mono text-sm text-subtle sm:block">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <div className="flex gap-5">
                  <span className="inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border border-line-strong bg-elevated text-violet-soft transition-colors group-hover:text-cyan">
                    <Icon name={s.icon} className="h-5 w-5" />
                  </span>
                  <div>
                    <h3 className="font-display text-xl font-semibold tracking-tight text-ink sm:text-2xl">
                      <Link
                        href={`/services/${s.slug}`}
                        className="after:absolute after:inset-0 after:content-['']"
                      >
                        {s.title}
                      </Link>
                    </h3>
                    <p className="mt-2 max-w-2xl leading-relaxed text-muted">{s.summary}</p>
                  </div>
                </div>
                <span
                  aria-hidden="true"
                  className="hidden h-11 w-11 items-center justify-center rounded-full border border-line-strong text-muted transition-[color,border-color,transform] group-hover:translate-x-1 group-hover:border-violet-soft/60 group-hover:text-ink sm:inline-flex"
                >
                  <ArrowRight className="h-4 w-4" />
                </span>
              </div>
            </li>
          ))}
        </ul>
      </Section>
      <Section labelledBy="engagement-title" className="border-t border-line">
        <SectionHeading
          id="engagement-title"
          eyebrow="How engagements work"
          title="One process, adapted to each project."
          description="Every engagement follows the same disciplined path, scaled to the size of the work."
        />
        <div className="mt-14">
          <ApproachSteps />
        </div>
      </Section>
      <FinalCta />
    </>
  );
}
