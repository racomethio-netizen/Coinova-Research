import { whyRacom } from "@/content/company";
import { Section, SectionHeading } from "@/components/ui/section";

export function WhyRacom() {
  return (
    <Section labelledBy="why-title" className="border-t border-line">
      <div className="grid gap-12 lg:grid-cols-12">
        <div className="lg:col-span-5">
          <div className="lg:sticky lg:top-28">
            <SectionHeading
              id="why-title"
              index="05"
              eyebrow="Why Racom"
              title="Built properly. Explained clearly. Made to last."
              description="We would rather earn trust through the quality of the work than through big claims. This is what you can expect from working with us."
            />
          </div>
        </div>
        <ol className="lg:col-span-7">
          {whyRacom.map((item, i) => (
            <li
              key={item.title}
              className="grid grid-cols-[3rem_1fr] gap-4 border-t border-line py-8 first:border-t-0 first:pt-0 sm:grid-cols-[4rem_1fr] lg:first:pt-2"
            >
              <span className="font-mono text-sm text-violet-soft">{String(i + 1).padStart(2, "0")}</span>
              <div>
                <h3 className="font-display text-xl font-semibold tracking-tight text-ink">{item.title}</h3>
                <p className="mt-2 leading-relaxed text-muted">{item.description}</p>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </Section>
  );
}
