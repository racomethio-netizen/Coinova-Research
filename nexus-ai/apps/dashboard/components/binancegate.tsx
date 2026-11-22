"use client";

import { Badge, Card, Table } from "@/components/ui";
import { DASH, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const TONE: Record<string, "good" | "warn" | "bad"> = { closed: "good", half_open: "warn", open: "bad" };
const LABEL: Record<string, string> = { closed: "normal", half_open: "recovering (one test request)", open: "paused" };

function top(counts: Record<string, number>, prefix: string): string {
  const rows = Object.entries(counts).filter(([k]) => k.startsWith(prefix))
    .sort((a, b) => b[1] - a[1]).slice(0, 6).map(([k, v]) => `${k.slice(prefix.length)} ${v}`);
  return rows.length ? rows.join(" · ") : DASH;
}

/** Binance rate-limit gate and live stream, for the System page. */
export function BinanceGateCard() {
  const { data } = useApi<Any>("/system/binance", 15000);
  if (!data) return null;
  const ws = data.websocket;
  return (
    <Card title="Binance connection and request budget">
      <div className="grid gap-3 md:grid-cols-3">
        <div className="rounded-lg bg-elevated p-3 text-sm">
          <p className="mb-1 font-medium">Live stream (WebSocket)</p>
          {ws ? (
            <>
              <Badge tone={ws.connected ? "good" : "bad"}>{ws.connected ? "connected" : "disconnected"}</Badge>
              <p className="mt-1 text-xs text-muted">last message {ws.last_message_at ? time(ws.last_message_at) : DASH} · reconnects {ws.reconnects}{ws.last_error ? ` · last error: ${ws.last_error}` : ""}{ws.next_attempt_at ? ` · next attempt ${time(ws.next_attempt_at)}` : ""}</p>
            </>
          ) : <p className="text-muted">not reported</p>}
        </div>
        {(["spot", "futures"] as const).map((sc) => {
          const s = data.scopes[sc];
          const c = s.last_hour || {};
          return (
            <div key={sc} className="rounded-lg bg-elevated p-3 text-sm">
              <p className="mb-1 font-medium">{sc === "spot" ? "Spot REST" : "Futures REST"}</p>
              <Badge tone={TONE[s.circuit]}>{LABEL[s.circuit]}</Badge>
              {s.paused_until && <p className="mt-1 text-xs text-down">paused until {time(s.paused_until)}</p>}
              <p className="mt-1 text-xs text-muted">last hour: {c.requests ?? 0} requests, weight {c.weight ?? 0} (NEXUS budget {s.own_budget_per_minute}/min; IP limit {s.ip_weight_limit}/min){s.ip_used_weight != null ? ` · IP used now ${s.ip_used_weight}` : ""}</p>
              <p className="text-xs text-muted">status: {top(c, "status:")}</p>
              <p className="text-xs text-muted">endpoints: {top(c, "endpoint:")}</p>
              {s.consecutive_failures > 0 && <p className="text-xs text-warn">consecutive refusals: {s.consecutive_failures}</p>}
            </div>
          );
        })}
      </div>
      <div className="mt-3">
        <Table rows={data.events} empty="No pauses recorded recently." columns={[
          { key: "at", label: "Time", render: (e: Any) => time(e.at) },
          { key: "scope", label: "API" },
          { key: "event", label: "Event", render: (e: Any) => (e.event === "circuit_open" ? "paused" : "resumed") },
          { key: "status", label: "HTTP", render: (e: Any) => e.status ?? DASH },
          { key: "endpoint", label: "Endpoint" },
          { key: "until", label: "Until", render: (e: Any) => (e.until ? time(e.until) : DASH) },
        ]} />
      </div>
    </Card>
  );
}
