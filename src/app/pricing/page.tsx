import type { Metadata } from "next";
import { Badge, ButtonLink, Card, CardBody, Container, PageHeader } from "@/components/ui";
import { FEATURE_MATRIX, formatPrice, PAID_PLANS } from "@/config/plans";
import { getViewer } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Pricing", description: "Membership plans, features, and subscription terms." };
export const dynamic = "force-dynamic";

function Cell({ v }: { v: string | boolean }) {
  if (v === true) return <span className="text-positive">✓<span className="sr-only">Included</span></span>;
  if (v === false) return <span className="text-faint">—<span className="sr-only">Not included</span></span>;
  return <span>{v}</span>;
}

const FAQ = [
  {
    q: "How does renewal work?",
    a: "Subscriptions renew automatically at the end of each billing period (monthly or yearly) at the price shown at checkout, unless you cancel before the renewal date.",
  },
  {
    q: "How do I cancel?",
    a: "You can cancel any time from Dashboard → Membership. Cancellation takes effect at the end of the current paid period; you keep access until then and will not be charged again.",
  },
  {
    q: "What happens if a payment fails?",
    a: "Your subscription is marked past due. Access continues for a short grace period while the payment is retried. If payment is not completed, access returns to the Free plan.",
  },
  {
    q: "Do you offer refunds?",
    a: "Refund terms will be published before paid plans launch. A refunded subscription ends Premium access immediately.",
  },
  {
    q: "Is this investment advice?",
    a: "No. Our research and scores are informational and educational. They are not recommendations to buy or sell any asset, and past performance does not predict future results.",
  },
];

export default async function PricingPage() {
  const viewer = await getViewer();
  const cta = (planId: string) => (viewer.user ? `/dashboard/membership?plan=${planId}` : `/register?next=${encodeURIComponent(`/dashboard/membership?plan=${planId}`)}`);

  return (
    <Container className="py-10">
      <PageHeader title="Membership plans" description="Start free. Upgrade for full research, portfolios, and alerts. Prices in USD." />
      <p className="mb-6 text-xs text-faint">Prices shown are illustrative and subject to change before launch. Applicable taxes may be added at checkout.</p>

      <div className="grid gap-4 lg:grid-cols-4">
        <Card>
          <CardBody className="space-y-4">
            <h2 className="font-semibold">Free</h2>
            <p className="text-3xl font-semibold">$0</p>
            <p className="text-sm text-muted">Public market pages, sample research scores, free articles, and a basic watchlist.</p>
            <ButtonLink href={viewer.user ? "/dashboard" : "/register"} variant="secondary" className="w-full">
              {viewer.user ? "Your dashboard" : "Create account"}
            </ButtonLink>
          </CardBody>
        </Card>
        {PAID_PLANS.filter((p) => p.isActive).map((p) => (
          <Card key={p.id} className={p.id === "premium_yearly" ? "border-accent/60" : undefined}>
            <CardBody className="space-y-4">
              <div className="flex items-center justify-between gap-2">
                <h2 className="font-semibold">{p.name}</h2>
                {p.id === "premium_yearly" ? <Badge tone="accent">Best value</Badge> : null}
              </div>
              <p className="text-3xl font-semibold">
                {formatPrice(p.priceCents)}
                <span className="text-sm font-normal text-muted"> / {p.interval}</span>
              </p>
              <p className="text-sm text-muted">{p.description}</p>
              <ButtonLink href={cta(p.id)} className="w-full">
                Choose plan
              </ButtonLink>
            </CardBody>
          </Card>
        ))}
      </div>

      <section className="mt-12" aria-labelledby="compare">
        <h2 id="compare" className="mb-4 text-xl font-semibold">
          Compare features
        </h2>
        <div className="overflow-x-auto rounded-lg border border-line">
          <table className="w-full min-w-[600px] text-sm">
            <thead className="bg-subtle text-left text-xs uppercase text-faint">
              <tr>
                <th scope="col" className="px-4 py-2.5">Feature</th>
                <th scope="col" className="px-4 py-2.5">Free</th>
                <th scope="col" className="px-4 py-2.5">Premium</th>
                <th scope="col" className="px-4 py-2.5">Founding</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {FEATURE_MATRIX.map((f) => (
                <tr key={f.feature}>
                  <th scope="row" className="px-4 py-2.5 text-left font-normal">{f.feature}</th>
                  <td className="px-4 py-2.5"><Cell v={f.free} /></td>
                  <td className="px-4 py-2.5"><Cell v={f.premium} /></td>
                  <td className="px-4 py-2.5"><Cell v={f.founding} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="mt-12 max-w-3xl" aria-labelledby="faq">
        <h2 id="faq" className="mb-4 text-xl font-semibold">
          Subscription FAQ
        </h2>
        <div className="divide-y divide-line rounded-lg border border-line">
          {FAQ.map((f) => (
            <details key={f.q} className="group px-5 py-4">
              <summary className="cursor-pointer list-none font-medium marker:hidden">
                <span className="mr-2 inline-block text-faint transition-transform group-open:rotate-90">›</span>
                {f.q}
              </summary>
              <p className="mt-2 text-sm text-muted">{f.a}</p>
            </details>
          ))}
        </div>
      </section>
    </Container>
  );
}
