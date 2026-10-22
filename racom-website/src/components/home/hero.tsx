import { primaryCta } from "@/config/site";
import { ButtonLink } from "@/components/ui/button";
import { HeroVisual } from "@/components/visuals/hero-visual";

const disciplines = ["Custom software", "Web platforms", "AI automation", "SaaS products", "Integrations"];

export function Hero() {
  return (
    <section aria-labelledby="hero-title" className="relative isolate overflow-hidden">
      <div aria-hidden="true" className="bg-blueprint absolute inset-0 -z-10" />
      <div
        aria-hidden="true"
        className="absolute top-[-20%] left-1/2 -z-10 h-[40rem] w-[60rem] -translate-x-1/2 rounded-full bg-[radial-gradient(closest-side,rgb(117_101_255/0.16),transparent)]"
      />
      <div className="container-page grid items-center gap-10 pt-14 pb-12 sm:pt-20 lg:grid-cols-12 lg:gap-6 lg:pt-24 lg:pb-16">
        <div className="lg:col-span-7">
          <p className="flex animate-fade-up items-center gap-2.5 font-mono text-[0.68rem] font-medium tracking-[0.14em] text-violet-soft uppercase sm:text-[0.72rem] sm:tracking-[0.2em]">
            <span aria-hidden="true" className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-cyan" />
            Software engineering · AI · Digital products
          </p>
          <h1
            id="hero-title"
            className="mt-6 animate-rise font-display text-[2.85rem] leading-[0.98] font-semibold tracking-[-0.04em] text-balance text-ink [animation-delay:80ms] sm:text-6xl lg:text-[5.25rem]"
          >
            We engineer the <span className="text-gradient">digital future.</span>
          </h1>
          <p className="mt-7 max-w-xl animate-rise text-lg leading-relaxed text-pretty text-muted [animation-delay:160ms] sm:text-xl">
            From intelligent software to powerful digital products, we transform ambitious ideas into
            technology built for real-world impact.
          </p>
          <div className="mt-9 flex animate-fade-up flex-col gap-3 [animation-delay:240ms] min-[400px]:flex-row">
            <ButtonLink href={primaryCta.href} size="lg" arrow>
              {primaryCta.label}
            </ButtonLink>
            <ButtonLink href="/work" size="lg" variant="secondary">
              Explore Our Work
            </ButtonLink>
          </div>
          <ul
            aria-label="Disciplines"
            className="mt-12 flex animate-fade-up flex-wrap gap-x-5 gap-y-2 border-t border-line pt-6 text-sm text-subtle [animation-delay:320ms]"
          >
            {disciplines.map((d) => (
              <li key={d} className="flex items-center gap-2">
                <span aria-hidden="true" className="h-px w-3 bg-violet" />
                {d}
              </li>
            ))}
          </ul>
        </div>
        <div className="relative mx-auto w-full max-w-[22rem] sm:max-w-md lg:col-span-5 lg:max-w-none">
          <HeroVisual className="h-auto w-full lg:scale-[1.12]" />
        </div>
      </div>
    </section>
  );
}
