"use client";

import { Badge, Card, Loading, PageHeader, Table } from "@/components/ui";
import { DASH, num, pct, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

function tone(score: number | null): "good" | "bad" | "muted" {
  if (score == null) return "muted";
  return score >= 20 ? "good" : score <= -20 ? "bad" : "muted";
}

function ScoreBar({ score }: { score: number | null }) {
  if (score == null) return <p className="text-sm text-muted">Insufficient data</p>;
  const left = score < 0 ? 50 + score / 2 : 50;
  const width = Math.abs(score) / 2;
  return (
    <div>
      <div className="relative h-2 rounded bg-elevated" role="img"
        aria-label={`Sentiment ${score} on a scale from -100 bearish to +100 bullish`}>
        <div className="absolute top-0 h-2 w-px bg-line" style={{ left: "50%" }} />
        <div className={`absolute top-0 h-2 rounded ${score >= 0 ? "bg-up" : "bg-down"}`}
          style={{ left: `${left}%`, width: `${width}%` }} />
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-muted"><span>-100 bearish</span><span>0</span><span>+100 bullish</span></div>
    </div>
  );
}

function CoinCard({ c }: { c: Any }) {
  const m = c.market ?? {};
  return (
    <Card title={c.coin} actions={<Badge tone={tone(c.score)}>{c.label}</Badge>}>
      <div className="mb-2 flex items-baseline gap-3">
        <span className="num text-3xl font-semibold">{c.score == null ? DASH : (c.score > 0 ? `+${c.score}` : c.score)}</span>
        <span className="text-xs text-muted">confidence {pct(c.confidence, 0)}</span>
        <span className="text-xs text-muted">change {c.change == null ? DASH : (c.change > 0 ? `+${c.change}` : c.change)} (last 6h vs 6–24h)</span>
      </div>
      <ScoreBar score={c.score} />
      <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted">Posts used / seen</dt><dd className="num">{c.posts_used} / {c.posts_total}</dd>
        <dt className="text-muted">Spam/bot excluded</dt><dd className="num">{c.excluded_spam_bot}</dd>
        <dt className="text-muted">Duplicates excluded</dt><dd className="num">{c.duplicates}</dd>
        <dt className="text-muted">Unique authors</dt><dd className="num">{c.unique_authors}</dd>
        <dt className="text-muted">Avg source credibility</dt><dd className="num">{c.credibility_avg == null ? DASH : pct(c.credibility_avg, 0)}</dd>
        <dt className="text-muted">Latest post</dt><dd>{c.latest_post_at ? time(c.latest_post_at) : DASH}</dd>
      </dl>
      {c.warnings?.length > 0 && (
        <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-warn">{c.warnings.map((w: string) => <li key={w}>⚠ {w}</li>)}</ul>
      )}
      <div className="mt-3 rounded-lg border border-line p-3 text-xs">
        <p className="mb-1 font-medium">Combined with market data</p>
        {m.available ? (
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
            <dt className="text-muted">Alignment</dt><dd>{m.alignment}</dd>
            <dt className="text-muted">Technical direction</dt><dd>{m.technical_direction ?? "none"} {m.technical_score != null && `(score ${m.technical_score})`}</dd>
            <dt className="text-muted">24h change</dt><dd className="num">{pct(m.change_24h)}</dd>
            <dt className="text-muted">Volatility percentile</dt><dd className="num">{pct(m.atr_percentile, 0)}</dd>
            <dt className="text-muted">Book imbalance / spread</dt><dd className="num">{num(m.book_imbalance, 2)} / {num(m.spread_bps, 2)} bps</dd>
            <dt className="text-muted">Funding rate</dt><dd className="num">{m.funding_rate == null ? DASH : pct(m.funding_rate, 4)}</dd>
            <dt className="text-muted">Open interest</dt><dd className="num">{m.open_interest == null ? DASH : num(m.open_interest, 0)}</dd>
          </dl>
        ) : <p className="text-muted">{m.note ?? "Market context unavailable."}</p>}
        {m.derivatives_note && <p className="mt-1 text-muted">Derivatives: {m.derivatives_note}</p>}
        {m.risk_flags?.length > 0 && <ul className="mt-1 list-disc pl-5 text-warn">{m.risk_flags.map((r: string) => <li key={r}>⚠ {r}</li>)}</ul>}
      </div>
      {c.drivers?.length > 0 && (
        <div className="mt-3 space-y-2">
          <p className="text-xs font-medium">Most influential posts</p>
          {c.drivers.map((d: Any) => (
            <a key={d.url} href={d.url} target="_blank" rel="noopener noreferrer" className="block rounded-lg border border-line p-2 text-xs hover:bg-elevated">
              <span className="text-muted">@{d.author ?? "unknown"}{d.trusted ? " (trusted)" : ""} · credibility {pct(d.credibility, 0)} · {time(d.created_at)} · {d.sentiment > 0 ? "▲" : d.sentiment < 0 ? "▼" : "◆"} {num(d.sentiment, 2)}</span>
              <span className="mt-1 block">{d.text}</span>
            </a>
          ))}
        </div>
      )}
    </Card>
  );
}

export default function SocialPage() {
  const st = useApi<Any>("/x/status", 60_000);
  const x = useApi<Any>("/x/sentiment", 60_000);
  const s = st.data;
  const d = x.data;
  return (
    <>
      <PageHeader title="X sentiment" subtitle="Crypto sentiment from public X posts through the official X API, read-only. Information only: NEXUS never opens a trade because of X posts." />
      <Loading error={st.error || x.error} loading={(st.loading && !s) || (x.loading && !d)} />
      {s && (
        <Card title="Connection" actions={<Badge tone={s.configured ? (s.status?.ok === false ? "bad" : "good") : "warn"}>{s.configured ? (s.status?.ok === false ? "error" : "configured") : "not configured"}</Badge>}>
          {!s.configured ? (
            <ol className="list-decimal space-y-1 pl-5 text-sm">
              <li>Create an app at console.x.com and copy its <b>Bearer token</b> (app-only, read-only use).</li>
              <li>Add API credits in the X Developer Console (X bills per post read).</li>
              <li>In Render → nexus-ai-fra → Environment, add <code>NEXUS_X_BEARER_TOKEN</code> and save.</li>
            </ol>
          ) : (
            <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm md:grid-cols-4">
              <dt className="text-muted">Last refresh</dt><dd>{s.status?.last_attempt ? time(s.status.last_attempt) : "pending"}</dd>
              <dt className="text-muted">Last success</dt><dd>{s.status?.last_ok ? time(s.status.last_ok) : DASH}</dd>
              <dt className="text-muted">Spent today (est.)</dt><dd className="num">${s.usage?.spent_usd ?? "0"} of ${s.daily_budget_usd}</dd>
              <dt className="text-muted">Refresh every</dt><dd>{s.refresh_minutes} min · {s.max_posts_per_query} posts/query</dd>
              <dt className="text-muted">Coins</dt><dd>{s.coins.join(", ")}</dd>
              <dt className="text-muted">Watched accounts</dt><dd>{s.watch_accounts.map((a: string) => `@${a}`).join(", ")}</dd>
              <dt className="text-muted">Rate limited until</dt><dd>{s.usage?.rate_limited_until ? time(s.usage.rate_limited_until) : "no"}</dd>
              <dt className="text-muted">Permissions</dt><dd>read-only (GET allowlist)</dd>
            </dl>
          )}
          {s.status?.errors?.length > 0 && <ul role="alert" className="mt-2 list-disc pl-5 text-xs text-down">{s.status.errors.map((e: string) => <li key={e}>{e}</li>)}</ul>}
        </Card>
      )}
      {d && !d.available && <p className="my-4 rounded-lg border border-line bg-elevated p-3 text-sm">{d.message}</p>}
      {d?.available && (
        <div className="mt-4 space-y-4">
          <p className="rounded-lg border border-line bg-elevated p-3 text-xs text-muted">{d.policy} Snapshot {time(d.generated_at)} · {d.posts_in_window} posts in the last 24h.{d.market_context_note ? ` ${d.market_context_note}` : ""}</p>
          {Object.keys(d.blocks ?? {}).length > 0 && (
            <Card title="New entries paused by confirmed news">
              <ul className="list-disc pl-5 text-sm text-down">{Object.entries(d.blocks).map(([c, r]) => <li key={c}><b>{c}</b>: {String(r)}</li>)}</ul>
            </Card>
          )}
          <div className="grid gap-4 md:grid-cols-2">{d.coins.map((c: Any) => <CoinCard key={c.coin} c={c} />)}</div>
          <Card title="News alerts (last 24h)">
            <Table rows={d.alerts} empty="No news events detected." columns={[
              { key: "last_seen", label: "Time", render: (r: Any) => time(r.last_seen) },
              { key: "coin", label: "Coin" },
              { key: "label", label: "Type", render: (r: Any) => <>{r.label}{r.high_impact ? " ⚡" : ""}</> },
              { key: "status", label: "Status", render: (r: Any) => <Badge tone={r.status === "confirmed" ? "good" : "warn"}>{r.status}</Badge> },
              { key: "fake_news_risk", label: "Fake-news risk", render: (r: Any) => <Badge tone={r.fake_news_risk === "high" ? "bad" : r.fake_news_risk === "medium" ? "warn" : "muted"}>{r.fake_news_risk}</Badge> },
              { key: "credible_sources", label: "Credible sources", render: (r: Any) => `${r.credible_sources} of ${r.sources}` },
              { key: "headline", label: "Top post", render: (r: Any) => (
                <span className="block max-w-md text-xs"><a href={r.url} target="_blank" rel="noopener noreferrer" className="underline">@{r.author}</a> ({pct(r.credibility, 0)}): {r.headline}
                  {r.warnings?.map((w: string) => <span key={w} className="block text-warn">⚠ {w}</span>)}</span>) },
            ]} />
          </Card>
          <div className="grid gap-4 md:grid-cols-2">
            <Card title="Trending coins">
              <Table rows={d.trending?.coins} empty="No data." columns={[
                { key: "coin", label: "Coin" },
                { key: "mentions_per_hour", label: "Mentions / hour (6h)", render: (r: Any) => num(r.mentions_per_hour, 2) },
                { key: "change", label: "vs previous 18h", render: (r: Any) => r.change == null ? DASH : pct(r.change, 0) },
              ]} />
              <p className="mt-3 text-xs font-medium">Crypto-related X trends</p>
              {d.trending?.x_trends?.length ? (
                <ul className="mt-1 list-disc pl-5 text-xs">{d.trending.x_trends.map((t: Any) => <li key={t.trend_name}>{t.trend_name}{t.tweet_count ? ` · ${t.tweet_count} posts` : ""}</li>)}</ul>
              ) : <p className="text-xs text-muted">None in the current worldwide trends.</p>}
            </Card>
            <Card title="Sentiment history">
              <Table rows={[...(d.history ?? [])].reverse().slice(0, 24)} empty="No history yet." columns={[
                { key: "t", label: "Time", render: (r: Any) => time(r.t) },
                ...d.coins.map((c: Any) => ({ key: c.coin, label: c.coin, render: (r: Any) => r[c.coin] == null ? DASH : String(r[c.coin]) })),
              ]} />
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
