"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Badge, Button, Card, Field, Loading, PageHeader, Table } from "@/components/ui";
import { DASH, num, pct, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const MEANING: Record<string, string> = {
  OFF: "No new signals and no new orders. Open positions stay protected.",
  RESEARCH: "Data and analysis; signals are recorded with their risk verdict. No orders.",
  PAPER: "Signals that pass every check become paper orders. No real money.",
  LIVE: "Signals that pass every check become REAL orders with real money.",
};
const TONE: Record<string, "bad" | "info" | "muted"> = { LIVE: "bad", PAPER: "info", RESEARCH: "muted", OFF: "muted" };

function Research({ m }: { m: Any }) {
  const r = m.research;
  if (!r || r.autonomy === "OFF" || !r.instruments) return <p className="mt-3 text-sm text-muted">{m.autonomy === "OFF" ? "Switched OFF: no data is collected." : "Waiting for the first research run (every 6 hours)."}</p>;
  return (
    <div className="mt-3">
      <p className="mb-1 text-sm font-medium">Research view {m.research_at ? `(updated ${time(m.research_at)})` : ""}: descriptive, not a prediction</p>
      <Table rows={r.instruments} empty="No data." columns={[
        { key: "name", label: "Instrument" },
        { key: "last", label: "Last", numeric: true, render: (x: Any) => num(x.last, x.last < 10 ? 4 : 2) },
        { key: "as_of", label: "As of" },
        { key: "o", label: "Market now", render: (x: Any) => x.open_now === undefined ? DASH : x.open_now ? "open" : `closed${x.next_open ? `, opens ${time(x.next_open)}` : ""}` },
        { key: "r1", label: "1 month", numeric: true, render: (x: Any) => pct(x.return_1m, 1) },
        { key: "r12", label: "12 months", numeric: true, render: (x: Any) => pct(x.return_12m, 1) },
        { key: "t", label: "Trend (200 days)", render: (x: Any) => x.above_200d === null || x.above_200d === undefined ? DASH : x.above_200d ? "above average" : "below average" },
        { key: "v", label: "Volatility (30 d)", numeric: true, render: (x: Any) => pct(x.vol_30d, 0) },
        { key: "c", label: "Carry for a long / rule weight", numeric: true, render: (x: Any) => x.carry !== undefined ? `${pct(x.carry, 2)} (rates ${x.rate_as_of?.slice(0, 7)})` : x.voltarget_weight !== undefined && x.voltarget_weight !== null ? `${pct(x.voltarget_weight, 0)} held` : DASH },
      ]} />
      {r.errors?.length > 0 && <p className="mt-1 text-xs text-down">Not available: {r.errors.join("; ")}</p>}
    </div>
  );
}

function Forward({ f }: { f: Any }) {
  if (!f) return null;
  return (
    <div className="mt-3 rounded-lg border border-line p-3 text-sm">
      <p className="font-medium">Paper forward test: {f.strategy} (no real money)</p>
      {f.days ? (
        <p className="mt-1">Started {f.started_at} with {num(f.initial, 0)} virtual USD: now {num(f.equity, 2)} ({pct(f.return_since_start, 2)}), holding {pct(f.weight, 0)} in the index. Holding the index fully would be {num(f.hold_equity, 2)} ({pct(f.hold_return, 2)}). Review on {f.review_at}.</p>
      ) : <p className="mt-1 text-muted">{f.message}; it starts {f.started_at}.</p>}
    </div>
  );
}

function MarketCard({ m, reload }: { m: Any; reload: () => Promise<void> }) {
  const [mode, setMode] = useState("");
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState("");
  const [totp, setTotp] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const live = mode === "LIVE";
  const id = m.market;
  const submit = async () => {
    setMsg(null);
    try {
      await api.post(`/markets/${id}/autonomy`, { autonomy: mode, reason, confirm: confirm || null, totp: totp || null });
      setMode(""); setReason(""); setConfirm(""); setTotp(""); await reload();
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <Card title={m.name} actions={<Badge tone={TONE[m.autonomy]}>{m.autonomy}</Badge>}>
      <p className="text-sm">{MEANING[m.autonomy]}</p>
      <p className="mt-1 text-xs text-muted">
        {m.set_by ? `Set by ${m.set_by} at ${time(m.at)}: ${m.reason}` : `Default (${m.default}), never changed.`} Effective with the operating mode: {m.effective_mode} (paper orders: {m.paper_orders ? "yes" : "no"}, live orders: {m.live_orders ? "yes" : "no"}).
      </p>
      {m.paper_positions && <p className="mt-1 text-sm">Paper positions: {m.paper_positions.open} open, {m.paper_positions.closed} closed. Details on the Paper trading and Open positions pages.</p>}
      <div className="mt-3 space-y-2">
        {m.venues.map((v: Any) => (
          <div key={v.code} className="rounded-lg border border-line p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{v.name}</span>
              <Badge tone={v.connector === "built" ? "good" : "muted"}>{v.role === "data" ? (v.connector === "built" ? "data source" : "data: not built") : v.connector === "built" ? "trading connector built" : "broker: not connected"}</Badge>
            </div>
            <p className="mt-1 text-xs text-muted">{v.instruments.join("; ")}. Access: {v.access}.</p>
            {v.notes && <p className="mt-1 text-xs text-muted">{v.notes}</p>}
            {v.owner_needs.length > 0 && <p className="mt-1 text-xs">Needed from you: {v.owner_needs.join("; ")}.</p>}
          </div>
        ))}
      </div>
      {m.notes.length > 0 && <ul className="mt-2 list-disc pl-5 text-xs text-muted">{m.notes.map((n: string) => <li key={n}>{n}</li>)}</ul>}
      {m.market !== "crypto" && <Research m={m} />}
      {m.paper_forward && m.autonomy === "PAPER" && <Forward f={m.paper_forward} />}
      {m.allowed.length > 1 ? (
        <div className="mt-3 grid gap-3 md:grid-cols-4">
          <div>
            <label htmlFor={`mode-${id}`} className="mb-1 block text-sm">New setting</label>
            <select id={`mode-${id}`} value={mode} onChange={(e) => setMode(e.target.value)} className="w-full rounded-lg border border-line bg-elevated px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent">
              <option value="">Choose…</option>
              {m.allowed.map((a: string) => <option key={a} value={a}>{a}</option>)}
            </select>
          </div>
          <Field id={`reason-${id}`} label="Reason for the change" value={reason} onChange={setReason} />
          {live && <Field id={`confirm-${id}`} label={`Type: ${m.live_confirm_phrase}`} value={confirm} onChange={setConfirm} autoComplete="off" />}
          {live && <Field id={`totp-${id}`} label="Two-factor code" value={totp} onChange={setTotp} inputMode="numeric" />}
          <div className="flex items-end"><Button tone={live ? "danger" : "default"} disabled={!mode || reason.trim().length < 5} onClick={submit}>Change</Button></div>
        </div>
      ) : (
        <p className="mt-3 text-sm">Stays OFF until NEXUS has a data source for this market.</p>
      )}
      {msg && <p role="alert" className="mt-2 text-sm text-down">{msg}</p>}
      {m.live_blockers?.length > 0 && (
        <div className="mt-3 border-t border-line pt-3 text-sm">
          <p className="mb-1 font-medium">LIVE is locked until all of these are fixed:</p>
          <ul className="list-disc pl-5 text-muted">{m.live_blockers.map((b: string) => <li key={b}>{b}</li>)}</ul>
        </div>
      )}
    </Card>
  );
}

function ResearchHistory() {
  const { data } = useApi<Any>("/research/history", 60000);
  const series: Any[] = data?.series ?? [];
  return (
    <div className="mt-4 border-t border-line pt-3 text-sm">
      <p className="mb-1 font-medium">Price history for forex research (NQS-1)</p>
      <p className="mb-2 text-muted">The NQS-1 forex strategy is tested on your broker&apos;s own price history. To send it, run the bridge once with <code>--export-history</code> while the forex market is open (about 10 to 30 minutes). It sends M5 bars for 3 years, H1 for 12 years and all daily bars for EURUSD, GBPUSD, USDJPY, AUDUSD and USDCHF.</p>
      <Table rows={series} empty="No history received yet." columns={[
        { key: "symbol", label: "Pair" },
        { key: "timeframe", label: "Timeframe" },
        { key: "bars", label: "Bars", numeric: true, render: (x: Any) => num(x.bars, 0) },
        { key: "from", label: "From", render: (x: Any) => time(x.first_open) },
        { key: "to", label: "To", render: (x: Any) => time(x.last_open) },
        { key: "size", label: "Stored", numeric: true, render: (x: Any) => `${num(x.bytes / 1e6, 1)} MB` },
      ]} />
    </div>
  );
}

function ExnessBridge({ ex, reload }: { ex: Any; reload: () => Promise<void> }) {
  const { data: tokens, reload: reloadTokens } = useApi<Any>("/bridge-tokens", 30000);
  const [name, setName] = useState("");
  const [reason, setReason] = useState("");
  const [created, setCreated] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const create = async () => {
    setMsg(null); setCreated(null);
    try {
      const r = await api.post<Any>("/bridge-tokens", { name, reason });
      setCreated(r.token); setName(""); setReason(""); await reloadTokens();
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };
  const revoke = async (id: number) => {
    setMsg(null);
    try { await api.post(`/bridge-tokens/${id}/revoke`); await reloadTokens(); await reload(); } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };
  const state = !ex ? "never connected" : ex.connected ? `connected (${ex.trade_mode} account, read-only)` : `not connected (last seen ${time(ex.at)})`;
  return (
    <Card title="Exness (MetaTrader 5) bridge" actions={<Badge tone={ex?.connected ? (ex.trade_mode === "real" ? "bad" : "good") : "muted"}>{state}</Badge>}>
      <p className="text-sm">A small read-only program on your Windows PC or VPS sends prices, contract details and your account summary from your logged-in MT5 terminal to NEXUS. It cannot place orders, and your MT5 password never leaves the terminal. Setup: apps/mt5_bridge/README.md in the repository.</p>
      {ex && (
        <div className="mt-3 space-y-2 text-sm">
          <p>Account {ex.account} on {ex.server} ({ex.company}): balance {num(ex.balance, 2)} {ex.currency}, equity {num(ex.equity, 2)}, free margin {num(ex.margin_free, 2)}. Account leverage 1:{ex.leverage}; NEXUS never uses more than 1x of equity.</p>
          {ex.trade_mode === "real" && <p role="alert" className="text-down">This is a REAL-money account. The bridge only reads it; NEXUS places no orders on Exness.</p>}
          {ex.positions?.length > 0 && <p>Open positions (yours, not NEXUS's): {ex.positions.map((p: Any) => `${p.symbol} ${p.side} ${p.volume} lot (P/L ${p.profit})`).join("; ")}</p>}
          <Table rows={ex.symbols ?? []} empty="No symbols reported." columns={[
            { key: "name", label: "Symbol" },
            { key: "class", label: "Market" },
            { key: "bid", label: "Bid", numeric: true, render: (x: Any) => x.bid ?? DASH },
            { key: "ask", label: "Ask", numeric: true, render: (x: Any) => x.ask ?? DASH },
            { key: "sp", label: "Spread (points)", numeric: true, render: (x: Any) => x.spread_points ?? DASH },
            { key: "cs", label: "Contract size", numeric: true, render: (x: Any) => num(x.contract_size, 0) },
            { key: "vm", label: "Min lot", numeric: true, render: (x: Any) => x.volume_min },
            { key: "sw", label: "Swap long / short", numeric: true, render: (x: Any) => `${x.swap_long ?? DASH} / ${x.swap_short ?? DASH}` },
          ]} />
          {ex.unresolved?.length > 0 && <p className="text-xs text-muted">Not offered on this account: {ex.unresolved.join(", ")}</p>}
        </div>
      )}
      <ResearchHistory />
      <div className="mt-4 border-t border-line pt-3">
        <p className="mb-2 text-sm font-medium">Bridge tokens</p>
        {(tokens?.bridges ?? []).length > 0 && (
          <ul className="mb-3 space-y-1 text-sm">
            {tokens.bridges.map((b: Any) => (
              <li key={b.id} className="flex flex-wrap items-center gap-2">
                <span>{b.name}</span>
                <span className="text-xs text-muted">created {time(b.created_at)}, last seen {b.last_seen_at ? time(b.last_seen_at) : "never"}</span>
                {b.revoked ? <Badge tone="muted">revoked</Badge> : <Button tone="danger" onClick={() => revoke(b.id)}>Revoke</Button>}
              </li>
            ))}
          </ul>
        )}
        <div className="grid gap-3 md:grid-cols-3">
          <Field id="bridge-name" label="Name (for example: home PC)" value={name} onChange={setName} />
          <Field id="bridge-reason" label="Reason" value={reason} onChange={setReason} />
          <div className="flex items-end"><Button disabled={name.trim().length < 2 || reason.trim().length < 5} onClick={create}>Create bridge token</Button></div>
        </div>
        {created && (
          <div role="status" className="mt-3 rounded-lg border border-warn p-3 text-sm">
            <p className="font-medium">Copy this token now. It is shown only once:</p>
            <code className="mt-1 block break-all">{created}</code>
            <p className="mt-1 text-xs text-muted">Put it in NEXUS_BRIDGE_TOKEN on your Windows machine. Never paste it in chat or email. Revoke it here if it is ever exposed.</p>
          </div>
        )}
        {msg && <p role="alert" className="mt-2 text-sm text-down">{msg}</p>}
      </div>
    </Card>
  );
}

export default function MultiAssetPage() {
  const { data, error, loading, reload } = useApi<Any>("/markets", 15000);
  return (
    <>
      <PageHeader title="All markets" subtitle="Crypto, stocks, commodities and forex: one switch per market, each with its own live authorization" />
      <Loading error={error} loading={loading && !data} />
      {data && (
        <div className="space-y-4">
          <p className="rounded-lg border border-line bg-elevated p-3 text-sm">
            Global operating mode: <strong>{data.operating_mode}</strong>; server trading mode: <strong>{data.server_trading_mode}</strong>. A market switch can only narrow the operating mode, never widen it. Live trading is never switched on by code: each market needs its own typed phrase, your two-factor code and its live checks.
          </p>
          <ExnessBridge ex={data.exness} reload={reload} />
          {data.markets.map((m: Any) => <MarketCard key={m.market} m={m} reload={reload} />)}
        </div>
      )}
    </>
  );
}
