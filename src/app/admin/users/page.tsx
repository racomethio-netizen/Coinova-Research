import { redirect } from "next/navigation";
import { Badge, Card, CardBody, Notice, PageHeader } from "@/components/ui";
import { requireStaffPage } from "@/lib/auth/session";
import { fmtDate } from "@/lib/format";
import { getServiceClient } from "@/lib/supabase/server";

/**
 * User overview. Emails come from Supabase Auth via the service role, only
 * after the server-side admin check. Roles are read-only here: they are
 * granted through the controlled CLI process (npm run admin:grant).
 */
export default async function AdminUsersPage({ searchParams }: { searchParams: Promise<{ page?: string }> }) {
  const viewer = await requireStaffPage();
  if (!viewer.isAdmin) redirect("/admin/research");
  const page = Math.max(1, Math.min(1000, Number((await searchParams).page) || 1));
  const svc = getServiceClient();
  const users = svc ? await svc.auth.admin.listUsers({ page, perPage: 50 }) : null;
  const ids = users?.data?.users.map((u) => u.id) ?? [];
  const [{ data: subs }, { data: roles }] = await Promise.all([
    viewer.supabase.from("subscriptions").select("user_id, tier, status, livemode, current_period_end").in("user_id", ids.length ? ids : ["00000000-0000-0000-0000-000000000000"]),
    viewer.supabase.from("user_roles").select("user_id, role").in("user_id", ids.length ? ids : ["00000000-0000-0000-0000-000000000000"]),
  ]);
  return (
    <div className="space-y-6">
      <PageHeader title="Users" description="Membership status and roles. Billing details stay with the payment provider." />
      <Notice>Admin roles are granted only via <code>npm run admin:grant -- user@example.com</code> with service-role credentials, and every grant is audited.</Notice>
      {!svc ? <Notice tone="warning">SUPABASE_SERVICE_ROLE_KEY is not configured; user emails cannot be listed.</Notice> : null}
      <Card>
        <CardBody className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="text-left text-xs uppercase text-faint"><tr><th className="py-2">Email</th><th>Joined</th><th>Confirmed</th><th>Membership</th><th>Roles</th></tr></thead>
            <tbody className="divide-y divide-line">
              {(users?.data?.users ?? []).map((u) => {
                const s = (subs ?? []).filter((x) => x.user_id === u.id && ["active", "trialing", "past_due"].includes(x.status));
                const r = (roles ?? []).filter((x) => x.user_id === u.id);
                return (
                  <tr key={u.id}>
                    <td className="py-2">{u.email}</td>
                    <td className="text-xs">{fmtDate(u.created_at)}</td>
                    <td>{u.email_confirmed_at ? "yes" : "no"}</td>
                    <td>{s.length ? s.map((x) => <Badge key={x.tier} tone={x.livemode ? "accent" : "warning"}>{x.tier} · {x.status}{x.livemode ? "" : " (test)"}</Badge>) : <span className="text-faint">free</span>}</td>
                    <td>{r.map((x) => <Badge key={x.role} tone="warning">{x.role}</Badge>)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="mt-3 flex gap-3 text-sm">
            {page > 1 ? <a className="text-accent" href={`/admin/users?page=${page - 1}`}>← Previous</a> : null}
            {(users?.data?.users.length ?? 0) === 50 ? <a className="text-accent" href={`/admin/users?page=${page + 1}`}>Next →</a> : null}
          </div>
        </CardBody>
      </Card>
    </div>
  );
}
