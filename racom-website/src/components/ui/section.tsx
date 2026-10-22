import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/** Small uppercase label with an index, e.g. "01 — Capabilities". */
export function Eyebrow({
  index,
  children,
  className,
}: {
  index?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <p
      className={cn(
        "flex items-center gap-3 font-mono text-[0.72rem] font-medium tracking-[0.18em] text-violet-soft uppercase",
        className,
      )}
    >
      {index && (
        <>
          <span className="text-subtle">{index}</span>
          <span aria-hidden="true" className="h-px w-6 bg-line-strong" />
        </>
      )}
      <span>{children}</span>
    </p>
  );
}

export function SectionHeading({
  index,
  eyebrow,
  title,
  description,
  id,
  align = "left",
  className,
  as: Heading = "h2",
}: {
  index?: string;
  eyebrow: string;
  title: ReactNode;
  description?: ReactNode;
  id?: string;
  align?: "left" | "center";
  className?: string;
  as?: "h1" | "h2";
}) {
  return (
    <div className={cn("max-w-2xl", align === "center" && "mx-auto text-center", className)}>
      <Eyebrow index={index} className={align === "center" ? "justify-center" : undefined}>
        {eyebrow}
      </Eyebrow>
      <Heading
        id={id}
        className="mt-5 font-display text-[2rem] leading-[1.08] font-semibold tracking-[-0.025em] text-balance text-ink sm:text-[2.6rem]"
      >
        {title}
      </Heading>
      {description && (
        <p className="mt-5 text-[1.05rem] leading-relaxed text-pretty text-muted">{description}</p>
      )}
    </div>
  );
}

export function Section({
  children,
  className,
  labelledBy,
  id,
}: {
  children: ReactNode;
  className?: string;
  labelledBy?: string;
  id?: string;
}) {
  return (
    <section id={id} aria-labelledby={labelledBy} className={cn("relative py-20 sm:py-28", className)}>
      <div className="container-page">{children}</div>
    </section>
  );
}
