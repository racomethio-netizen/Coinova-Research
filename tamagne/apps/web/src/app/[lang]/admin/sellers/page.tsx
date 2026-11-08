import Link from "next/link";
import { notFound } from "next/navigation";
import { AdminPage, ApiErrorCard, Pill, fmtDate } from "@/components/admin/shell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { hasLocale } from "@/i18n/dictionaries";
import { adminGet } from "@/lib/server-api";

interface Row { tamagneId: string; displayName: string; displayNameAm: string | null; accountStatus: string; sellerType: string; createdAt: string }

export default async function Sellers({ params, searchParams }: PageProps<"/[lang]/admin/sellers">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  const sp = await searchParams;
  const q = typeof sp.q === "string" ? sp.q.slice(0, 120) : "";
  return (
    <AdminPage lang={lang} active="/sellers" roles={["SUPER_ADMIN", "VERIFICATION_OFFICER", "SUPPORT_OFFICER", "SECURITY_OFFICER"]} title="Sellers">
      {async () => {
        const r = q ? await adminGet<Row[]>(`/admin/sellers?q=${encodeURIComponent(q)}`) : null;
        return (
          <>
            <Card>
              <form method="get" role="search" className="flex flex-col gap-3 sm:flex-row sm:items-end">
                <div className="flex-1">
                  <Label htmlFor="q">Tamagne ID, phone number or name</Label>
                  <Input id="q" name="q" defaultValue={q} autoComplete="off" />
                </div>
                <Button type="submit">Search</Button>
              </form>
            </Card>
            {r && !r.ok ? <ApiErrorCard message={r.message} /> : null}
            {r && r.ok ? (
              r.data.length === 0 ? <Card><p className="text-sm text-muted">No sellers found.</p></Card> : (
                <Card className="p-0 sm:p-0">
                  <ul className="divide-y divide-line" data-testid="seller-results">
                    {r.data.map((s) => (
                      <li key={s.tamagneId} className="flex flex-wrap items-center justify-between gap-2 p-3 text-sm">
                        <span><strong>{s.displayName}</strong> <span className="font-mono">{s.tamagneId}</span> · {s.sellerType.replace(/_/g, " ").toLowerCase()} · joined {fmtDate(s.createdAt)}</span>
                        <span className="flex items-center gap-3"><Pill tone={s.accountStatus === "ACTIVE" ? "good" : "bad"}>{s.accountStatus.toLowerCase()}</Pill><Link className="font-semibold text-emerald-700" href={`/${lang}/admin/sellers/${s.tamagneId}`}>Open</Link></span>
                      </li>
                    ))}
                  </ul>
                </Card>
              )
            ) : null}
          </>
        );
      }}
    </AdminPage>
  );
}
