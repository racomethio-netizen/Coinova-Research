import { notFound } from "next/navigation";
import { ActionForm } from "@/components/admin/action-form";
import { AdminPage, ApiErrorCard, Pill, can, fmtDate } from "@/components/admin/shell";
import { Card } from "@/components/ui/card";
import { hasLocale } from "@/i18n/dictionaries";
import { adminGet } from "@/lib/server-api";

interface Detail {
  tamagneId: string; displayName: string; displayNameAm: string | null; sellerType: string; accountStatus: string; suspensionReason: string | null; suspendedAt: string | null; isPublic: boolean; city: string | null; createdAt: string;
  user: { phone: string | null; status: string; lastLoginAt: string | null };
  verification: Record<string, { status: string; expiresAt: string | null; testMode?: boolean } | string>;
  badges: { id: string; level: string; method: string; status: string; issuedAt: string | null; expiresAt: string | null; revocationReason: string | null; environment: string | null }[];
  business: { legalName: string; status: string } | null;
  reports: { id: string; category: string; status: string; createdAt: string }[];
  appeals: { id: string; subjectType: string; status: string; createdAt: string }[];
  events: { level: string; type: string; actorType: string; reason: string | null; createdAt: string }[];
  reviews: { id: string; targetType: string; decision: string; reason: string; status: string; createdAt: string }[];
}

