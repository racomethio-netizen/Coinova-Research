"use client";

import { useId, useState } from "react";
import { Button, Field, inputClass, Notice } from "@/components/ui";
import { useMutation } from "@/lib/client/use-mutation";

export function OpenAccount({ reset = false }: { reset?: boolean }) {
  const m = useMutation();
  const [balance, setBalance] = useState("10000");
  const id = useId();
  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (reset && !window.confirm("Archive this virtual account and start a new one? History is kept.")) return;
        void m.run("/api/paper/account", "POST", { action: reset ? "reset" : "open", startingBalance: balance });
      }}
    >
      <Field id={id} label="Virtual starting balance (USDT)" error={m.fields.startingBalance}>
        <input id={id} value={balance} onChange={(e) => setBalance(e.target.value)} inputMode="decimal" className={inputClass} aria-invalid={m.fields.startingBalance ? true : undefined} />
      </Field>
      <Button type="submit" variant={reset ? "secondary" : "primary"} disabled={m.pending}>{reset ? "Reset account" : "Open virtual account"}</Button>
      {m.error ? <p className="w-full text-sm text-negative" role="alert">{m.error}</p> : null}
    </form>
  );
}

export function OrderForm({ pairs }: { pairs: { id: number; symbol: string }[] }) {
  const m = useMutation();
  const uid = useId();
  const [v, setV] = useState({ pairId: pairs[0]?.id ?? 0, side: "buy", type: "market", quantity: "", limitPrice: "", stopLoss: "", takeProfit: "" });
  const set = (k: keyof typeof v) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setV({ ...v, [k]: k === "pairId" ? Number(e.target.value) : e.target.value });
  const opt = (x: string) => (x.trim() ? x.trim() : undefined);
  const err = (k: string) => m.fields[k];
  return (
    <form
      className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
      onSubmit={(e) => {
        e.preventDefault();
        void m.run("/api/paper/orders", "POST", {
          pairId: v.pairId, side: v.side, type: v.type, quantity: v.quantity.trim(),
          limitPrice: v.type === "limit" ? opt(v.limitPrice) : undefined,
          stopLoss: v.side === "buy" ? opt(v.stopLoss) : undefined, takeProfit: v.side === "buy" ? opt(v.takeProfit) : undefined,
        });
      }}
    >
      {m.error ? <div className="sm:col-span-2 lg:col-span-4"><Notice tone="negative">{m.error}</Notice></div> : null}
      <Field id={`${uid}-pair`} label="Pair"><select id={`${uid}-pair`} value={v.pairId} onChange={set("pairId")} className={inputClass}>{pairs.map((p) => <option key={p.id} value={p.id}>{p.symbol}</option>)}</select></Field>
      <Field id={`${uid}-side`} label="Side"><select id={`${uid}-side`} value={v.side} onChange={set("side")} className={inputClass}><option value="buy">Buy</option><option value="sell">Sell (close long)</option></select></Field>
      <Field id={`${uid}-type`} label="Type"><select id={`${uid}-type`} value={v.type} onChange={set("type")} className={inputClass}><option value="market">Market</option><option value="limit">Limit</option></select></Field>
      <Field id={`${uid}-qty`} label="Quantity" error={err("quantity")}><input id={`${uid}-qty`} value={v.quantity} onChange={set("quantity")} inputMode="decimal" required className={inputClass} aria-invalid={err("quantity") ? true : undefined} /></Field>
      {v.type === "limit" ? <Field id={`${uid}-limit`} label="Limit price" error={err("limitPrice")}><input id={`${uid}-limit`} value={v.limitPrice} onChange={set("limitPrice")} inputMode="decimal" className={inputClass} aria-invalid={err("limitPrice") ? true : undefined} /></Field> : null}
      {v.side === "buy" ? (
        <>
          <Field id={`${uid}-sl`} label="Stop loss (optional)" error={err("stopLoss")}><input id={`${uid}-sl`} value={v.stopLoss} onChange={set("stopLoss")} inputMode="decimal" className={inputClass} aria-invalid={err("stopLoss") ? true : undefined} /></Field>
          <Field id={`${uid}-tp`} label="Take profit (optional)" error={err("takeProfit")}><input id={`${uid}-tp`} value={v.takeProfit} onChange={set("takeProfit")} inputMode="decimal" className={inputClass} aria-invalid={err("takeProfit") ? true : undefined} /></Field>
        </>
      ) : null}
      <div className="flex items-end"><Button type="submit" disabled={m.pending || !pairs.length}>{m.pending ? "Placing…" : "Place virtual order"}</Button></div>
    </form>
  );
}

export function CancelOrder({ id }: { id: string }) {
  const m = useMutation();
  return <Button size="sm" variant="ghost" disabled={m.pending} onClick={() => m.run(`/api/paper/orders/${id}`, "DELETE")}>Cancel</Button>;
}

export function Protection({ pairId, stopLoss, takeProfit }: { pairId: number; stopLoss: string | null; takeProfit: string | null }) {
  const m = useMutation();
  const uid = useId();
  const [sl, setSl] = useState(stopLoss ?? "");
  const [tp, setTp] = useState(takeProfit ?? "");
  return (
    <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); void m.run(`/api/paper/positions/${pairId}`, "PUT", { stopLoss: sl.trim() || null, takeProfit: tp.trim() || null }); }}>
      <label className="text-xs" htmlFor={`${uid}-sl`}>Stop <input id={`${uid}-sl`} value={sl} onChange={(e) => setSl(e.target.value)} inputMode="decimal" className={`${inputClass} w-28`} /></label>
      <label className="text-xs" htmlFor={`${uid}-tp`}>Target <input id={`${uid}-tp`} value={tp} onChange={(e) => setTp(e.target.value)} inputMode="decimal" className={`${inputClass} w-28`} /></label>
      <Button size="sm" variant="secondary" type="submit" disabled={m.pending}>Save</Button>
      {m.error ? <span className="text-xs text-negative" role="alert">{m.error}</span> : null}
    </form>
  );
}
