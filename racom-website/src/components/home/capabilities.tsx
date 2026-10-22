import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { capabilities } from "@/content/services";
import { Icon } from "@/components/ui/icon";
import { ArrowLink } from "@/components/ui/button";
import { Section, SectionHeading } from "@/components/ui/section";

export function Capabilities() {
  return (
    <Section labelledBy="capabilities-title">
      <div className="flex flex-col justify-between gap-6 md:flex-row md:items-end">
        <SectionHeading
          id="capabilities-title"
          index="01"
          eyebrow="Capabilities"
          title="Engineering across the full product lifecycle."
          description="We combine product thinking, design and software engineering to build systems that are useful on day one and maintainable for years."
        />
        <ArrowLink href="/services" className="shrink-0">
          All services
        </ArrowLink>
      </div>
      {/* Hairline grid: cells share borders instead of floating as separate cards. */}
      <ul className="mt-14 grid gap-px overflow-hidden rounded-2xl border border-line bg-line sm:grid-cols-2 lg:grid-cols-3">
        {capabilities.map((c) => (
          <li
            key={c.title}
            className="group relative bg-obsidian transition-colors duration-300 hover:bg-midnight"
          >
            <div className="flex h-full flex-col p-7 sm:p-8">
              <span className="inline-flex h-11 w-11 items-center justify-center rounded-xl border border-line-strong bg-elevated text-violet-soft transition-colors group-hover:border-violet/50 group-hover:text-cyan">
                <Icon name={c.icon} className="h-5 w-5" />
              </span>
              <h3 className="mt-6 font-display text-lg font-semibold tracking-tight text-ink">
                <Link href={c.href} className="after:absolute after:inset-0 after:content-['']">
                  {c.title}
                </Link>
              </h3>
              <p className="mt-2.5 text-[0.95rem] leading-relaxed text-muted">{c.description}</p>
              <span
                aria-hidden="true"
                className="mt-6 inline-flex items-center gap-1.5 text-sm font-medium text-violet-soft"
              >
                Learn more
                <ArrowRight className="h-4 w-4 transition-transform duration-200 group-hover:translate-x-0.5" />
              </span>
            </div>
          </li>
        ))}
      </ul>
    </Section>
  );
}
