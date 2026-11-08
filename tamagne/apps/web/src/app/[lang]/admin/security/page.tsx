import { notFound } from "next/navigation";
import { AdminPage, ApiErrorCard, Pill, fmtDate } from "@/components/admin/shell";
import { Card } from "@/components/ui/card";
import { hasLocale } from "@/i18n/dictionaries";
import { adminGet } from "@/lib/server-api";

interface Events {
  events: { action: string; label: string; alertAt24h: number; last24h: number; last7d: number; alert: boolean }[];
  recent: { seq: string; occurredAt: string; action: string; actorType: string; actorUserId: string | null; targetType: string | null; targetId: string | null }[];
}

export default async function Security({ params }: PageProps<"/[lang]/admin/security">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  return (
    <AdminPage lang={lang} active="/security" roles={["SUPER_ADMIN", "SECURITY_OFFICER"]} title="Security events">
      {async () => {
        const r = await adminGet<Events>("/admin/security/events");
        if (!r.ok) return <ApiErrorCard message={r.message} />;
        return (
          <>
            <Card><p className="text-sm">Counts come from the tamper-evident audit log. Items above their threshold are marked <strong>Check</strong>; investigate them and note the outcome in your incident log (docs/legal/INCIDENT_RESPONSE.md).</p></Card>
            <Card className="overflow-x-auto p-0 sm:p-0">
              <table className="w-full text-left text-sm" data-testid="security-table">
                <caption className="sr-only">Security event counts</caption>
                <thead className="border-b border-line bg-mist"><tr><th className="p-3">Event</th><th className="p-3 text-right">24 h</th><th className="p-3 text-right">7 days</th><th className="p-3 text-right">Threshold (24 h)</th><th className="p-3">Status</th></tr></thead>
                <tbody className="divide-y divide-line">
                  {r.data.events.map((e) => (
                    <tr key={e.action}>
                      <td className="p-3">{e.label}<span className="block font-mono text-xs text-muted">{e.action}</span></td>
                      <td className="p-3 text-right tabular-nums">{e.last24h}</td>
                      <td className="p-3 text-right tabular-nums">{e.last7d}</td>
                      <td className="p-3 text-right tabular-nums">{e.alertAt24h}</td>
                      <td className="p-3">{e.alert ? <Pill tone="bad">Check</Pill> : <Pill tone="good">OK</Pill>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
            <Card>
              <h2 className="font-semibold">Recent (7 days)</h2>
              <ul className="mt-2 space-y-1 font-mono text-xs">
                {r.data.recent.map((x) => <li key={x.seq}>{fmtDate(x.occurredAt)} · {x.action} · {x.actorType.toLowerCase()} {x.actorUserId?.slice(0, 8) ?? ""} → {x.targetType ?? ""} {x.targetId?.slice(0, 8) ?? ""}</li>)}
              </ul>
            </Card>
          </>
        );
      }}
    </AdminPage>
  );
}
