import { notFound } from "next/navigation";
import { ActionForm } from "@/components/admin/action-form";
import { AdminPage, ApiErrorCard, Pill, fmtDate } from "@/components/admin/shell";
import { Card } from "@/components/ui/card";
import { hasLocale } from "@/i18n/dictionaries";
import { adminGet } from "@/lib/server-api";

interface Grant { id: string; userId: string; role: string; phone: string | null; mfaEnrolled: boolean; grantedAt: string }
const ROLES = ["SUPER_ADMIN", "VERIFICATION_OFFICER", "SUPPORT_OFFICER", "SECURITY_OFFICER", "FINANCE_OFFICER"];

export default async function Staff({ params }: PageProps<"/[lang]/admin/staff">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  return (
    <AdminPage lang={lang} active="/staff" roles={["SUPER_ADMIN"]} title="Staff">
      {async (viewer) => {
        const r = await adminGet<Grant[]>("/admin/staff");
        if (!r.ok) return <ApiErrorCard message={r.message} />;
        return (
          <>
            <Card>
              <h2 className="font-semibold">Grant a role</h2>
              <p className="mt-1 text-sm text-muted">The person must already have a TAMAGNE account. They will set up two-factor authentication at their next sign-in.</p>
              <div className="mt-3 max-w-md">
                <ActionForm path="/admin/staff" submitLabel="Grant role" successText="Role granted." fields={[{ name: "phone", label: "Their phone number", type: "text", required: true }, { name: "role", label: "Role", type: "select", options: ROLES.map((x) => ({ value: x, label: x.replace(/_/g, " ").toLowerCase() })) }]} />
              </div>
            </Card>
            <Card className="p-0 sm:p-0">
              <ul className="divide-y divide-line text-sm" data-testid="staff-list">
                {r.data.map((g) => (
                  <li key={g.id} className="flex flex-wrap items-center justify-between gap-2 p-3">
                    <span><strong>{g.role.replace(/_/g, " ").toLowerCase()}</strong> · {g.phone} · since {fmtDate(g.grantedAt)} {g.mfaEnrolled ? <Pill tone="good">MFA</Pill> : <Pill tone="warn">no MFA yet</Pill>}</span>
                    {g.userId !== viewer.userId ? <ActionForm path={`/admin/staff/${g.id}/revoke`} submitLabel="Revoke" variant="ghost" confirm="Revoke this role?" /> : <span className="text-muted">you</span>}
                  </li>
                ))}
              </ul>
            </Card>
          </>
        );
      }}
    </AdminPage>
  );
}
