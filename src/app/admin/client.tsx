"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button, Field, inputClass } from "@/components/ui";
import { SCORE_CATEGORIES, CATEGORY_LABELS, type ScoringConfig } from "@/config/scoring";
import { api } from "@/lib/client/api";
import { useMutation } from "@/lib/client/use-mutation";

export function FlagToggle({ flagKey, enabled }: { flagKey: string; enabled: boolean }) {
  const m = useMutation();
  return (
    <span className="inline-flex items-center gap-2">
      <Button size="sm" variant={enabled ? "secondary" : "ghost"} disabled={m.pending} onClick={() => m.run("/api/admin/feature-flags", "PATCH", { key: flagKey, enabled: !enabled })} aria-pressed={enabled}>
        {enabled ? "On" : "Off"}
      </Button>
      {m.error ? <span className="text-xs text-negative">{m.error}</span> : null}
    </span>
  );
}

export interface ArticleFormValues {
  id?: string;
  slug: string;
  title: string;
  summary: string;
  category: string;
  tags: string;
  accessLevel: string;
  authorName: string;
  relatedCoinIds: string;
  dataSources: string;
  isAiAssisted: boolean;
  body: string;
  status?: string;
}

const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
/** One source per line: "Name | https://url" (URL optional). */
const sources = (s: string) =>
  s
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => {
      const [name, url] = l.split("|").map((x) => x.trim());
      return { name: name!, url: url || null };
    });

export function ArticleEditor({ initial }: { initial: ArticleFormValues }) {
  const router = useRouter();
  const [v, setV] = useState(initial);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof ArticleFormValues) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setV({ ...v, [k]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value });

  async function save(action: "save" | "publish" | "unpublish" | "archive") {
    setBusy(true);
    setMsg(null);
    const payload = {
      slug: v.slug,
      title: v.title,
      summary: v.summary,
      category: v.category,
      tags: list(v.tags),
      accessLevel: v.accessLevel,
      authorName: v.authorName,
      relatedCoinIds: list(v.relatedCoinIds),
      dataSources: sources(v.dataSources),
      isAiAssisted: v.isAiAssisted,
      body: v.body,
    };
    const res = v.id ? await api(`/api/admin/research/${v.id}`, { method: "PATCH", body: { ...payload, action } }) : await api<{ id: string }>("/api/admin/research", { body: payload });
    setBusy(false);
    if (!res.ok) {
      const details = Array.isArray(res.error.details) ? ` (${(res.error.details as { path: string; message: string }[]).map((d) => `${d.path}: ${d.message}`).join("; ")})` : "";
      return setMsg({ ok: false, text: res.error.message + details });
    }
    if (!v.id) return router.push(`/admin/research/${(res.data as { id: string }).id}`);
    setMsg({ ok: true, text: action === "save" ? "Saved." : `Article ${action === "publish" ? "published" : action === "unpublish" ? "moved to draft" : "archived"}.` });
    router.refresh();
  }

  return (
    <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); save("save"); }}>
      <div className="grid gap-4 md:grid-cols-2">
        <Field id="title" label="Title"><input id="title" value={v.title} onChange={set("title")} className={inputClass} /></Field>
        <Field id="slug" label="Slug" hint="lowercase-with-hyphens"><input id="slug" value={v.slug} onChange={set("slug")} className={inputClass} /></Field>
        <Field id="category" label="Category">
          <select id="category" value={v.category} onChange={set("category")} className={inputClass}>
            {["market", "tokenomics", "fundamentals", "risk", "methodology", "education"].map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </Field>
        <Field id="access" label="Access level">
          <select id="access" value={v.accessLevel} onChange={set("accessLevel")} className={inputClass}>
            <option value="free">Free</option>
            <option value="premium">Premium</option>
            <option value="founding">Founding</option>
          </select>
        </Field>
        <Field id="author" label="Author name"><input id="author" value={v.authorName} onChange={set("authorName")} className={inputClass} /></Field>
        <Field id="tags" label="Tags (comma separated)"><input id="tags" value={v.tags} onChange={set("tags")} className={inputClass} /></Field>
        <Field id="coins" label="Related coin IDs (comma separated)"><input id="coins" value={v.relatedCoinIds} onChange={set("relatedCoinIds")} className={inputClass} /></Field>
        <label className="flex items-center gap-2 self-end pb-2 text-sm">
          <input type="checkbox" checked={v.isAiAssisted} onChange={set("isAiAssisted")} /> AI-assisted draft (shown to readers)
        </label>
      </div>
      <Field id="summary" label="Summary (public teaser)"><textarea id="summary" rows={3} value={v.summary} onChange={set("summary")} className={inputClass + " h-auto py-2"} /></Field>
      <Field id="sources" label="Data sources" hint='One per line: "Name | https://link"'><textarea id="sources" rows={3} value={v.dataSources} onChange={set("dataSources")} className={inputClass + " h-auto py-2 font-mono text-xs"} /></Field>
      <Field id="body" label="Body (Markdown)"><textarea id="body" rows={18} value={v.body} onChange={set("body")} className={inputClass + " h-auto py-2 font-mono text-xs"} /></Field>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" disabled={busy}>{v.id ? "Save" : "Create draft"}</Button>
        {v.id && v.status !== "published" ? <Button type="button" variant="secondary" disabled={busy} onClick={() => save("publish")}>Publish</Button> : null}
        {v.id && v.status === "published" ? <Button type="button" variant="secondary" disabled={busy} onClick={() => save("unpublish")}>Unpublish</Button> : null}
        {v.id && v.status !== "archived" ? <Button type="button" variant="ghost" disabled={busy} onClick={() => save("archive")}>Archive</Button> : null}
        {v.id ? <a href={`/research/${v.slug}`} target="_blank" className="text-sm text-accent underline">Preview</a> : null}
        {msg ? <span role="status" className={msg.ok ? "text-sm text-positive" : "text-sm text-negative"}>{msg.text}</span> : null}
      </div>
    </form>
  );
}

