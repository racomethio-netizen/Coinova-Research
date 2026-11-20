"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { Badge, Loading } from "@/components/ui";
import { time } from "@/lib/format";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type AccountView = any;

/** Shows where the numbers come from, or why there are none. Never shows placeholders. */
export function AccountState({ view, error, loading, children }: { view: AccountView | null; error: string | null; loading: boolean; children: (data: AccountView) => ReactNode }) {
  if (!view) return <Loading error={error} loading={loading} />;
  if (!view.connected) {
    return (
      <p role="status" className="rounded-xl border border-line bg-panel p-4 text-sm">
        {view.message} Connect a key on the <Link className="underline" href="/connection/">Binance connection</Link> page.
      </p>
    );
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <span>Source: Binance {view.market}</span>
        <Badge tone={view.environment === "production" ? "warn" : "info"}>{view.environment}</Badge>
        <span>key {view.label} (…{view.fingerprint})</span>
        <span>· fetched {time(view.fetched_at)}{view.cached ? " (cached)" : ""}</span>
      </div>
      {view.ok ? children(view.data) : (
        <p role="alert" className="rounded-xl border border-down/40 bg-down/10 p-4 text-sm text-down">Binance did not return data: {view.error}</p>
      )}
    </div>
  );
}
