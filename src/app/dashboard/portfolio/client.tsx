"use client";

import { useState } from "react";
import { Button, Field, inputClass } from "@/components/ui";
import { useMutation } from "@/lib/client/use-mutation";

type TxType = "buy" | "sell" | "transfer_in" | "transfer_out";

export function NewPortfolioForm() {
  const m = useMutation();
  const [name, setName] = useState("");
  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        m.run("/api/portfolio", "POST", { name }, () => setName(""));
      }}
    >
      <div className="min-w-[200px] flex-1 text-left">
        <Field id="portfolio-name" label="Portfolio name" error={m.fields.name ?? m.error ?? undefined}>
          <input id="portfolio-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} required className={inputClass} />
        </Field>
      </div>
      <Button type="submit" disabled={m.pending || !name.trim()}>
        Create portfolio
      </Button>
    </form>
  );
}

export function PortfolioActions({ id, name }: { id: string; name: string }) {
  const m = useMutation();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(name);
  if (editing) {
    return (
      <form
        className="flex items-center gap-2"
        onSubmit={async (e) => {
          e.preventDefault();
          if (await m.run(`/api/portfolio/${id}`, "PATCH", { name: value })) setEditing(false);
        }}
      >
        <label htmlFor={`rename-${id}`} className="sr-only">
          New name
        </label>
        <input id={`rename-${id}`} value={value} onChange={(e) => setValue(e.target.value)} maxLength={60} className={inputClass + " h-8 w-40"} />
        <Button size="sm" type="submit" disabled={m.pending}>
          Save
        </Button>
        <Button size="sm" variant="ghost" type="button" onClick={() => setEditing(false)}>
          Cancel
        </Button>
        {m.error ? <span className="text-xs text-negative">{m.error}</span> : null}
      </form>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
        Rename
      </Button>
      <Button
        size="sm"
        variant="danger"
        disabled={m.pending}
        onClick={() => {
          if (confirm(`Delete portfolio "${name}" and all of its transactions? This cannot be undone.`)) m.run(`/api/portfolio/${id}`, "DELETE");
        }}
      >
        Delete
      </Button>
      {m.error ? <span className="text-xs text-negative">{m.error}</span> : null}
    </div>
  );
}

function localNow() {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

export function TransactionForm({ portfolioId, coins }: { portfolioId: string; coins: { id: string; label: string }[] }) {
  const m = useMutation();
  const [type, setType] = useState<TxType>("buy");
  const [form, setForm] = useState({ coinId: coins[0]?.id ?? "", quantity: "", priceUsd: "", feeUsd: "0", occurredAt: localNow(), notes: "" });
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [k]: e.target.value });
  const priceLabel = type === "transfer_in" ? "Cost basis per unit (USD)" : type === "transfer_out" ? "Price per unit (optional)" : "Price per unit (USD)";

  return (
    <form
      className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        m.run(
          "/api/portfolio/transactions",
          "POST",
          {
            portfolioId,
            coinId: form.coinId,
            type,
            quantity: form.quantity,
            priceUsd: form.priceUsd === "" ? null : form.priceUsd,
            feeUsd: form.feeUsd || "0",
            occurredAt: new Date(form.occurredAt).toISOString(),
            notes: form.notes || null,
          },
          () => setForm({ ...form, quantity: "", priceUsd: "", notes: "" }),
        );
      }}
    >
      <Field id={`type-${portfolioId}`} label="Type">
        <select id={`type-${portfolioId}`} value={type} onChange={(e) => setType(e.target.value as TxType)} className={inputClass}>
          <option value="buy">Buy</option>
          <option value="sell">Sell</option>
          <option value="transfer_in">Transfer in</option>
          <option value="transfer_out">Transfer out</option>
        </select>
      </Field>
      <Field id={`coin-${portfolioId}`} label="Asset" error={m.fields.coinId}>
        <select id={`coin-${portfolioId}`} value={form.coinId} onChange={set("coinId")} className={inputClass}>
          {coins.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}
            </option>
          ))}
        </select>
      </Field>
      <Field id={`qty-${portfolioId}`} label="Quantity" error={m.fields.quantity}>
        <input id={`qty-${portfolioId}`} inputMode="decimal" value={form.quantity} onChange={set("quantity")} className={inputClass} aria-invalid={Boolean(m.fields.quantity) || undefined} />
      </Field>
      <Field id={`price-${portfolioId}`} label={priceLabel} error={m.fields.priceUsd} hint={type === "transfer_in" ? "Use 0 for airdrops or rewards with no cost." : undefined}>
        <input id={`price-${portfolioId}`} inputMode="decimal" value={form.priceUsd} onChange={set("priceUsd")} className={inputClass} aria-invalid={Boolean(m.fields.priceUsd) || undefined} />
      </Field>
      <Field id={`fee-${portfolioId}`} label="Fee (USD)" error={m.fields.feeUsd}>
        <input id={`fee-${portfolioId}`} inputMode="decimal" value={form.feeUsd} onChange={set("feeUsd")} className={inputClass} />
      </Field>
      <Field id={`date-${portfolioId}`} label="Date and time" error={m.fields.occurredAt}>
        <input id={`date-${portfolioId}`} type="datetime-local" value={form.occurredAt} max={localNow()} onChange={set("occurredAt")} className={inputClass} />
      </Field>
      <div className="sm:col-span-2 lg:col-span-3">
        <Field id={`notes-${portfolioId}`} label="Notes (optional)" error={m.fields.notes}>
          <input id={`notes-${portfolioId}`} maxLength={500} value={form.notes} onChange={set("notes")} className={inputClass} />
        </Field>
      </div>
      <div className="flex items-center gap-3 sm:col-span-2 lg:col-span-3">
        <Button type="submit" disabled={m.pending || !form.quantity}>
          Save transaction
        </Button>
        {m.error ? (
          <p role="alert" className="text-sm text-negative">
            {m.error}
          </p>
        ) : null}
      </div>
    </form>
  );
}

