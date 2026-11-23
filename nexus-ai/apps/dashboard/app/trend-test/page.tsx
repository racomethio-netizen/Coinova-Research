"use client";

import { Badge, Card, Loading, PageHeader, Stat, Table } from "@/components/ui";
import { DASH, num, pct, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const COINS = ["BTCUSDT", "ETHUSDT"];

export default function TrendTest() {
  const { data: r, error, loading } = useApi<Any>("/research/trend-forward", 60_000);
  const latest = r?.latest;
  return (
    <>
      <PageHeader title="Trend strategy test" subtitle="Forward PAPER test of an ensemble trend-following strategy on BTC and ETH (after Zarattini, Pagani & Barbon, 2025). It passed every pre-registered test on 2018-2026 Binance data; this checks it on new days. Virtual money only: nothing is executed." />
      <Loading error={error} loading={loading && !r} />
      {r && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Paper balance" value={r.equity == null ? DASH : `${num(r.equity)} USDT`} hint={`started with ${num(r.initial)} virtual USDT`} />
            <Stat label="Return since start" value={r.return_since_start == null ? DASH : pct(r.return_since_start, 2)} tone={r.return_since_start > 0 ? "up" : r.return_since_start < 0 ? "down" : "flat"} hint="after fees and slippage" />
            <Stat label="Largest drop" value={r.max_drawdown == null ? DASH : pct(r.max_drawdown, 1)} hint="limit 15%" />
            <Stat label="Days recorded" value={`${r.days} of 180`} hint={`review on ${time(r.review_at).slice(0, 10)}`} />
          </div>
          <Card title="Position now (share of each coin's half of the money)">
            {latest ? (
              <ul className="space-y-1 text-sm">
                {COINS.map((c) => (
                  <li key={c} className="flex flex-wrap justify-between gap-2">
                    <span className="font-medium">{c.replace("USDT", "")}</span>
                    <span className="num">{pct(latest.weights?.[c] ?? 0, 0)} invested · target {pct(latest.targets?.[c] ?? 0, 0)} · close {num(latest.closes?.[c])}</span>
                  </li>
                ))}
              </ul>
            ) : <p className="text-sm text-muted">{r.status?.ok === false ? `Last run failed: ${r.status.error}` : "Waiting for the first closed day after the start (24 Nov 2026)."}</p>}
            <p className="mt-2 text-xs text-muted">The rest stays in cash. The strategy buys only when prices break above their recent range, steps aside on a trailing stop, and sizes each position for about 25% yearly price swing.</p>
          </Card>
          <Card title="Checks at the 180-day review">
            {r.checks ? (
              <ul className="space-y-1 text-sm">
                <li className="flex justify-between"><span>Recorded days match a fresh replay</span><Badge tone={r.checks.integrity ? "good" : "bad"}>{r.checks.integrity ? "yes" : `${r.divergences} divergence(s)`}</Badge></li>
                <li className="flex justify-between"><span>Return at least -6.6%</span><Badge tone={r.checks.return_ok ? "good" : "bad"}>{r.checks.return_ok ? "on track" : "below"}</Badge></li>
                <li className="flex justify-between"><span>Largest drop at most 15%</span><Badge tone={r.checks.drawdown_ok ? "good" : "bad"}>{r.checks.drawdown_ok ? "on track" : "above"}</Badge></li>
                <li className="flex justify-between"><span>No data gap over 3 days</span><Badge tone={r.checks.gaps_ok ? "good" : "bad"}>{r.checks.gaps_ok ? "ok" : `${r.longest_gap_days} days`}</Badge></li>
              </ul>
            ) : <p className="text-sm text-muted">Shown once the first day is recorded.</p>}
            <p className="mt-2 text-xs text-muted">Decision: {r.decision ?? "pending"}. Backtest 2018-2026 (BTC+ETH, after costs): about 14% a year, Sharpe 1.11, largest drop 16% (holding the coins: 31% a year but a 76% drop). Past results do not guarantee future returns. Rules: {r.preregistration}.</p>
          </Card>
          <Card title="Recent paper trades">
            <Table rows={[...(r.trades ?? [])].reverse()} empty="No paper trades yet." columns={[
              { key: "date", label: "Day" },
              { key: "symbol", label: "Coin", render: (t: Any) => t.symbol.replace("USDT", "") },
              { key: "side", label: "Side", render: (t: Any) => <Badge tone={t.side === "buy" ? "good" : "muted"}>{t.side}</Badge> },
              { key: "to_weight", label: "Position", render: (t: Any) => `${pct(t.from_weight, 0)} → ${pct(t.to_weight, 0)}` },
              { key: "reason", label: "Why" },
              { key: "notional", label: "Amount", numeric: true, render: (t: Any) => `${num(t.notional)} USDT` },
              { key: "cost", label: "Fees", numeric: true, render: (t: Any) => num(t.cost) },
            ]} />
          </Card>
        </div>
      )}
    </>
  );
}