export default async function SellerDetail({ params }: PageProps<"/[lang]/admin/sellers/[tamagneId]">) {
  const { lang, tamagneId } = await params;
  if (!hasLocale(lang)) notFound();
  return (
    <AdminPage lang={lang} active="/sellers" roles={["SUPER_ADMIN", "VERIFICATION_OFFICER", "SUPPORT_OFFICER", "SECURITY_OFFICER"]} title="Seller">
      {async (viewer) => {
        const r = await adminGet<Detail>(`/admin/sellers/${encodeURIComponent(tamagneId)}`);
        if (!r.ok) return <ApiErrorCard message={r.message} />;
        const s = r.data;
        const live = s.badges.filter((b) => b.status === "ACTIVE" || b.status === "SUSPENDED");
        return (
          <>
            <Card>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-lg font-semibold">{s.displayName} <span className="font-mono text-base">{s.tamagneId}</span></h2>
                <Pill tone={s.accountStatus === "ACTIVE" ? "good" : "bad"}>{s.accountStatus.toLowerCase()}</Pill>
              </div>
              <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-3">
                <div><dt className="text-muted">Phone (masked)</dt><dd>{s.user.phone ?? "—"}</dd></div>
                <div><dt className="text-muted">Public profile</dt><dd>{s.isPublic ? "yes" : "no"}</dd></div>
                <div><dt className="text-muted">Last sign-in</dt><dd>{fmtDate(s.user.lastLoginAt)}</dd></div>
                <div><dt className="text-muted">Business</dt><dd>{s.business ? `${s.business.legalName} (${s.business.status.toLowerCase()})` : "—"}</dd></div>
                <div><dt className="text-muted">Joined</dt><dd>{fmtDate(s.createdAt)}</dd></div>
              </dl>
              {s.suspensionReason ? <p className="mt-3 text-sm text-red-800">Suspended {fmtDate(s.suspendedAt)}: {s.suspensionReason}</p> : null}
            </Card>

            <Card>
              <h2 className="font-semibold">Badges</h2>
              <ul className="mt-2 divide-y divide-line">
                {s.badges.map((b) => (
                  <li key={b.id} className="py-2 text-sm" data-testid="admin-badge">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span><strong>{b.level.replace(/_/g, " ").toLowerCase()}</strong> · {b.method.replace(/_/g, " ").toLowerCase()} {b.environment === "SANDBOX" ? <Pill tone="warn">sandbox</Pill> : null} · <Pill tone={b.status === "ACTIVE" ? "good" : "neutral"}>{b.status.toLowerCase()}</Pill> · expires {fmtDate(b.expiresAt)}</span>
                    </div>
                    {b.revocationReason ? <p className="text-muted">Revoked: {b.revocationReason}</p> : null}
                    {live.includes(b) && can(viewer, ["SUPER_ADMIN", "VERIFICATION_OFFICER", "SECURITY_OFFICER"]) ? (
                      <details className="mt-2">
                        <summary className="cursor-pointer text-sm font-semibold text-red-800">Revoke this badge</summary>
                        <div className="mt-2"><ActionForm path={`/admin/badges/${b.id}/revoke`} submitLabel="Revoke badge" variant="outline" confirm="Revoke this badge? This cannot be undone; the seller must re-verify." fields={[{ name: "reason", label: "Reason (recorded and shown to staff)", type: "textarea", required: true }]} /></div>
                      </details>
                    ) : null}
                  </li>
                ))}
              </ul>
            </Card>

            <div className="grid gap-6 lg:grid-cols-2">
              {s.accountStatus !== "SUSPENDED" && can(viewer, ["SUPER_ADMIN", "SUPPORT_OFFICER", "SECURITY_OFFICER"]) ? (
                <Card>
                  <h2 className="font-semibold">Suspend account</h2>
                  <p className="mt-1 text-sm text-muted">Takes effect immediately. Buyers see &ldquo;Suspended&rdquo; and no current verification. The reason is not shown publicly.</p>
                  <div className="mt-3"><ActionForm path={`/admin/sellers/${s.tamagneId}/suspend`} submitLabel="Suspend" variant="outline" confirm="Suspend this seller now?" testId="suspend-form" fields={[{ name: "reason", label: "Reason", type: "textarea", required: true }]} /></div>
                </Card>
              ) : null}
              {s.accountStatus === "SUSPENDED" && can(viewer, ["SUPER_ADMIN", "SECURITY_OFFICER"]) ? (
                <Card>
                  <h2 className="font-semibold">Request reactivation</h2>
                  <p className="mt-1 text-sm text-muted">High-risk override: a different reviewer must confirm it under Second reviews.</p>
                  <div className="mt-3"><ActionForm path={`/admin/sellers/${s.tamagneId}/reactivate`} submitLabel="Request reactivation" testId="reactivate-form" fields={[{ name: "reason", label: "Reason", type: "textarea", required: true }]} /></div>
                </Card>
              ) : null}
            </div>

            <div className="grid gap-6 lg:grid-cols-2">
              <Card>
                <h2 className="font-semibold">Fraud reports ({s.reports.length})</h2>
                <ul className="mt-2 space-y-1 text-sm">{s.reports.map((x) => <li key={x.id}>{x.category.toLowerCase()} · {x.status.toLowerCase()} · {fmtDate(x.createdAt)}</li>)}</ul>
              </Card>
              <Card>
                <h2 className="font-semibold">Appeals ({s.appeals.length})</h2>
                <ul className="mt-2 space-y-1 text-sm">{s.appeals.map((x) => <li key={x.id}>{x.subjectType.toLowerCase()} · {x.status.toLowerCase()} · {fmtDate(x.createdAt)}</li>)}</ul>
              </Card>
            </div>
            <Card>
              <h2 className="font-semibold">History</h2>
              <ul className="mt-2 space-y-1 text-sm">
                {s.reviews.map((x) => <li key={x.id}><Pill>{x.targetType.toLowerCase()}</Pill> {x.decision.toLowerCase()} {x.status === "PENDING_SECOND_REVIEW" ? <Pill tone="warn">awaiting second reviewer</Pill> : null} — {x.reason} <span className="text-muted">({fmtDate(x.createdAt)})</span></li>)}
                {s.events.map((e, i) => <li key={i} className="text-muted">{e.level.toLowerCase()} {e.type.toLowerCase()} by {e.actorType.toLowerCase()}{e.reason ? `: ${e.reason}` : ""} ({fmtDate(e.createdAt)})</li>)}
              </ul>
            </Card>
          </>
        );
      }}
    </AdminPage>
  );
}
