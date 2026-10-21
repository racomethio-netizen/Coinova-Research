import { Badge, Card, CardBody, CardHeader, PageHeader } from "@/components/ui";
import { requireStaffPage } from "@/lib/auth/session";
import { fmtDate, fmtNumber } from "@/lib/format";
import { CoinToggles, DeleteUnlock, UnlockForm } from "../client";

export default async function AdminCoinsPage() {
  const viewer = await requireStaffPage();
  const [{ data: coins }, { data: unlocks }] = await Promise.all([
    viewer.supabase.from("cryptocurrencies").select("id, name, symbol, market_cap_rank, is_active, is_score_sample, is_scored, fundamentals_updated_at, history_updated_at").order("market_cap_rank", { ascending: true, nullsFirst: false }).limit(500),
    viewer.supabase.from("token_unlock_events").select("id, coin_id, unlock_at, amount, allocation, source_type, source_url, confidence, status").order("unlock_at").limit(200),
  ]);
  return (
    <div className="space-y-6">
      <PageHeader title="Coins & token unlocks" description="Coin rows are created by the market refresh job. Unlock events must cite a verifiable https source." />
      <Card>
        <CardHeader title="Record a token unlock" description="Enter only events published by the project or a configured data provider. Never estimate schedules." />
        <CardBody><UnlockForm coins={(coins ?? []).map((c) => ({ id: c.id, name: `${c.name} (${c.symbol})` }))} /></CardBody>
      </Card>
      <Card>
        <CardHeader title="Recorded unlock events" />
        <CardBody className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="text-left text-xs uppercase text-faint"><tr><th className="py-2">Coin</th><th>Date</th><th>Amount</th><th>Allocation</th><th>Source</th><th /></tr></thead>
            <tbody className="divide-y divide-line">
              {(unlocks ?? []).length === 0 ? <tr><td colSpan={6} className="py-3 text-muted">None recorded.</td></tr> : null}
              {(unlocks ?? []).map((u) => (
                <tr key={u.id}>
                  <td className="py-2">{u.coin_id}</td><td>{fmtDate(u.unlock_at)}</td><td className="tabular">{fmtNumber(Number(u.amount), { compact: true })}</td><td>{u.allocation ?? "—"}</td>
                  <td><Badge>{u.source_type}</Badge> {u.source_url ? <a className="text-xs text-accent underline" href={u.source_url} target="_blank" rel="noopener noreferrer nofollow">link</a> : null} <span className="text-xs text-faint">{u.confidence}</span></td>
                  <td className="text-right"><DeleteUnlock id={u.id} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Coverage" />
        <CardBody className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
            <thead className="text-left text-xs uppercase text-faint"><tr><th className="py-2">#</th><th>Coin</th><th>Flags</th><th>Fundamentals</th><th>History</th><th /></tr></thead>
            <tbody className="divide-y divide-line">
              {(coins ?? []).length === 0 ? <tr><td colSpan={6} className="py-3 text-muted">No coins yet. Run the refresh-markets job.</td></tr> : null}
              {(coins ?? []).map((c) => (
                <tr key={c.id}>
                  <td className="py-2 text-faint">{c.market_cap_rank ?? "—"}</td>
                  <td>{c.name} <span className="text-xs text-faint">{c.symbol}</span></td>
                  <td className="space-x-1">{!c.is_active ? <Badge tone="negative">inactive</Badge> : null}{c.is_score_sample ? <Badge tone="accent">sample</Badge> : null}{!c.is_scored ? <Badge>unscored</Badge> : null}</td>
                  <td className="text-xs">{fmtDate(c.fundamentals_updated_at)}</td>
                  <td className="text-xs">{fmtDate(c.history_updated_at)}</td>
                  <td className="text-right"><CoinToggles id={c.id} isActive={c.is_active} isSample={c.is_score_sample} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardBody>
      </Card>
    </div>
  );
}
