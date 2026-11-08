import Link from "next/link";
import { notFound } from "next/navigation";
import { AdminPage, ApiErrorCard, Pill, fmtDate } from "@/components/admin/shell";
import { Button } from "@/components/ui/button";
import { Alert, Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { hasLocale } from "@/i18n/dictionaries";
import { adminGet } from "@/lib/server-api";

interface Page { entries: { seq: string; occurredAt: string; actorType: string; actorUserId: string | null; action: string; targetType: string | null; targetId: string | null; metadata: unknown; hash: string | null }[]; nextBefore: string | null }
interface Verify { intact: boolean; firstBrokenSeq: string | null; entries: string; checkedAt: string }

export default async function Audit({ params, searchParams }: PageProps<"/[lang]/admin/audit">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  const sp = await searchParams;
  const pick = (k: string) => (typeof sp[k] === "string" ? (sp[k] as string).slice(0, 100) : "");
  const filters = { action: pick("action"), targetType: pick("targetType"), targetId: pick("targetId"), actorUserId: pick("actorUserId"), before: pick("before") };
  const qs = new URLSearchParams(Object.entries(filters).filter(([, v]) => v)).toString();
  return (
    <AdminPage lang={lang} active="/audit" roles={["SUPER_ADMIN", "SECURITY_OFFICER"]} title="Audit log">
      {async () => {
        const [r, v] = await Promise.all([adminGet<Page>(`/admin/audit${qs ? `?${qs}` : ""}`), sp.verify ? adminGet<Verify>("/admin/audit/verify") : Promise.resolve(null)]);
        return (
          <>
            {v ? (
              v.ok ? (
                <Alert tone={v.data.intact ? "success" : "error"} data-testid="chain-result">
                  {v.data.intact ? `Hash chain intact across ${v.data.entries} entries (checked ${fmtDate(v.data.checkedAt)}).` : `Hash chain BROKEN at entry ${v.data.firstBrokenSeq}. Escalate to security immediately.`}
                </Alert>
              ) : <ApiErrorCard message={v.message} />
            ) : null}
            <Card>
              <form method="get" className="grid gap-3 sm:grid-cols-4">
                <div><Label htmlFor="a">Action starts with</Label><Input id="a" name="action" defaultValue={filters.action} placeholder="auth." /></div>
                <div><Label htmlFor="tt">Target type</Label><Input id="tt" name="targetType" defaultValue={filters.targetType} /></div>
                <div><Label htmlFor="ti">Target ID</Label><Input id="ti" name="targetId" defaultValue={filters.targetId} /></div>
                <div><Label htmlFor="au">Actor user ID</Label><Input id="au" name="actorUserId" defaultValue={filters.actorUserId} /></div>
                <div className="flex gap-2 sm:col-span-4">
                  <Button type="submit" size="sm">Filter</Button>
                  <Button asChild variant="outline" size="sm"><Link href={`/${lang}/admin/audit?verify=1`}>Verify hash chain</Link></Button>
                </div>
              </form>
            </Card>
            {!r.ok ? <ApiErrorCard message={r.message} /> : (
              <Card className="overflow-x-auto p-0 sm:p-0">
                <table className="w-full text-left text-xs" data-testid="audit-table">
                  <thead className="border-b border-line bg-mist"><tr><th className="p-2">#</th><th className="p-2">Time</th><th className="p-2">Actor</th><th className="p-2">Action</th><th className="p-2">Target</th><th className="p-2">Details</th></tr></thead>
                  <tbody className="divide-y divide-line">
                    {r.data.entries.map((e) => (
                      <tr key={e.seq}>
                        <td className="p-2 font-mono">{e.seq}</td>
                        <td className="p-2 whitespace-nowrap">{fmtDate(e.occurredAt)}</td>
                        <td className="p-2"><Pill>{e.actorType.toLowerCase()}</Pill> <span className="font-mono">{e.actorUserId?.slice(0, 8) ?? ""}</span></td>
                        <td className="p-2 font-mono">{e.action}</td>
                        <td className="p-2">{e.targetType} <span className="font-mono">{e.targetId?.slice(0, 8) ?? ""}</span></td>
                        <td className="p-2 font-mono">{JSON.stringify(e.metadata)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {r.data.nextBefore ? (
                  <div className="p-3"><Link className="font-semibold text-emerald-700" href={`/${lang}/admin/audit?${new URLSearchParams({ ...Object.fromEntries(Object.entries(filters).filter(([, x]) => x)), before: r.data.nextBefore })}`}>Older entries</Link></div>
                ) : null}
              </Card>
            )}
          </>
        );
      }}
    </AdminPage>
  );
}
