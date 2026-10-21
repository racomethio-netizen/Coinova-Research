import { notFound } from "next/navigation";
import { Button, ButtonLink, Card, CardBody, Notice, PageHeader } from "@/components/ui";
import { formatPrice, getPaidPlan } from "@/config/plans";
import { requireUserPage } from "@/lib/auth/session";
import { getDevSimulator } from "@/server/billing";
import { simulateCheckoutPayment } from "../actions";

function isOpen(s: { status: string; expires_at: string }) {
  return s.status === "open" && Date.parse(s.expires_at) > Date.now();
}

/** DEVELOPMENT-ONLY simulated checkout page. 404 unless the simulator is enabled. */
export default async function DevCheckoutPage({ searchParams }: { searchParams: Promise<{ session?: string }> }) {
  if (!getDevSimulator()) notFound();
  const viewer = await requireUserPage("/dashboard/membership");
  const { session } = await searchParams;
  if (!session || !/^devcs_[0-9a-f-]{36}$/.test(session)) notFound();
  const { data } = await viewer.supabase
    .from("checkout_sessions")
    .select("plan_id, status, expires_at")
    .eq("provider", "dev_simulator")
    .eq("provider_session_id", session)
    .eq("user_id", viewer.user.id)
    .maybeSingle();
  if (!data) notFound();
  const plan = getPaidPlan(data.plan_id);
  const usable = isOpen(data);

  return (
    <div className="max-w-xl space-y-6">
      <PageHeader title="Simulated checkout" eyebrow="Development only" />
      <Notice tone="warning" title="This is not a real payment page">
        No card details are collected and no money moves. Clicking “Simulate successful payment” sends a signed test webhook to this server.
      </Notice>
      <Card>
        <CardBody className="space-y-4">
          <p>
            <span className="font-medium">{plan?.name ?? data.plan_id}</span> — {plan ? `${formatPrice(plan.priceCents)} / ${plan.interval}` : ""}
          </p>
          {usable ? (
            <form action={simulateCheckoutPayment} className="flex gap-3">
              <input type="hidden" name="session" value={session} />
              <Button type="submit">Simulate successful payment</Button>
              <ButtonLink href="/pricing?checkout=canceled" variant="secondary">
                Abandon checkout
              </ButtonLink>
            </form>
          ) : (
            <p className="text-sm text-muted">This checkout session is no longer open.</p>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