interface TxView {
  id: string;
  type: TxType;
  quantity: string;
  priceUsd: string | null;
  feeUsd: string;
  occurredAt: string;
  notes: string | null;
}

export function TransactionRowActions({ tx, canEdit }: { tx: TxView; canEdit: boolean }) {
  const m = useMutation();
  const [editing, setEditing] = useState(false);
  const [q, setQ] = useState(tx.quantity);
  const [p, setP] = useState(tx.priceUsd ?? "");
  const [f, setF] = useState(tx.feeUsd);

  if (editing) {
    return (
      <form
        className="flex flex-wrap items-center justify-end gap-1"
        onSubmit={async (e) => {
          e.preventDefault();
          if (await m.run(`/api/portfolio/transactions/${tx.id}`, "PATCH", { quantity: q, priceUsd: p === "" ? null : p, feeUsd: f })) setEditing(false);
        }}
      >
        <input aria-label="Quantity" value={q} onChange={(e) => setQ(e.target.value)} className={inputClass + " h-8 w-24"} />
        <input aria-label="Price" value={p} onChange={(e) => setP(e.target.value)} className={inputClass + " h-8 w-24"} />
        <input aria-label="Fee" value={f} onChange={(e) => setF(e.target.value)} className={inputClass + " h-8 w-20"} />
        <Button size="sm" type="submit" disabled={m.pending}>
          Save
        </Button>
        <Button size="sm" variant="ghost" type="button" onClick={() => setEditing(false)}>
          Cancel
        </Button>
        {m.error ? <span className="basis-full text-right text-xs text-negative">{m.error}</span> : null}
      </form>
    );
  }
  return (
    <span className="inline-flex flex-wrap justify-end gap-1">
      {canEdit ? (
        <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
          Edit
        </Button>
      ) : null}
      <Button
        size="sm"
        variant="ghost"
        disabled={m.pending}
        onClick={() => {
          if (confirm("Delete this transaction?")) m.run(`/api/portfolio/transactions/${tx.id}`, "DELETE");
        }}
      >
        Delete
      </Button>
      {m.error ? <span className="basis-full text-right text-xs text-negative">{m.error}</span> : null}
    </span>
  );
}
