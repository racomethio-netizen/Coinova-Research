import type { Metadata } from "next";
import { site } from "@/config/site";
import { values } from "@/content/company";
import { pageMetadata } from "@/lib/seo";
import { Placeholder } from "@/components/ui/badge";
import { PageHero } from "@/components/ui/page-hero";
import { Section, SectionHeading } from "@/components/ui/section";
import { ApproachSteps } from "@/components/home/approach";
import { FinalCta } from "@/components/home/final-cta";
import { LogoMark } from "@/components/brand/logo";

export const metadata: Metadata = pageMetadata({
  title: "About",
  description:
    "Racom Technology is a founder-led software engineering and digital products company serving Ethiopia and international markets. Our mission, values and approach.",
  path: "/about",
});

export default function AboutPage() {
  return (
    <>
      <PageHero
        eyebrow="About Racom"
        title="Engineering useful software, carefully."
        description={site.message}
      />

      <Section labelledBy="mission-title">
        <div className="grid gap-14 lg:grid-cols-12">
          <div className="lg:col-span-7">
            <SectionHeading
              id="mission-title"
              eyebrow="Our mission"
              title="Turn complex challenges into practical technology."
            />
            <div className="mt-8 space-y-5 text-lg leading-relaxed text-muted">
              <p>{site.supportingMessage}</p>
              <p>
                Racom Technology is a founder-led company. We started Racom because we believe businesses in
                Ethiopia and around the world deserve software that is engineered with the same care as the
                best international products — fast on real networks, clear to use, and built to be maintained.
              </p>
              <p>
                We work closely with a small number of clients at a time, so the people you speak to are the
                people building your software.
              </p>
              {/* PLACEHOLDER: founder story. Replace with the real founder name, background and motivation. */}
              <p className="rounded-xl border border-dashed border-warning/40 p-5 text-base">
                <Placeholder>Founder story — to be added</Placeholder>
                <span className="mt-3 block text-muted">
                  A short, first-person note from the founder about why Racom exists, their background and
                  what they want clients to experience. Replace this block in{" "}
                  <code className="text-ink">src/app/about/page.tsx</code>.
                </span>
              </p>
            </div>
          </div>
          <div className="lg:col-span-5">
            <div className="surface relative overflow-hidden rounded-2xl p-8">
              <div aria-hidden="true" className="bg-blueprint absolute inset-0" />
              <LogoMark className="relative h-16 w-16" />
              <dl className="relative mt-10 space-y-6">
                <div>
                  <dt className="font-mono text-xs tracking-[0.16em] text-subtle uppercase">Focus</dt>
                  <dd className="mt-1.5 text-ink">
                    Software engineering, AI automation and digital products
                  </dd>
                </div>
                <div>
                  <dt className="font-mono text-xs tracking-[0.16em] text-subtle uppercase">Serving</dt>
                  <dd className="mt-1.5 text-ink">Clients in {site.markets}</dd>
                </div>
                <div>
                  <dt className="font-mono text-xs tracking-[0.16em] text-subtle uppercase">Based in</dt>
                  <dd className="mt-1.5 text-ink">
                    {site.contact.location ?? <Placeholder>City, country — to be added</Placeholder>}
                  </dd>
                </div>
                <div>
                  <dt className="font-mono text-xs tracking-[0.16em] text-subtle uppercase">Slogan</dt>
                  <dd className="mt-1.5 font-display text-lg text-violet-soft">{site.slogan}</dd>
                </div>
              </dl>
            </div>
          </div>
        </div>
      </Section>

      <Section labelledBy="values-title" className="border-t border-line">
        <SectionHeading id="values-title" eyebrow="Values" title="What we hold ourselves to." />
        <dl className="mt-12 grid gap-px overflow-hidden rounded-2xl border border-line bg-line sm:grid-cols-2">
          {values.map((v, i) => (
            <div key={v.title} className="bg-obsidian p-7 sm:p-8">
              <dt className="flex items-baseline gap-3 font-display text-xl font-semibold text-ink">
                <span className="font-mono text-sm font-normal text-violet-soft">
                  {String(i + 1).padStart(2, "0")}
                </span>
                {v.title}
              </dt>
              <dd className="mt-2 leading-relaxed text-muted">{v.description}</dd>
            </div>
          ))}
        </dl>
      </Section>

      <Section labelledBy="engineering-title" className="border-t border-line">
        <div className="grid gap-12 lg:grid-cols-12">
          <div className="lg:col-span-5">
            <SectionHeading
              id="engineering-title"
              eyebrow="How we engineer"
              title="Quality is a process, not a promise."
            />
          </div>
          <div className="prose-racom lg:col-span-7">
            <p>
              Good software is the result of many small, deliberate decisions. We write typed, tested code;
              review every change; and document the decisions that future maintainers will need to understand.
            </p>
            <p>
              We design for the conditions our users actually face — mid-range phones, variable connections
              and busy days — and measure performance and accessibility rather than assuming them.
            </p>
            <p>
              And we tell you the truth about trade-offs: what something will cost to build, what it will cost
              to run, and what we would do differently if it were our own money.
            </p>
          </div>
        </div>
        <div className="mt-16">
          <ApproachSteps />
        </div>
      </Section>

      <FinalCta />
    </>
  );
}
