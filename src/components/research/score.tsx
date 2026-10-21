import Link from "next/link";
import { CATEGORY_LABELS } from "@/config/scoring";
import { fmtDate } from "@/lib/format";
import { scoreBand } from "@/lib/research/scoring";
import type { ScoreAccess, StoredScore } from "@/lib/research/service";
import { Badge, buttonClass, cn } from "../ui";

export function ScoreBadge({ score, status }: { score: number | null; status?: string }) {
  if (status === "insufficient_data" || score === null) {
    return (
      <Badge tone="neutral" title="Not enough reliable data to calculate a score">
        Insufficient data
      </Badge>
    );
  }
  const band = scoreBand(score);
  const tone = band.tone === "strong" ? "positive" : band.tone === "moderate" ? "accent" : "warning";
  return (
    <Badge tone={tone} title={`${band.label} research score`}>
      <span className="tabular">{score.toFixed(1)}</span>
      <span className="sr-only"> out of 100, {band.label}</span>
    </Badge>
  );
}

export function ScoreLocked({ compact = false }: { compact?: boolean }) {
  if (compact) return <span className="text-xs text-faint">Premium</span>;
  return (
    <div className="rounded-md border border-dashed border-line-strong px-4 py-6 text-center text-sm">
      <p className="font-medium">Research score available with Premium</p>
      <p className="mt-1 text-muted">Free members can view scores for a small set of sample coins.</p>
      <Link href="/pricing" className={buttonClass("primary", "sm", "mt-3")}>
        View plans
      </Link>
    </div>
  );
}

function SubscoreRow({ s }: { s: StoredScore["subscores"][number] }) {
  const pct = s.score ?? 0;
  return (
    <li className="py-3">
      <div className="flex items-center justify-between gap-3 text-sm">
        <span className="font-medium">{CATEGORY_LABELS[s.category]}</span>
        <span className="flex items-center gap-2 text-xs text-faint">
          <span>weight {(s.weight * 100).toFixed(0)}%</span>
          {s.status === "ok" ? (
            <span className="tabular w-10 text-right text-sm font-semibold text-fg">{s.score!.toFixed(1)}</span>
          ) : (
            <Badge tone={s.status === "unreliable" ? "warning" : "neutral"}>{s.status === "unreliable" ? "Unreliable input" : "Missing data"}</Badge>
          )}
        </span>
      </div>
      {s.status === "ok" ? (
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-subtle" aria-hidden>
          <div className="h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
        </div>
      ) : null}
      <p className="mt-1.5 text-xs text-muted">{s.explanation}</p>
    </li>
  );
}

export function ScoreBreakdown({ access }: { access: ScoreAccess }) {
  if (access.kind === "locked") return <ScoreLocked />;
  if (access.kind === "unscored") return <p className="text-sm text-muted">{access.reason}</p>;
  if (access.kind === "unavailable") {
    return <p className="text-sm text-muted">A research score has not been calculated for this asset yet, or the scoring data is temporarily unavailable.</p>;
  }
  const s = access.score;
  return (
    <div>
      <div className="flex flex-wrap items-center gap-3">
        <span className={cn("tabular text-4xl font-semibold", s.overallScore === null && "text-2xl text-muted")}>
          {s.overallScore === null ? "Insufficient data" : s.overallScore.toFixed(1)}
        </span>
        {s.overallScore !== null ? <span className="text-sm text-muted">/ 100 · {scoreBand(s.overallScore).label}</span> : null}
      </div>
      <p className="mt-2 text-xs text-faint">
        Methodology v{s.methodologyVersion} · data coverage {(s.coverage * 100).toFixed(0)}% · calculated {fmtDate(s.calculatedAt, { time: true })}
        {s.origin === "live" ? " · computed live (no database configured)" : ""}
      </p>
      {s.status === "insufficient_data" ? (
        <p className="mt-3 text-sm text-muted">
          Not enough reliable inputs were available to produce an overall score. Missing data is reported, not treated as poor quality.
        </p>
      ) : null}
      <ul className="mt-3 divide-y divide-line">
        {s.subscores.map((sub) => (
          <SubscoreRow key={sub.category} s={sub} />
        ))}
      </ul>
      <p className="mt-3 text-xs text-faint">
        The research score is a transparent heuristic, not a prediction of future returns and not financial advice.{" "}
        <Link className="underline" href="/about#methodology">
          How it works
        </Link>
      </p>
    </div>
  );
}
