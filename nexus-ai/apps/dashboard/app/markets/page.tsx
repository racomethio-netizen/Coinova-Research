"use client";

import { useState } from "react";
import { CandleChart, type CandleRow } from "@/components/charts";
import { Badge, Card, Loading, PageHeader, Table } from "@/components/ui";
import { DASH, num, qty, time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

const SEL = "rounded-lg border border-line bg-elevated px-3 py-2 text-sm";

export default function Markets() {
  const [exchange, setExchange] = useState("binance");
  const [symbol, setSymbol] = useState("BTC/USDT");
  const [tf, setTf] = useState("1h");
  const candles = useApi<{ candles: CandleRow[] }>(`/markets/candles?exchange=${exchange}&symbol=${encodeURIComponent(symbol)}&timeframe=${tf}&limit=300`, 60000);
  const books = useApi<Any[]>("/markets/books", 3000);
  return (
    <>
      <PageHeader title="Live markets" subtitle="Closed candles from the database; top of book from WebSocket streams">
        <div className="flex flex-wrap gap-2">
          <label className="sr-only" htmlFor="ex">Exchange</label>
          <select id="ex" className={SEL} value={exchange} onChange={(e) => setExchange(e.target.value)}>
            {["binance", "kraken", "coinbase"].map((x) => <option key={x}>{x}</option>)}
          </select>
          <label className="sr-only" htmlFor="sym">Pair</label>
          <select id="sym" className={SEL} value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {["BTC/USDT", "ETH/USDT"].map((x) => <option key={x}>{x}</option>)}
          </select>
          <label className="sr-only" htmlFor="tf">Timeframe</label>
          <select id="tf" className={SEL} value={tf} onChange={(e) => setTf(e.target.value)}>
            {["5m", "15m", "1h", "4h"].map((x) => <option key={x}>{x}</option>)}
          </select>
        </div>
      </PageHeader>
      <div className="space-y-4">
        <Card title={`${exchange} · ${symbol} · ${tf}`}>
          <Loading error={candles.error} loading={candles.loading && !candles.data} />
          {candles.data && (candles.data.candles.length ? <CandleChart candles={candles.data.candles} label={`${symbol} ${tf} candles on ${exchange}`} /> : <p className="py-6 text-center text-sm text-muted">No candles stored for this market yet.</p>)}
        </Card>
        <Card title="Top of book (live)">
          <Table rows={books.data} empty="No live books. Is the market-data service running?" columns={[
            { key: "exchange", label: "Exchange" }, { key: "symbol", label: "Pair" },
            { key: "bid", label: "Bid", numeric: true, render: (b: Any) => num(b.book?.bid) },
            { key: "ask", label: "Ask", numeric: true, render: (b: Any) => num(b.book?.ask) },
            { key: "spread", label: "Spread (bps)", numeric: true, render: (b: Any) => b.book ? num(String((Number(b.book.ask) - Number(b.book.bid)) / ((Number(b.book.ask) + Number(b.book.bid)) / 2) * 1e4), 2) : DASH },
            { key: "size", label: "Bid / ask size", numeric: true, render: (b: Any) => b.book ? `${qty(b.book.bid_size)} / ${qty(b.book.ask_size)}` : DASH },
            { key: "ts", label: "Updated", render: (b: Any) => b.book ? time(b.book.ts) : <Badge tone="bad">stale</Badge> },
          ]} />
        </Card>
      </div>
    </>
  );
}
