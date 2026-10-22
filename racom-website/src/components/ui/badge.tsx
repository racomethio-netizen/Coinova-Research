import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

type Tone = "neutral" | "violet" | "cyan" | "warning";

const tones: Record<Tone, string> = {
  neutral: "border-line-strong text-muted",
  violet: "border-violet/40 bg-violet/10 text-violet-soft",
  cyan: "border-cyan/30 bg-cyan/10 text-cyan",
  warning: "border-warning/35 bg-warning/10 text-warning",
};

/** Text label pill. Meaning is always carried by the text, never by colour alone. */
export function Badge({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: Tone;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium tracking-wide",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/**
 * Visible marker for content that must be replaced with real company information.
 * Search for "PLACEHOLDER" in src/ to find every occurrence.
 */
export function Placeholder({ children }: { children: ReactNode }) {
  return (
    <span className="rounded border border-dashed border-warning/50 bg-warning/10 px-1.5 py-0.5 text-[0.92em] text-warning">
      {children}
    </span>
  );
}
