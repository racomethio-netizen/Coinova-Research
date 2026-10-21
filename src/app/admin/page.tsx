import { redirect } from "next/navigation";
import { Badge, Card, CardBody, CardHeader, PageHeader, Stat } from "@/components/ui";
import { formatPrice } from "@/config/plans";
import { requireStaffPage } from "@/lib/auth/session";
import { getEnv } from "@/lib/env";
import { fmtDate, fmtNumber } from "@/lib/format";
import { getAdminOverview } from "@/server/admin";
import { FlagToggle } from "./client";

const JOB_TONE: Record<string, "positive" | "negative" | "warning"> = { succeeded: "positive", failed: "negative", running: "warning" };

export default async function AdminOverviewPage() {
  const viewer = await requireStaffPage();
  if (!viewer.isAdmin) redirect("/admin/research");
  const env = getEnv();
  const [o, flags] = await Promise.all([getAdminOverview(viewer.supabase), viewer.supabase.from("feature_flags").select("key, enabled, description").order("key")]);
  const lastMarket = o.jobs.find((j) => j.job_name === "refresh-markets" && j.status === "succeeded");

  return (
    <div className="space-y-6">
      <PageHeader title="Admin overview" description="Operational status, subscribers, and recent activity." />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card><CardBody><dl><Stat label="Registered users" value={fmtNumber(o.users)} /></dl></CardBody></Card>
        <Card><CardBody><dl><Stat label="Live subscribers" value={fmtNumber(o.subscribers.live)} sub={`${o.subscribers.byTier.premium} premium · ${o.subscribers.byTier.founding} founding`} /></dl></CardBody></Card>
        <Card><CardBody><dl><Stat label="Est. MRR (live)" value={formatPrice(o.mrrCents)} sub="Plan prices × live subscriptions; yearly ÷ 12" /></dl></CardBody></Card>
        <Card><CardBody><dl><Stat label="Past due / canceling" value={`${o.subscribers.pastDue} / ${o.subscribers.cancelingAtPeriodEnd}`} sub={o.subscribers.test ? `${o.subscribers.test} test-mode subscriptions excluded` : undefined} /></dl></CardBody></Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Integrations" />
          <CardBody>
            <ul className="space-y-2 text-sm">
              <li className="flex justify-between"><span>Market data</span><span><Badge tone={env.MARKET_DATA_PROVIDER === "mock" ? "warning" : "positive"}>{env.MARKET_DATA_PROVIDER}</Badge> <span className="text-xs text-faint">last refresh {fmtDate(lastMarket?.finished_at ?? null, { time: true })}</span></span></li>
              <li className="flex justify-between"><span>Billing</span><Badge tone={env.BILLING_PROVIDER === "none" ? "neutral" : "warning"}>{env.BILLING_PROVIDER}</Badge></li>
              <li className="flex justify-between"><span>Email</span><Badge>{env.EMAIL_PROVIDER}</Badge></li>
              <li className="flex justify-between"><span>Telegram</span><Badge>{env.TELEGRAM_BOT_TOKEN ? "configured" : "not configured"}</Badge></li>
              <li className="flex justify-between"><span>AI</span><Badge>{env.AI_PROVIDER}{env.AI_MODEL ? ` · ${env.AI_MODEL}` : ""}</Badge></li>
              <li className="flex justify-between"><span>AI usage (30d)</span><span className="text-muted">{o.ai.requests30d} requests · {o.ai.errors30d} errors · {fmtNumber(o.ai.outputTokens30d)} output tokens</span></li>
            </ul>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Feature flags" />
          <CardBody>
            <ul className="divide-y divide-line text-sm">
              {(flags.data ?? []).map((f) => (
                <li key={f.key} className="flex items-center justify-between gap-3 py-2">
                  <span><code>{f.key}</code><span className="block text-xs text-faint">{f.description}</span></span>
                  <FlagToggle flagKey={f.key} enabled={f.enabled} />
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader title="Background jobs" description="Most recent runs. Failed windows are retried on the next invocation." />
        <CardBody className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="text-left text-xs uppercase text-faint"><tr><th className="py-2">Job</th><th>Status</th><th>Started</th><th>Attempts</th><th>Summary / error</th></tr></thead>
            <tbody className="divide-y divide-line">
              {o.jobs.length === 0 ? <tr><td colSpan={5} className="py-3 text-muted">No job runs recorded yet.</td></tr> : null}
              {o.jobs.map((j) => (
                <tr key={j.id}>
                  <td className="py-2 font-mono text-xs">{j.job_name}</td>
                  <td><Badge tone={JOB_TONE[j.status] ?? "neutral"}>{j.status}</Badge></td>
                  <td className="text-xs">{fmtDate(j.started_at, { time: true })}</td>
                  <td>{j.attempts}</td>
                  <td className="max-w-md truncate text-xs text-muted" title={j.error ?? JSON.stringify(j.summary)}>{j.error ?? JSON.stringify(j.summary)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardBody>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Notification deliveries (30d)" />
          <CardBody>
            {Object.keys(o.deliveries).length === 0 ? <p className="text-sm text-muted">No deliveries.</p> : (
              <ul className="space-y-1 text-sm">
                {Object.entries(o.deliveries).map(([ch, counts]) => (
                  <li key={ch}><span className="font-medium">{ch}</span>: <span className="text-muted">{Object.entries(counts).map(([s, n]) => `${s} ${n}`).join(" · ")}</span></li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Recent billing events" />
          <CardBody>
            {o.billingEvents.length === 0 ? <p className="text-sm text-muted">No billing events.</p> : (
              <ul className="space-y-1 text-xs">
                {o.billingEvents.map((e) => (
                  <li key={e.id} className="flex justify-between gap-2">
                    <span><code>{e.event_type}</code> {!e.livemode ? <Badge tone="warning">test</Badge> : null} <Badge tone={e.status === "processed" ? "positive" : e.status === "failed" ? "negative" : "neutral"}>{e.status}</Badge> {e.processing_note ? <span className="text-faint">{e.processing_note}</span> : null}</span>
                    <span className="text-faint">{fmtDate(e.received_at, { time: true })}</span>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader title="Audit log" description="Append-only record of administrative actions." />
        <CardBody>
          <ul className="space-y-1 text-xs">
            {o.audit.length === 0 ? <li className="text-muted">No entries.</li> : null}
            {o.audit.map((a) => (
              <li key={a.id} className="flex justify-between gap-2">
                <span><code>{a.action}</code> {a.target_type ? `${a.target_type}:${a.target_id}` : ""} <span className="text-faint">by {a.actor_id?.slice(0, 8) ?? "system"}</span></span>
                <span className="text-faint">{fmtDate(a.created_at, { time: true })}</span>
              </li>
            ))}
          </ul>
        </CardBody>
      </Card>
    </div>
  );
}
