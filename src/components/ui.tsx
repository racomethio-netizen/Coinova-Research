import Link from "next/link";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

export function cn(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

// ------------------------------------------------------------------ buttons
type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-accent-fg hover:opacity-90",
  secondary: "border border-line-strong bg-elevated text-fg hover:bg-subtle",
  ghost: "text-muted hover:text-fg hover:bg-subtle",
  danger: "border border-negative/50 text-negative hover:bg-danger-soft",
};
const SIZES: Record<Size, string> = { sm: "h-8 px-3 text-sm", md: "h-10 px-4 text-sm" };

export function buttonClass(variant: Variant = "primary", size: Size = "md", extra?: string) {
  return cn(
    "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
    VARIANTS[variant],
    SIZES[size],
    extra,
  );
}

export function Button({ variant = "primary", size = "md", className, ...props }: ComponentPropsWithoutRef<"button"> & { variant?: Variant; size?: Size }) {
  return <button className={buttonClass(variant, size, className)} {...props} />;
}

export function ButtonLink({ href, variant = "primary", size = "md", className, children }: { href: string; variant?: Variant; size?: Size; className?: string; children: ReactNode }) {
  return (
    <Link href={href} className={buttonClass(variant, size, className)}>
      {children}
    </Link>
  );
}

// -------------------------------------------------------------------- cards
export function Card({ className, children, ...rest }: ComponentPropsWithoutRef<"section">) {
  return (
    <section className={cn("rounded-lg border border-line bg-elevated", className)} {...rest}>
      {children}
    </section>
  );
}

export function CardHeader({ title, description, action }: { title: ReactNode; description?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4">
      <div>
        <h2 className="text-base font-semibold">{title}</h2>
        {description ? <p className="mt-0.5 text-sm text-muted">{description}</p> : null}
      </div>
      {action}
    </div>
  );
}

export function CardBody({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn("px-5 py-4", className)}>{children}</div>;
}

// ------------------------------------------------------------------- badges
type Tone = "neutral" | "accent" | "positive" | "negative" | "warning";
const TONES: Record<Tone, string> = {
  neutral: "border-line-strong text-muted",
  accent: "border-accent/40 bg-accent-soft text-accent",
  positive: "border-positive/40 bg-positive-soft text-positive",
  negative: "border-negative/40 bg-danger-soft text-negative",
  warning: "border-warning/40 bg-warning-soft text-warning",
};

export function Badge({ tone = "neutral", children, className, title }: { tone?: Tone; children: ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium", TONES[tone], className)}>
      {children}
    </span>
  );
}

// ------------------------------------------------------------------ notices
export function Notice({ tone = "neutral", title, children, className }: { tone?: Tone; title?: ReactNode; children?: ReactNode; className?: string }) {
  const border: Record<Tone, string> = {
    neutral: "border-line bg-subtle",
    accent: "border-accent/40 bg-accent-soft",
    positive: "border-positive/40 bg-positive-soft",
    negative: "border-negative/40 bg-danger-soft",
    warning: "border-warning/40 bg-warning-soft",
  };
  return (
    <div role={tone === "negative" ? "alert" : "status"} className={cn("rounded-md border px-4 py-3 text-sm", border[tone], className)}>
      {title ? <p className="font-medium">{title}</p> : null}
      {children ? <div className={cn(title ? "mt-1" : "", "text-muted")}>{children}</div> : null}
    </div>
  );
}

export function EmptyState({ title, description, action }: { title: string; description?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line-strong px-6 py-12 text-center">
      <p className="font-medium">{title}</p>
      {description ? <p className="max-w-md text-sm text-muted">{description}</p> : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("animate-pulse rounded-md bg-subtle", className)} />;
}

// -------------------------------------------------------------------- forms
export const inputClass =
  "h-10 w-full rounded-md border border-line-strong bg-bg px-3 text-sm text-fg placeholder:text-faint focus:border-accent focus:outline-none aria-[invalid=true]:border-negative";

export function Field({ id, label, error, hint, children }: { id: string; label: string; error?: string | string[]; hint?: ReactNode; children: ReactNode }) {
  const msg = Array.isArray(error) ? error[0] : error;
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-medium">
        {label}
      </label>
      {children}
      {hint && !msg ? (
        <p id={`${id}-hint`} className="text-xs text-faint">
          {hint}
        </p>
      ) : null}
      {msg ? (
        <p id={`${id}-error`} className="text-xs text-negative" role="alert">
          {msg}
        </p>
      ) : null}
    </div>
  );
}

// -------------------------------------------------------------------- page
export function PageHeader({ title, description, actions, eyebrow }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4 pb-6">
      <div className="min-w-0">
        {eyebrow ? <div className="mb-2 text-xs font-medium uppercase tracking-wide text-faint">{eyebrow}</div> : null}
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">{title}</h1>
        {description ? <p className="mt-2 max-w-2xl text-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </div>
  );
}

export function Container({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn("mx-auto w-full max-w-7xl px-4 sm:px-6 lg:px-8", className)}>{children}</div>;
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs uppercase tracking-wide text-faint">{label}</dt>
      <dd className="tabular mt-1 truncate text-lg font-semibold">{value}</dd>
      {sub ? <dd className="mt-0.5 text-xs text-muted">{sub}</dd> : null}
    </div>
  );
}

// ------------------------------------------------------------ pagination
export function Pagination({ page, totalPages, hrefFor }: { page: number; totalPages: number; hrefFor: (p: number) => string }) {
  if (totalPages <= 1) return null;
  return (
    <nav aria-label="Pagination" className="flex items-center justify-between gap-2 pt-4 text-sm">
      {page > 1 ? (
        <Link className={buttonClass("secondary", "sm")} href={hrefFor(page - 1)} rel="prev">
          Previous
        </Link>
      ) : (
        <span className={buttonClass("secondary", "sm", "pointer-events-none opacity-40")} aria-disabled="true">
          Previous
        </span>
      )}
      <span className="text-muted">
        Page {page} of {totalPages}
      </span>
      {page < totalPages ? (
        <Link className={buttonClass("secondary", "sm")} href={hrefFor(page + 1)} rel="next">
          Next
        </Link>
      ) : (
        <span className={buttonClass("secondary", "sm", "pointer-events-none opacity-40")} aria-disabled="true">
          Next
        </span>
      )}
    </nav>
  );
}
