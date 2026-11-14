"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { ModeProvider, useMode } from "@/lib/hooks";

export const NAV: [string, string][] = [
  ["/", "Overview"], ["/portfolio/", "Portfolio"], ["/exchanges/", "Exchanges"],
  ["/markets/", "Live markets"], ["/signals/", "AI signals"], ["/positions/", "Open positions"],
  ["/orders/", "Order history"], ["/trades/", "Trade history"], ["/performance/", "Performance"],
  ["/strategies/", "Strategies"], ["/models/", "AI models"], ["/risk/", "Risk management"],
  ["/system/", "System"], ["/security/", "Security"],
];

function ModeBanner() {
  const { mode } = useMode();
  if (mode === "paper") {
    return (
      <div role="status" className="border-b border-warn/40 bg-warn/10 px-4 py-2 text-center text-xs font-medium text-warn">
        PAPER TRADING: simulated fills on real market prices. Paper results are not real profits.
      </div>
    );
  }
  return (
    <div role="status" className="border-b border-down/40 bg-down/10 px-4 py-2 text-center text-xs font-semibold text-down">
      LIVE VIEW: real exchange accounts. Live trading only runs after every owner approval gate passes.
    </div>
  );
}

function ModeSwitch() {
  const { mode, setMode } = useMode();
  return (
    <div role="group" aria-label="Trading mode" className="flex rounded-lg border border-line p-0.5 text-xs">
      {(["paper", "live"] as const).map((m) => (
        <button key={m} type="button" aria-pressed={mode === m} onClick={() => setMode(m)}
          className={`rounded-md px-3 py-1 font-medium uppercase ${mode === m ? (m === "live" ? "bg-down text-white" : "bg-warn text-black") : "text-muted"}`}>
          {m}
        </button>
      ))}
    </div>
  );
}

function Frame({ children }: { children: ReactNode }) {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  if (path?.startsWith("/login")) return <>{children}</>;
  const logout = async () => {
    try { await api.post("/auth/logout"); } finally { window.location.href = "/login/"; }
  };
  return (
    <div className="min-h-screen">
      <ModeBanner />
      <div className="flex">
        <aside className={`${open ? "block" : "hidden"} fixed inset-y-0 left-0 z-20 w-60 shrink-0 border-r border-line bg-panel p-4 md:static md:block md:min-h-screen`}>
          <div className="mb-6 flex items-center gap-2">
            <div className="grid h-8 w-8 place-items-center rounded-lg bg-accent font-bold text-white">N</div>
            <div>
              <div className="text-sm font-semibold">NEXUS AI</div>
              <div className="text-xs text-muted">Spot trading system</div>
            </div>
          </div>
          <nav aria-label="Main">
            <ul className="space-y-0.5">
              {NAV.map(([href, label]) => {
                const active = href === "/" ? path === "/" : path?.startsWith(href);
                return (
                  <li key={href}>
                    <Link href={href} onClick={() => setOpen(false)} aria-current={active ? "page" : undefined}
                      className={`block rounded-lg px-3 py-2 text-sm ${active ? "bg-elevated font-medium text-text" : "text-muted hover:bg-elevated hover:text-text"}`}>
                      {label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
        </aside>
        <div className="min-w-0 flex-1">
          <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
            <button type="button" className="rounded-lg border border-line px-3 py-1 text-sm md:hidden" aria-expanded={open} onClick={() => setOpen(!open)}>Menu</button>
            <ModeSwitch />
            <button type="button" onClick={logout} className="text-sm text-muted hover:text-text">Sign out</button>
          </header>
          <main className="mx-auto max-w-7xl p-4 md:p-6">{children}</main>
        </div>
      </div>
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  return <ModeProvider><Frame>{children}</Frame></ModeProvider>;
}
