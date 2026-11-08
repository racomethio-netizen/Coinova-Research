import { notFound } from "next/navigation";
import { ActionForm } from "@/components/admin/action-form";
import { AdminPage, ApiErrorCard, Pill, fmtDate } from "@/components/admin/shell";
import { Card } from "@/components/ui/card";
import { hasLocale } from "@/i18n/dictionaries";
import { adminGet } from "@/lib/server-api";

interface Plan { id: string; code: string; kind: string; nameEn: string; nameAm: string; isActive: boolean; currentPrices: { amount: string; currency: string; interval: string }[]; priceHistory: { id: string; amount: string; currency: string; interval: string; validFrom: string; validTo: string | null }[] }
const KINDS = ["FREE_INDIVIDUAL", "PREMIUM_SELLER", "BUSINESS_VERIFICATION", "MARKETPLACE_API", "ENTERPRISE"];

export default async function Pricing({ params }: PageProps<"/[lang]/admin/pricing">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  return (
    <AdminPage lang={lang} active="/pricing" roles={["SUPER_ADMIN", "FINANCE_OFFICER"]} title="Pricing">
      {async () => {
        const r = await adminGet<Plan[]>("/admin/plans");
        if (!r.ok) return <ApiErrorCard message={r.message} />;
        return (
          <>
            <Card><p className="text-sm">Prices are set here, never in code. Changing a price closes the old one (kept in history). Plans and payments never grant, extend or restore verification.</p></Card>
            {r.data.map((p) => (
              <Card key={p.id} data-testid="plan">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="font-semibold">{p.nameEn} <span className="text-muted" lang="am">/ {p.nameAm}</span> <span className="font-mono text-xs">{p.code}</span></h2>
                  <Pill tone={p.isActive ? "good" : "neutral"}>{p.isActive ? "active" : "inactive"}</Pill>
                </div>
                <p className="mt-2 text-sm">Current: {p.currentPrices.length ? p.currentPrices.map((x) => `${x.amount} ${x.currency} / ${x.interval.toLowerCase()}`).join(" · ") : "no price set"}</p>
                <details className="mt-2 text-sm">
                  <summary className="cursor-pointer">Price history ({p.priceHistory.length})</summary>
                  <ul className="mt-1 space-y-1">{p.priceHistory.map((h) => <li key={h.id}>{h.amount} {h.currency} / {h.interval.toLowerCase()} · {fmtDate(h.validFrom)} → {h.validTo ? fmtDate(h.validTo) : "now"}</li>)}</ul>
                </details>
                <div className="mt-3 grid gap-4 md:grid-cols-2">
                  <ActionForm path={`/admin/plans/${p.id}/prices`} submitLabel="Set price" fields={[
                    { name: "amount", label: "Amount (e.g. 199.00)", type: "text", required: true },
                    { name: "currency", label: "Currency", type: "text", defaultValue: "ETB" },
                    { name: "interval", label: "Interval", type: "select", options: [{ value: "MONTH", label: "per month" }, { value: "YEAR", label: "per year" }, { value: "ONE_TIME", label: "one time" }] },
                  ]} />
                  <div><ActionForm method="PATCH" path={`/admin/plans/${p.id}`} submitLabel={p.isActive ? "Deactivate plan" : "Activate plan"} variant="outline" extra={{ isActive: !p.isActive }} /></div>
                </div>
              </Card>
            ))}
            <Card>
              <h2 className="font-semibold">New plan</h2>
              <div className="mt-3 max-w-lg">
                <ActionForm path="/admin/plans" submitLabel="Create plan" fields={[
                  { name: "code", label: "Code (lowercase, e.g. premium_seller)", type: "text", required: true },
                  { name: "kind", label: "Kind", type: "select", options: KINDS.map((k) => ({ value: k, label: k.replace(/_/g, " ").toLowerCase() })) },
                  { name: "nameEn", label: "Name (English)", type: "text", required: true },
                  { name: "nameAm", label: "Name (Amharic)", type: "text", required: true },
                ]} />
              </div>
            </Card>
          </>
        );
      }}
    </AdminPage>
  );
}