export function ScoringForm({ current }: { current: ScoringConfig }) {
  const m = useMutation();
  const [weights, setWeights] = useState<Record<string, string>>(Object.fromEntries(SCORE_CATEGORIES.map((c) => [c, String(current.weights[c])])));
  const [version, setVersion] = useState(current.methodologyVersion);
  const [coverage, setCoverage] = useState(String(current.minWeightCoverage));
  const [notes, setNotes] = useState("");
  const sum = Object.values(weights).reduce((s, w) => s + (Number(w) || 0), 0);
  return (
    <form
      className="grid gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (!confirm("Publish this scoring configuration? Future scoring runs will use it.")) return;
        m.run("/api/admin/scoring-config", "POST", {
          methodologyVersion: version,
          weights: Object.fromEntries(Object.entries(weights).map(([k, w]) => [k, Number(w)])),
          minWeightCoverage: Number(coverage),
          requiredCategories: current.requiredCategories,
          maxMarketDataAgeMinutes: current.maxMarketDataAgeMinutes,
          minHistoryDays: current.minHistoryDays,
          notes,
        });
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {SCORE_CATEGORIES.map((c) => (
          <Field key={c} id={`w-${c}`} label={CATEGORY_LABELS[c]}>
            <input id={`w-${c}`} inputMode="decimal" value={weights[c]} onChange={(e) => setWeights({ ...weights, [c]: e.target.value })} className={inputClass} />
          </Field>
        ))}
      </div>
      <p className={Math.abs(sum - 1) < 1e-6 ? "text-sm text-positive" : "text-sm text-negative"}>Weights sum: {sum.toFixed(4)} (must equal 1)</p>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field id="ver" label="Methodology version" hint="Bump when weights or rules change."><input id="ver" value={version} onChange={(e) => setVersion(e.target.value)} className={inputClass} /></Field>
        <Field id="cov" label="Minimum weight coverage"><input id="cov" value={coverage} onChange={(e) => setCoverage(e.target.value)} className={inputClass} /></Field>
        <Field id="notes" label="Change notes"><input id="notes" value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={1000} className={inputClass} /></Field>
      </div>
      <div className="flex items-center gap-3">
        <Button type="submit" disabled={m.pending || Math.abs(sum - 1) > 1e-6}>Publish configuration</Button>
        {m.error ? <span role="alert" className="text-sm text-negative">{m.error}</span> : null}
      </div>
    </form>
  );
}

