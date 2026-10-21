"use client";

import Link from "next/link";
import { useState } from "react";
import { buttonClass } from "@/components/ui";

interface Watchlist {
  id: string;
  name: string;
  coinIds: string[];
}

export function WatchButton({ coinId, signedIn }: { coinId: string; signedIn: boolean }) {
  const [state, setState] = useState<"idle" | "loading" | "picking" | "done" | "error">("idle");
  const [lists, setLists] = useState<Watchlist[]>([]);
  const [message, setMessage] = useState<string | null>(null);

  if (!signedIn) {
    return (
      <Link href={`/login?next=/coins/${coinId}`} className={buttonClass("secondary", "sm")}>
        Sign in to watch
      </Link>
    );
  }

  async function open() {
    setState("loading");
    setMessage(null);
    const res = await fetch("/api/watchlists");
    const json = await res.json().catch(() => null);
    if (!json?.ok) {
      setState("error");
      setMessage(json?.error?.message ?? "Could not load watchlists.");
      return;
    }
    setLists(json.data.watchlists);
    setState("picking");
  }

  async function add(listId: string | null) {
    setState("loading");
    const res = listId
      ? await fetch(`/api/watchlists/${listId}/items`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ coinId }) })
      : await fetch("/api/watchlists", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: "My watchlist", coinIds: [coinId] }) });
    const json = await res.json().catch(() => null);
    if (!json?.ok) {
      setState("error");
      setMessage(json?.error?.message ?? "Could not add to watchlist.");
      return;
    }
    setState("done");
    setMessage("Added to watchlist.");
  }

  return (
    <div className="flex flex-col items-end gap-2">
      {state === "picking" ? (
        <div className="flex flex-wrap justify-end gap-2" role="group" aria-label="Choose a watchlist">
          {lists.length === 0 ? (
            <button type="button" className={buttonClass("primary", "sm")} onClick={() => add(null)}>
              Create watchlist and add
            </button>
          ) : (
            lists.map((l) => (
              <button key={l.id} type="button" className={buttonClass("secondary", "sm")} onClick={() => add(l.id)} disabled={l.coinIds.includes(coinId)}>
                {l.coinIds.includes(coinId) ? `In ${l.name}` : `Add to ${l.name}`}
              </button>
            ))
          )}
        </div>
      ) : (
        <button type="button" className={buttonClass("secondary", "sm")} onClick={open} disabled={state === "loading"}>
          {state === "loading" ? "Working…" : "☆ Add to watchlist"}
        </button>
      )}
      {message ? (
        <p role="status" className={state === "error" ? "text-xs text-negative" : "text-xs text-positive"}>
          {message}
        </p>
      ) : null}
    </div>
  );
}
