"use client";

import { useState } from "react";
import { Badge, Card, Loading, PageHeader, Table } from "@/components/ui";
import { DASH, num, pct, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const px = (v: string | null | undefined) => (v == null ? DASH : num(v, Number(v) >= 100 ? 2 : Number(v) >= 1 ? 4 : 6));

const VIEW_TONE: Record<string, "good" | "bad" | "warn" | "muted"> = {
  bullish: "good", bearish: "bad", against_trading: "warn", neutral: "muted", unavailable: "muted",
};

function List({ title, items, tone }: { title: string; items: string[]; tone?: string }) {
  if (!items?.length) return null;
  return (
    <div>
      <p className="mb-1 text-xs font-medium">{title}</p>
      <ul className={`list-disc space-y-0.5 pl-5 text-xs ${tone ?? ""}`}>{items.map((x, i) => <li key={i}>{x}</li>)}</ul>
    </div>
  );
}

function Proposal({ p }: { p: Any }) {
  const [open, setOpen] = useState(false);
  const approved = p.risk_manager.verdict === "APPROVE";
  return (
    <Card title={p.symbol} actions={<Badge tone={approved ? "good" : "warn"}>{p.final_decision}</Badge>}>
      <p className="mb-3 text-sm">{p.explanation}</p>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs md:grid-cols-4">
        <dt className="text-muted">Direction</dt><dd>{p.direction ?? "none"}</dd>
        <dt className="text-muted">Regime</dt><dd>{p.regime ?? DASH}</dd>
        <dt className="text-muted">Strategy</dt><dd>{p.strategy ?? "none validated"}</dd>
        <dt className="text-muted">Confidence</dt><dd className="num">{pct(p.confidence, 0)}</dd>
        <dt className="text-muted">Entry</dt><dd className="num">{px(p.entry)}</dd>
        <dt className="text-muted">Stop-loss</dt><dd className="num">{px(p.stop_loss)}</dd>
        <dt className="text-muted">Take-profits</dt><dd className="num">{p.take_profits?.length ? p.take_profits.map((t: Any) => `${t.level}: ${px(t.price)}`).join(" · ") : DASH}</dd>
        <dt className="text-muted">Reward/risk</dt><dd className="num">{p.reward_risk ?? DASH}</dd>
        <dt className="text-muted">Position size</dt><dd className="num">{p.quantity && p.size_ok ? `${p.quantity} (${p.notional} USDT)` : p.size_note}</dd>
        <dt className="text-muted">Risk</dt><dd className="num">{p.risk_amount ? `${p.risk_amount} USDT (${pct(p.risk_pct, 2)})` : DASH}</dd>
      </dl>
      <div className="mt-3 rounded-lg border border-line p-3">
        <p className="mb-1 text-sm font-medium">Risk Manager: <Badge tone={approved ? "good" : "bad"}>{p.risk_manager.verdict}</Badge></p>
        <List title="Reasons to reject" items={p.reasons_to_reject} tone="text-down" />
        <p className="mt-1 text-[11px] text-muted">{p.risk_manager.note}</p>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-3">
        <List title="Technical evidence" items={p.technical_evidence} />
        <List title="Sentiment evidence" items={p.sentiment_evidence} />
        <List title="Contradicting evidence" items={p.contradicting_evidence} tone="text-warn" />
      </div>
      <button type="button" className="mt-3 text-xs underline" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? "Hide" : "Show"} the six agent reports
      </button>
      {open && (
        <div className="mt-2 grid gap-2 md:grid-cols-2">
          {p.agents.map((a: Any) => (
            <div key={a.agent} className="rounded-lg border border-line p-2 text-xs">
              <p className="mb-1 flex flex-wrap items-center gap-2 font-medium">{a.agent.replace("_", " ")}
                <Badge tone={VIEW_TONE[a.view] ?? "muted"}>{a.view.replace("_", " ")}</Badge>
                <span className="num text-muted">score {a.score > 0 ? `+${a.score}` : a.score} · confidence {pct(a.confidence, 0)}</span></p>
              <List title="Evidence" items={a.evidence} />
              <List title="Against" items={a.contradictions} tone="text-warn" />
              <List title="Warnings" items={a.warnings} tone="text-muted" />
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export default function ProposalsPage() {
  const d = useApi<Any>("/agents/proposals", 60_000);
  const r = useApi<Any>("/agents/reviews?mode=paper", 60_000);
  return (
    <>
      <PageHeader title="AI trade proposals" subtitle="Six analysis agents, then an independent rule-based Risk Manager whose rejection is final. 'No trade' is a normal answer. Nothing on this page places orders." />
      <Loading error={d.error} loading={d.loading && !d.data} />
      {d.data && (
        <div className="space-y-4">
          <p className="rounded-lg border border-line bg-elevated p-3 text-xs text-muted">{d.data.disclaimer} Generated {time(d.data.generated_at)} · portfolio from the {d.data.portfolio.source}.</p>
          {d.data.proposals.map((p: Any) => <Proposal key={p.symbol} p={p} />)}
        </div>
      )}
      <div className="mt-4">
        <Card title="Trade review (paper, last 50 closed trades)" actions={r.data?.summary ? <span className="text-xs text-muted">{Object.entries(r.data.summary).map(([k, v]) => `${k}: ${v}`).join(" · ") || "no trades yet"}</span> : undefined}>
          <Table rows={r.data?.reviews} empty="No closed paper trades to review yet." columns={[
            { key: "closed_at", label: "Closed", render: (x: Any) => time(x.closed_at) },
            { key: "symbol", label: "Symbol" },
            { key: "strategy", label: "Strategy", render: (x: Any) => x.strategy ?? DASH },
            { key: "outcome", label: "Outcome", render: (x: Any) => `${x.outcome} (${num(x.realized_pnl)})` },
            { key: "labels", label: "Classification", render: (x: Any) => <span className="flex flex-wrap gap-1">{x.labels.map((l: string) => <Badge key={l} tone={l === "good trade" || l === "good execution" ? "good" : l === "strategy failure" ? "muted" : "bad"}>{l}</Badge>)}</span> },
            { key: "notes", label: "Why", render: (x: Any) => <span className="block max-w-sm text-xs text-muted">{x.notes.join("; ")}</span> },
          ]} />
        </Card>
      </div>
    </>
  );
}
