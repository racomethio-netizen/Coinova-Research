"use client";

import { useState } from "react";
import { Button, Field, inputClass } from "@/components/ui";
import { useMutation } from "@/lib/client/use-mutation";

export function NewWatchlistForm() {
  const m = useMutation();
  const [name, setName] = useState("");
  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        m.run("/api/watchlists", "POST", { name }, () => setName(""));
      }}
    >
      <div className="min-w-[200px] flex-1 text-left">
        <Field id="wl-name" label="Watchlist name" error={m.error ?? undefined}>
          <input id="wl-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} className={inputClass} />
        </Field>
      </div>
      <Button type="submit" disabled={m.pending || !name.trim()}>
        Create
      </Button>
    </form>
  );
}

export function WatchlistActions({ id, name }: { id: string; name: string }) {
  const m = useMutation();
  return (
    <div className="flex items-center gap-2">
      <Button
        size="sm"
        variant="danger"
        disabled={m.pending}
        onClick={() => {
          if (confirm(`Delete watchlist "${name}"?`)) m.run(`/api/watchlists/${id}`, "DELETE");
        }}
      >
        Delete
      </Button>
      {m.error ? <span className="text-xs text-negative">{m.error}</span> : null}
    </div>
  );
}

export function AddCoinForm({ watchlistId, coins }: { watchlistId: string; coins: { id: string; label: string }[] }) {
  const m = useMutation();
  const [coinId, setCoinId] = useState(coins[0]?.id ?? "");
  if (coins.length === 0) return null;
  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        m.run(`/api/watchlists/${watchlistId}/items`, "POST", { coinId });
      }}
    >
      <div className="min-w-[220px] flex-1">
        <Field id={`add-${watchlistId}`} label="Add a coin" error={m.error ?? undefined}>
          <select id={`add-${watchlistId}`} value={coinId} onChange={(e) => setCoinId(e.target.value)} className={inputClass}>
            {coins.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Button type="submit" variant="secondary" disabled={m.pending}>
        Add
      </Button>
    </form>
  );
}

export function RemoveCoinButton({ watchlistId, coinId }: { watchlistId: string; coinId: string }) {
  const m = useMutation();
  return (
    <Button size="sm" variant="ghost" disabled={m.pending} onClick={() => m.run(`/api/watchlists/${watchlistId}/items/${coinId}`, "DELETE")} aria-label={`Remove ${coinId}`}>
      Remove
    </Button>
  );
}
