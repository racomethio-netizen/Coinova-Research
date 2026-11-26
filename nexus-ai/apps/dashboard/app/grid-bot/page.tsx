"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Badge, Button, Card, Field, Loading, PageHeader, Stat, Table } from "@/components/ui";
import { DASH, num, pct, qty, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

function tone(s: string | undefined): "good" | "warn" | "bad" | "muted" {
  if (s === "RUNNING") return "good";
  if (s === "ABOVE_RANGE") return "warn";
  if (s === "STOPPED") return "bad";
  return "muted";
}

export default function GridBotPage() {
  const { data: r, error, loading, reload } = useApi<Any>("/grid-bot", 10_000);
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const st = r?.status;
  const g = st?.grid;
  const m = st?.mark;
  const restart = async () => {
    setMsg(null);
    try { await api.post("/grid-bot/restart", { reason }); setReason(""); setMsg("Restart requested: a new grid is built from the current market within a few seconds."); await reload(); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <>
      <PageHeader title="Grid bot" subtitle="Pionex-style spot grid on BTC/USDT: buys below the price, sells one step higher. PAPER money on the live Binance order book." />
      <Loading error={error} loading={loading && !r} />
      <p className="mb-3 rounded-lg border border-warn/40 bg-warn/10 p-3 text-sm">PAPER: simulated 1,000 USDT. Prices and the order book are real; fills, balances and profit are simulated. No real order is placed.</p>
      {msg && <p role="status" className="mb-3 rounded-lg border border-line bg-elevated p-3 text-sm">{msg}</p>}
      {r && !st && <Card title="Status"><p className="text-sm">The grid bot has not reported yet. It starts when the server runs this version and a fresh order book is available.</p></Card>}
      {st && (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Profit (realised + open)" value={m ? num(m.pnl, 2) + " USDT" : DASH} tone={m && Number(m.pnl) >= 0 ? "up" : "down"} hint={m ? pct(m.pnl_pct, 2) : undefined} />
            <Stat label="Completed round trips" value={g?.round_trips ?? DASH} hint={g ? `grid profit ${num(g.realized, 2)} USDT, fees ${num(g.fees, 2)}` : undefined} />
            <Stat label="Coins held" value={m ? qty(m.held_base) + " BTC" : DASH} hint={m ? `worth ${num(m.held_value, 2)} USDT` : undefined} />
            <Stat label="Worst case (all buys, then stop)" value={st.worst_case_loss ? num(st.worst_case_loss, 2) + " USDT" : DASH} tone="warn" hint="computed before start; must be at most 10% of capital" />
          </div>
          <Card title="Grid" actions={<Badge tone={tone(g?.status)}>{g?.status ?? "not started"}</Badge>}>
            {st.kill_switch_pause && <p className="mb-2 text-sm text-warn">Paused: the kill switch is engaged.</p>}
            {!st.data_ok && <p className="mb-2 text-sm text-warn">Waiting for a fresh order book: no fills while data is stale.</p>}
            {st.refused?.length > 0 && <ul className="mb-2 list-disc pl-5 text-sm text-warn">{st.refused.map((x: string) => <li key={x}>Grid refused: {x}</li>)}</ul>}
            {g && (
              <dl className="grid gap-2 text-sm sm:grid-cols-3">
                <div><dt className="text-muted">Range</dt><dd className="num">{num(g.lower, 2)} – {num(g.upper, 2)}</dd></div>
                <div><dt className="text-muted">Levels / step</dt><dd className="num">{g.levels?.length} / {pct(st.step_pct, 2)}</dd></div>
                <div><dt className="text-muted">Stop-loss</dt><dd className="num">{num(g.stop_price, 2)}</dd></div>
                <div><dt className="text-muted">Bid / ask</dt><dd className="num">{num(st.bid, 2)} / {num(st.ask, 2)}</dd></div>
                <div><dt className="text-muted">Book time</dt><dd>{time(st.book_at)}</dd></div>
                <div><dt className="text-muted">Open orders</dt><dd className="num">{m?.open_buys ?? DASH} buys, {m?.open_sells ?? DASH} sells</dd></div>
              </dl>
            )}
            {g?.stop_reason && <p className="mt-2 text-sm text-down">{g.stop_reason}</p>}
          </Card>
          <Card title="Resting orders (simulated)">
            <Table rows={g?.orders} empty="No resting orders." columns={[
              { key: "side", label: "Side", render: (o: Any) => <Badge tone={o.side === "buy" ? "good" : "bad"}>{o.side}</Badge> },
              { key: "price", label: "Price", numeric: true, render: (o: Any) => num(o.price, 2) },
              { key: "qty", label: "Amount", numeric: true, render: (o: Any) => qty(o.qty) },
            ]} />
          </Card>
          <Card title="Recent fills (simulated)">
            <Table rows={r.history?.filter((h: Any) => h.kind === "fill")} empty="No fills yet: the price has not crossed a grid level." columns={[
              { key: "at", label: "Time", render: (h: Any) => time(h.at) },
              { key: "side", label: "Side", render: (h: Any) => h.fill?.side },
              { key: "price", label: "Price", numeric: true, render: (h: Any) => num(h.fill?.price, 2) },
              { key: "qty", label: "Amount", numeric: true, render: (h: Any) => qty(h.fill?.qty) },
              { key: "profit", label: "Round-trip profit", numeric: true, render: (h: Any) => h.fill?.profit ? num(h.fill.profit, 4) : DASH },
              { key: "reason", label: "Why", render: (h: Any) => h.fill?.reason },
            ]} />
          </Card>
          {g?.status === "STOPPED" && (
            <Card title="Restart after a stop-loss (owner)">
              <p className="mb-2 text-sm">The grid never restarts itself after a stop. Restarting builds a new grid from the current market.</p>
              <div className="grid gap-3 md:grid-cols-3">
                <Field id="grid-reason" label="Reason (at least 5 characters)" value={reason} onChange={setReason} />
                <div className="flex items-end"><Button tone="primary" disabled={reason.trim().length < 5} onClick={restart}>Restart grid</Button></div>
              </div>
            </Card>
          )}
        </div>
      )}
    </>
  );
}