export function UnlockForm({ coins }: { coins: { id: string; name: string }[] }) {
  const m = useMutation();
  const [f, setF] = useState({ coinId: coins[0]?.id ?? "", unlockAt: "", amount: "", allocation: "", sourceType: "project_reported", sourceName: "", sourceUrl: "", confidence: "medium", notes: "" });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <form
      className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
      onSubmit={(e) => {
        e.preventDefault();
        m.run("/api/admin/unlocks", "POST", {
          ...f,
          amount: Number(f.amount),
          unlockAt: f.unlockAt ? new Date(f.unlockAt).toISOString() : "",
          allocation: f.allocation || undefined,
          sourceName: f.sourceName || undefined,
          notes: f.notes || undefined,
        });
      }}
    >
      <Field id="u-coin" label="Coin"><select id="u-coin" value={f.coinId} onChange={set("coinId")} className={inputClass}>{coins.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
      <Field id="u-date" label="Unlock date (UTC)"><input id="u-date" type="datetime-local" value={f.unlockAt} onChange={set("unlockAt")} className={inputClass} /></Field>
      <Field id="u-amount" label="Amount (tokens)" error={m.fields.amount}><input id="u-amount" inputMode="decimal" value={f.amount} onChange={set("amount")} className={inputClass} /></Field>
      <Field id="u-alloc" label="Allocation (e.g. team, investors)"><input id="u-alloc" value={f.allocation} onChange={set("allocation")} className={inputClass} /></Field>
      <Field id="u-stype" label="Source type"><select id="u-stype" value={f.sourceType} onChange={set("sourceType")} className={inputClass}><option value="project_reported">Project-reported</option><option value="provider">Data provider</option></select></Field>
      <Field id="u-conf" label="Confidence"><select id="u-conf" value={f.confidence} onChange={set("confidence")} className={inputClass}><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></Field>
      <Field id="u-sname" label="Source name"><input id="u-sname" value={f.sourceName} onChange={set("sourceName")} className={inputClass} /></Field>
      <Field id="u-surl" label="Source URL (https, required)" error={m.fields.sourceUrl}><input id="u-surl" value={f.sourceUrl} onChange={set("sourceUrl")} className={inputClass} /></Field>
      <Field id="u-notes" label="Notes"><input id="u-notes" value={f.notes} onChange={set("notes")} className={inputClass} /></Field>
      <div className="flex items-center gap-3 sm:col-span-2 lg:col-span-3">
        <Button type="submit" disabled={m.pending}>Record unlock event</Button>
        {m.error ? <span role="alert" className="text-sm text-negative">{m.error}</span> : null}
      </div>
    </form>
  );
}

export function DeleteUnlock({ id }: { id: string }) {
  const m = useMutation();
  return <Button size="sm" variant="ghost" disabled={m.pending} onClick={() => confirm("Delete this unlock event?") && m.run(`/api/admin/unlocks/${id}`, "DELETE")}>Delete</Button>;
}

export function CoinToggles({ id, isActive, isSample }: { id: string; isActive: boolean; isSample: boolean }) {
  const m = useMutation();
  return (
    <span className="inline-flex gap-1">
      <Button size="sm" variant="ghost" disabled={m.pending} onClick={() => m.run(`/api/admin/coins/${id}`, "PATCH", { isActive: !isActive })}>{isActive ? "Deactivate" : "Activate"}</Button>
      <Button size="sm" variant="ghost" disabled={m.pending} onClick={() => m.run(`/api/admin/coins/${id}`, "PATCH", { isScoreSample: !isSample })}>{isSample ? "Unset sample" : "Make sample"}</Button>
      {m.error ? <span className="text-xs text-negative">{m.error}</span> : null}
    </span>
  );
}
