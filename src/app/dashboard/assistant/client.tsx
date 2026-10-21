"use client";

import { useState } from "react";
import { Markdown } from "@/components/research/markdown";
import { Badge, Button, Card, CardBody, Field, inputClass, Notice } from "@/components/ui";
import { AI_FEATURES, FEATURE_LABELS, type AIFeature } from "@/lib/ai/prompt";
import { api } from "@/lib/client/api";

interface Answer {
  answer: string;
  model: string;
  generatedAt: string;
  servedByFallback: boolean;
  sources: { label: string; source: string; asOf: string | null }[];
}

export function AssistantForm({ coins, articles }: { coins: { id: string; label: string }[]; articles: { slug: string; title: string }[] }) {
  const [feature, setFeature] = useState<AIFeature>("summary");
  const [coinA, setCoinA] = useState(coins[0]?.id ?? "");
  const [coinB, setCoinB] = useState(coins[1]?.id ?? "");
  const [slug, setSlug] = useState(articles[0]?.slug ?? "");
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const needsCoin = feature === "summary" || feature === "explain_score" || feature === "tokenomics" || feature === "compare";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const coinIds = feature === "compare" ? [coinA, coinB] : needsCoin ? [coinA] : [];
    const res = await api<Answer>("/api/ai/research", { body: { feature, coinIds, articleSlug: feature === "article_qa" ? slug : undefined, question } });
    setBusy(false);
    if (!res.ok) return setError(res.error.message);
    setAnswer(res.data);
  }

  return (
    <div className="space-y-6">
      <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
        <Field id="ai-feature" label="What would you like?">
          <select id="ai-feature" value={feature} onChange={(e) => setFeature(e.target.value as AIFeature)} className={inputClass}>
            {AI_FEATURES.map((f) => (
              <option key={f} value={f} disabled={f === "article_qa" && articles.length === 0}>
                {FEATURE_LABELS[f]}
              </option>
            ))}
          </select>
        </Field>
        {needsCoin ? (
          <Field id="ai-coin-a" label={feature === "compare" ? "First coin" : "Coin"}>
            <select id="ai-coin-a" value={coinA} onChange={(e) => setCoinA(e.target.value)} className={inputClass}>
              {coins.map((c) => (
                <option key={c.id} value={c.id}>{c.label}</option>
              ))}
            </select>
          </Field>
        ) : null}
        {feature === "compare" ? (
          <Field id="ai-coin-b" label="Second coin">
            <select id="ai-coin-b" value={coinB} onChange={(e) => setCoinB(e.target.value)} className={inputClass}>
              {coins.map((c) => (
                <option key={c.id} value={c.id}>{c.label}</option>
              ))}
            </select>
          </Field>
        ) : null}
        {feature === "article_qa" ? (
          <Field id="ai-article" label="Article">
            <select id="ai-article" value={slug} onChange={(e) => setSlug(e.target.value)} className={inputClass}>
              {articles.map((a) => (
                <option key={a.slug} value={a.slug}>{a.title}</option>
              ))}
            </select>
          </Field>
        ) : null}
        <div className="sm:col-span-2">
          <Field id="ai-question" label="Question (optional)">
            <textarea id="ai-question" value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={1000} rows={3} className={inputClass + " h-auto py-2"} />
          </Field>
        </div>
        <div className="flex items-center gap-3 sm:col-span-2">
          <Button type="submit" disabled={busy}>
            {busy ? "Generating…" : "Ask the assistant"}
          </Button>
          {error ? <p role="alert" className="text-sm text-negative">{error}</p> : null}
        </div>
      </form>

      {answer ? (
        <Card>
          <CardBody className="space-y-4">
            <div className="flex flex-wrap items-center gap-2 text-xs text-faint">
              <Badge tone="warning">AI-generated</Badge>
              <span>
                {answer.model} · {new Date(answer.generatedAt).toUTCString()}
              </span>
              {answer.servedByFallback ? <span>(answered by a fallback model)</span> : null}
            </div>
            <Markdown source={answer.answer} />
            <div className="border-t border-line pt-3">
              <p className="text-xs font-medium">Data provided to the assistant</p>
              <ul className="mt-1 space-y-0.5 text-xs text-muted">
                {answer.sources.map((s, i) => (
                  <li key={i}>
                    {s.label} — {s.source}
                    {s.asOf ? ` · as of ${new Date(s.asOf).toUTCString()}` : ""}
                  </li>
                ))}
              </ul>
            </div>
            <Notice>AI output may be wrong. Verify important figures against the linked data before relying on them.</Notice>
          </CardBody>
        </Card>
      ) : null}
    </div>
  );
}
