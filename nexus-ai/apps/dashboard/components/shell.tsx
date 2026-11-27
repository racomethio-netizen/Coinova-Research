"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { ModeProvider, useMode } from "@/lib/hooks";

// Fifteen sections; each section's first link is its landing page.
export const SECTIONS: [string, [string, string][]][] = [
  ["Overview", [["/", "Overview"], ["/multi-asset/", "All markets"]]],
  ["Live Markets", [["/markets/", "Live markets"], ["/data-quality/", "Data quality"]]],
  ["AI Analysis", [["/intelligence/", "Market analysis"], ["/technical/", "Technical analysis"], ["/candidates/", "Trade candidates"], ["/decisions/", "Decision log"], ["/proposals/", "Trade proposals"], ["/signals/", "Signals"], ["/models/", "AI models"]]],
  ["Strategy Center", [["/strategies/", "Strategies"], ["/carry/", "Funding carry test"], ["/taker-test/", "Taker flow test"], ["/trend-test/", "Trend strategy test"], ["/spot-engine/", "Spot strategy engine"], ["/strategy-lab/", "Strategy lab"], ["/trading-bot/", "Trading bot"], ["/adaptive-grid/", "Adaptive grid"], ["/grid-bot/", "Grid bot"], ["/strategy-test/", "Strategy test"], ["/strategy-modules/", "Strategy modules"]]],
  ["Paper Trading", [["/paper/", "Paper trading"]]],
  ["Live Trading", [["/live/", "Live trading"], ["/spot/", "Spot account"], ["/futures/", "Futures account"]]],
  ["Portfolio", [["/portfolio/", "Portfolio"], ["/positions/", "Open positions"], ["/exchanges/", "Exchanges"]]],
  ["Risk Center", [["/risk/", "Risk center"]]],
  ["X Intelligence", [["/social/", "X sentiment"]]],
  ["News", [["/news/", "News"]]],
  ["Trade History", [["/trades/", "Trades"], ["/orders/", "Orders"]]],
  ["Performance Analytics", [["/performance/", "Performance"]]],
  ["Backtesting", [["/backtests/", "Backtesting"]]],
  ["Settings", [["/settings/", "Settings"], ["/connection/", "Binance connection"], ["/telegram/", "Telegram"], ["/security/", "Security"]]],
  ["System Health", [["/system/", "System health"]]],
];

export const NAV: [string, string][] = SECTIONS.flatMap(([, links]) => links);

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
          <nav aria-label="Main" className="max-h-[calc(100vh-6rem)] overflow-y-auto">
            <ul className="space-y-1">
              {SECTIONS.map(([section, links]) => {
                const isActive = (href: string) => (href === "/" ? path === "/" : !!path?.startsWith(href));
                const sectionActive = links.some(([h]) => isActive(h));
                const [landing] = links[0];
                return (
                  <li key={section}>
                    <Link href={landing} onClick={() => setOpen(false)} aria-current={isActive(landing) ? "page" : undefined}
                      className={`block rounded-lg px-3 py-2 text-sm ${sectionActive ? "bg-elevated font-medium text-text" : "text-muted hover:bg-elevated hover:text-text"}`}>
                      {section}
                    </Link>
                    {sectionActive && links.length > 1 && (
                      <ul className="mt-0.5 space-y-0.5 border-l border-line pl-2 ml-3">
                        {links.slice(1).map(([href, label]) => (
                          <li key={href}>
                            <Link href={href} onClick={() => setOpen(false)} aria-current={isActive(href) ? "page" : undefined}
                              className={`block rounded px-2 py-1 text-xs ${isActive(href) ? "font-medium text-text" : "text-muted hover:text-text"}`}>
                              {label}
                            </Link>
                          </li>
                        ))}
                      </ul>
                    )}
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
