import type { ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import { site } from "@/config/site";
import { formatDate } from "@/lib/insights-date";
import { Placeholder } from "@/components/ui/badge";
import { PageHero } from "@/components/ui/page-hero";

/** Legal entity name, or a visible placeholder until configured in src/config/site.ts. */
export function EntityName() {
  return site.legal.entityName ? (
    <>{site.legal.entityName}</>
  ) : (
    <Placeholder>[Registered legal entity name]</Placeholder>
  );
}

export function LegalField({ value, label }: { value: string | null; label: string }) {
  return value ? <>{value}</> : <Placeholder>[{label}]</Placeholder>;
}

export function LegalPage({ title, intro, children }: { title: string; intro: string; children: ReactNode }) {
  return (
    <>
      <PageHero eyebrow="Legal" title={title} description={intro} />
      <div className="container-page max-w-3xl py-14 sm:py-20">
        <div
          role="note"
          className="mb-12 flex gap-3 rounded-xl border border-warning/40 bg-warning/[0.07] p-5 text-sm leading-relaxed"
        >
          <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
          <p className="text-muted">
            <strong className="font-medium text-warning">
              Template — requires legal review before launch.
            </strong>{" "}
            This starter text is not legal advice. Have a qualified lawyer review and adapt it to the laws
            that apply to {site.name}, and replace every highlighted placeholder.
          </p>
        </div>
        <p className="mb-10 text-sm text-subtle">
          Last updated: <time dateTime={site.legal.lastUpdated}>{formatDate(site.legal.lastUpdated)}</time>
        </p>
        <div className="prose-racom">{children}</div>
      </div>
    </>
  );
}
