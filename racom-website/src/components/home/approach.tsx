import { approachPrinciples, approachSteps } from "@/content/company";
import { Section, SectionHeading } from "@/components/ui/section";

export function ApproachSteps({ headingLevel = "h3" }: { headingLevel?: "h2" | "h3" }) {
  const Heading = headingLevel;
  return (
    <ol className="relative grid gap-0 lg:grid-cols-5">
      {/* Connecting rail (horizontal on desktop, vertical on mobile) */}
      <span
        aria-hidden="true"
        className="absolute top-2 bottom-2 left-[7px] w-px bg-gradient-to-b from-violet via-violet/40 to-cyan lg:top-[7px] lg:right-0 lg:bottom-auto lg:left-0 lg:h-px lg:w-auto lg:bg-gradient-to-r"
      />
      {approachSteps.map((step, i) => (
        <li key={step.title} className="relative pb-10 pl-10 last:pb-0 lg:pt-12 lg:pr-6 lg:pb-0 lg:pl-0">
          <span
            aria-hidden="true"
            className="absolute top-1 left-0 h-[15px] w-[15px] rounded-full border-2 border-violet bg-obsidian lg:top-0"
          >
            <span className="absolute inset-[3px] rounded-full bg-cyan" />
          </span>
          <p className="font-mono text-xs tracking-[0.16em] text-subtle">
            STEP {String(i + 1).padStart(2, "0")}
          </p>
          <Heading className="mt-2 font-display text-2xl font-semibold tracking-tight text-ink">
            {step.title}
          </Heading>
          <p className="mt-2 text-[0.95rem] leading-relaxed text-muted">{step.description}</p>
        </li>
      ))}
    </ol>
  );
}

export function Approach() {
  return (
    <Section labelledBy="approach-title" className="border-t border-line">
      <SectionHeading
        id="approach-title"
        index="04"
        eyebrow="Our approach"
        title={
          <>
            Discover <span className="text-subtle">→</span> Design <span className="text-subtle">→</span>{" "}
            Engineer <span className="text-subtle">→</span> Launch <span className="text-subtle">→</span>{" "}
            Improve
          </>
        }
        description="A clear, repeatable process — so you always know what is happening, why, and what comes next."
        className="max-w-3xl"
      />
      <div className="mt-16">
        <ApproachSteps />
      </div>
      <dl className="mt-20 grid gap-px overflow-hidden rounded-2xl border border-line bg-line sm:grid-cols-2 lg:grid-cols-4">
        {approachPrinciples.map((p) => (
          <div key={p.title} className="bg-obsidian p-6">
            <dt className="font-display font-semibold text-ink">{p.title}</dt>
            <dd className="mt-1.5 text-sm leading-relaxed text-muted">{p.description}</dd>
          </div>
        ))}
      </dl>
    </Section>
  );
}
