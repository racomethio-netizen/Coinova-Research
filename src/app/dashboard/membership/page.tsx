import { Badge, Button, Card, CardBody, CardHeader, Notice, PageHeader } from "@/components/ui";
import { formatPrice, getPaidPlan, PAID_PLANS, TIER_LIMITS } from "@/config/plans";
import { requireUserPage } from "@/lib/auth/session";
import { getEnv } from "@/lib/env";
import { fmtDate } from "@/lib/format";
import { getBillingProvider, getMembership } from "@/server/billing";
import { simulateLifecycleEvent } from "./actions";
import { CancelButton, CheckoutButton } from "./client";

const CHECKOUT_MESSAGES: Record<string, { tone: "positive" | "negative" | "neutral"; text: string }> = {
  simulated: { tone: "positive", text: "Simulated payment processed through the verified webhook path. No real payment occurred." },
  returned: { tone: "neutral", text: "Thanks! Your membership activates as soon as the payment provider confirms the payment. This can take a moment." },
  expired: { tone: "negative", text: "That checkout session expired. Please start again." },
  failed: { tone: "negative", text: "The payment could not be confirmed." },
};

export default async function MembershipPage({ searchParams }: { searchParams: Promise<{ plan?: string; checkout?: string; simulated?: string }> }) {
  const sp = await searchParams;
  const viewer = await requireUserPage("/dashboard/membership");
  const { subscriptions, payments } = await getMembership(viewer.supabase, viewer.user.id);
  const provider = getBillingProvider();
  const env = getEnv();
  const open = subscriptions.find((s) => ["active", "trialing", "past_due", "incomplete"].includes(s.status));
  const selected = sp.plan ? getPaidPlan(sp.plan) : undefined;
  const limits = TIER_LIMITS[viewer.tier];
  const message = sp.checkout ? CHECKOUT_MESSAGES[sp.checkout] : undefined;

  return (
    <div className="space-y-6">
      <PageHeader title="Membership" description="Your plan, billing status, and payment history." />
      {message ? <Notice tone={message.tone}>{message.text}</Notice> : null}
      {!provider ? (
        <Notice tone="warning" title="Paid plans are not available yet">
          No billing provider is configured on this server. Membership upgrades will open once payments are set up.
        </Notice>
      ) : provider.id === "dev_simulator" ? (
        <Notice tone="warning" title="Development billing simulator">
          Payments here are simulated and never charge a card. Simulated subscriptions only grant access in databases where
          <code className="mx-1">require_livemode_subscriptions</code> is false (development seed data).
        </Notice>
      ) : null}

      <Card>
        <CardHeader title="Current plan" />
        <CardBody className="space-y-3 text-sm">
          <p>
            <Badge tone={viewer.tier === "free" ? "neutral" : "accent"}>{viewer.tier === "free" ? "Free" : viewer.tier === "premium" ? "Premium" : "Founding"}</Badge>
          </p>
          <p className="text-muted">
            Limits: {limits.watchlists} watchlists · {limits.watchlistItems} coins per list · {limits.portfolios} portfolios · {limits.alerts} alerts · {limits.aiRequestsPerDay} AI requests/day
          </p>
          {open ? (
            <div className="space-y-1 rounded-md border border-line p-4">
              <p>
                <span className="font-medium">{getPaidPlan(open.planId)?.name ?? open.planId}</span>{" "}
                <Badge tone={open.status === "active" ? "positive" : open.status === "past_due" ? "warning" : "neutral"}>{open.status.replace("_", " ")}</Badge>
                {!open.livemode ? <Badge tone="warning" className="ml-2">test mode</Badge> : null}
              </p>
              {open.status === "past_due" ? (
                <p className="text-warning">Your last payment failed. Access continues during a short grace period while payment is retried.</p>
              ) : null}
              <p className="text-muted">
                {open.cancelAtPeriodEnd ? "Access ends" : "Renews"} on {fmtDate(open.currentPeriodEnd)}.
              </p>
              {!open.cancelAtPeriodEnd && (open.status === "active" || open.status === "past_due") ? <CancelButton /> : null}
              {open.cancelAtPeriodEnd ? <p className="text-muted">Cancellation scheduled. You will not be charged again.</p> : null}
            </div>
          ) : null}
        </CardBody>
      </Card>

      {!open && provider ? (
        <Card>
          <CardHeader title="Upgrade" description="Prices are illustrative and will be confirmed before launch." />
          <CardBody>
            <ul className="grid gap-3 md:grid-cols-3">
              {PAID_PLANS.filter((p) => p.isActive).map((p) => (
                <li key={p.id} className={`rounded-md border p-4 ${selected?.id === p.id ? "border-accent" : "border-line"}`}>
                  <p className="font-medium">{p.name}</p>
                  <p className="mt-1 text-xl font-semibold">
                    {formatPrice(p.priceCents)}
                    <span className="text-sm font-normal text-muted"> / {p.interval}</span>
                  </p>
                  <p className="mt-1 text-xs text-muted">Renews automatically each {p.interval} until canceled.</p>
                  <div className="mt-3">
                    <CheckoutButton planId={p.id} />
                  </div>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      ) : null}

      {provider?.id === "dev_simulator" && env.APP_ENV !== "production" && subscriptions.some((s) => s.provider === "dev_simulator") ? (
        <Card>
          <CardHeader title="Simulate lifecycle events (development only)" description="Each button sends a signed webhook event through the real processing path." />
          <CardBody>
            <form action={simulateLifecycleEvent} className="flex flex-wrap gap-2">
              {[
                ["renew", "Renewal"],
                ["fail", "Payment failure"],
                ["cancel_now", "Immediate cancellation"],
                ["expire", "Expiration"],
                ["refund", "Refund"],
              ].map(([v, l]) => (
                <Button key={v} name="event" value={v} variant="secondary" size="sm" type="submit">
                  {l}
                </Button>
              ))}
            </form>
          </CardBody>
        </Card>
      ) : null}

      <Card>
        <CardHeader title="Billing history" />
        <CardBody>
          {payments.length === 0 ? (
            <p className="text-sm text-muted">No payments yet.</p>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase text-faint">
                <tr>
                  <th className="py-2">Date</th>
                  <th className="py-2">Description</th>
                  <th className="py-2 text-right">Amount</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {payments.map((p) => (
                  <tr key={p.id}>
                    <td className="py-2">{fmtDate(p.occurred_at)}</td>
                    <td className="py-2">
                      {p.kind === "payment" ? "Payment" : p.kind === "refund" ? "Refund" : "Failed payment"} · {getPaidPlan(p.plan_id)?.name ?? p.plan_id}
                      {!p.livemode ? <span className="ml-2 text-xs text-warning">(simulated)</span> : null}
                    </td>
                    <td className="tabular py-2 text-right">
                      {p.kind === "refund" ? "−" : ""}
                      {formatPrice(p.amount_cents, p.currency)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
