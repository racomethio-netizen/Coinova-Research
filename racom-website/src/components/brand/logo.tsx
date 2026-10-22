import { useId } from "react";

type Tone = "dark" | "light";

/**
 * Racom mark: a geometric R built from three separated strokes — stem, bowl and
 * a forward-moving leg — expressing precision and connected parts.
 * `tone="dark"` is for dark backgrounds (default); `tone="light"` for light ones.
 */
export function LogoMark({
  className,
  tone = "dark",
  title,
}: {
  className?: string;
  tone?: Tone;
  title?: string;
}) {
  const id = useId();
  const gradientId = `racom-bowl-${id}`;
  const colors =
    tone === "dark"
      ? { stem: "#A89FFF", from: "#C2BBFF", to: "#7565FF", leg: "#29D8F2" }
      : { stem: "#4B3DDB", from: "#6656F5", to: "#4B3DDB", leg: "#0FA3BA" };
  return (
    <svg
      viewBox="0 0 40 40"
      fill="none"
      className={className}
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor={colors.from} />
          <stop offset="1" stopColor={colors.to} />
        </linearGradient>
      </defs>
      <path d="M10 33V7" stroke={colors.stem} strokeWidth="4.5" />
      <path d="M15 7h5.5a7.5 7.5 0 0 1 0 15H15" stroke={`url(#${gradientId})`} strokeWidth="4.5" />
      <path d="M22 26l7.5 7.5" stroke={colors.leg} strokeWidth="4.5" />
    </svg>
  );
}

/** Custom geometric RACOM lettering (no font dependency). Inherits `currentColor`. */
export function Wordmark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 122 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="3"
      className={className}
      aria-hidden="true"
    >
      <path d="M1.5 20V0M5 1.5h3.5a5 5 0 0 1 0 10H5M10 14l6 6" />
      <path d="M24.5 20l8-19 8 19" strokeLinejoin="miter" />
      <path d="M63.5 4.54a8.5 8.5 0 1 0 0 10.92" />
      <circle cx="78" cy="10" r="8.5" />
      <path d="M95.5 20V2l8.5 11 8.5-11v18" strokeLinejoin="miter" />
    </svg>
  );
}

export function Logo({ className, tone = "dark" }: { className?: string; tone?: Tone }) {
  return (
    <span className={`inline-flex items-center gap-2.5 ${className ?? ""}`}>
      <LogoMark className="h-8 w-8 shrink-0" tone={tone} />
      <Wordmark className={`h-[15px] w-auto ${tone === "dark" ? "text-ink" : "text-obsidian"}`} />
    </span>
  );
}
