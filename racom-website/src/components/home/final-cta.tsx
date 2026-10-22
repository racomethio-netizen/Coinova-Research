import { LogoMark } from "@/components/brand/logo";
import { ButtonLink } from "@/components/ui/button";

export function FinalCta({
  title = "Have an ambitious idea? Let’s engineer it.",
  description = "Tell us what you are building or the problem you want to solve. We will reply with honest questions, a suggested next step and no obligation.",
  cta = "Tell Us About Your Project",
}: {
  title?: string;
  description?: string;
  cta?: string;
}) {
  return (
    <section aria-labelledby="cta-title" className="py-20 sm:py-28">
      <div className="container-page">
        <div className="relative isolate overflow-hidden rounded-3xl border border-line bg-midnight px-6 py-16 sm:px-12 sm:py-20">
          <div aria-hidden="true" className="bg-blueprint absolute inset-0 -z-10 opacity-70" />
          <div
            aria-hidden="true"
            className="absolute -top-40 -right-24 -z-10 h-[30rem] w-[30rem] rounded-full bg-[radial-gradient(closest-side,rgb(117_101_255/0.35),transparent)]"
          />
          <div
            aria-hidden="true"
            className="absolute -bottom-48 -left-24 -z-10 h-[24rem] w-[24rem] rounded-full bg-[radial-gradient(closest-side,rgb(41_216_242/0.12),transparent)]"
          />
          <LogoMark className="absolute right-8 bottom-8 hidden h-28 w-28 opacity-25 md:block" />
          <div className="max-w-2xl">
            <h2
              id="cta-title"
              className="font-display text-[2.1rem] leading-[1.05] font-semibold tracking-[-0.03em] text-balance text-ink sm:text-5xl"
            >
              {title}
            </h2>
            <p className="mt-5 max-w-xl text-lg leading-relaxed text-muted">{description}</p>
            <ButtonLink href="/contact" size="lg" arrow className="mt-9">
              {cta}
            </ButtonLink>
          </div>
        </div>
      </div>
    </section>
  );
}
