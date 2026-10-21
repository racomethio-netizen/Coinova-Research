"use client";

import { useState } from "react";
import { Button, Field, inputClass } from "@/components/ui";
import { ALERT_TYPE_LABELS, ALERT_TYPES, PORTFOLIO_ALERT_TYPES, type AlertType, type Channel } from "@/lib/alerts/evaluate";
import { useMutation } from "@/lib/client/use-mutation";

export function AlertForm({
  coins,
  portfolios,
  telegramLinked,
  disabled,
}: {
  coins: { id: string; label: string }[];
  portfolios: { id: string; label: string }[];
  telegramLinked: boolean;
  disabled: boolean;
}) {
  const m = useMutation();
  const [type, setType] = useState<AlertType>("price_above");
  const [coinId, setCoinId] = useState(coins[0]?.id ?? "");
  const [portfolioId, setPortfolioId] = useState(portfolios[0]?.id ?? "");
  const [threshold, setThreshold] = useState("");
  const [channels, setChannels] = useState<Channel[]>(["in_app"]);
  const [triggerMode, setTriggerMode] = useState<"once" | "recurring">("recurring");
  const [cooldown, setCooldown] = useState("60");
  const isPortfolio = PORTFOLIO_ALERT_TYPES.includes(type);
  const toggle = (c: Channel) => setChannels((cs) => (cs.includes(c) ? cs.filter((x) => x !== c) : [...cs, c]));

  return (
    <form
      className="grid gap-4 sm:grid-cols-2"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        m.run(
          "/api/alerts",
          "POST",
          {
            type,
            coinId: isPortfolio ? null : coinId,
            portfolioId: isPortfolio ? portfolioId : null,
            threshold: Number(threshold),
            channels,
            triggerMode,
            cooldownMinutes: Number(cooldown),
          },
          () => setThreshold(""),
        );
      }}
    >
      <Field id="alert-type" label="Condition">
        <select id="alert-type" value={type} onChange={(e) => setType(e.target.value as AlertType)} className={inputClass}>
          {ALERT_TYPES.map((t) => (
            <option key={t} value={t} disabled={PORTFOLIO_ALERT_TYPES.includes(t) && portfolios.length === 0}>
              {ALERT_TYPE_LABELS[t]}
            </option>
          ))}
        </select>
      </Field>
      {isPortfolio ? (
        <Field id="alert-portfolio" label="Portfolio" error={m.fields.portfolioId}>
          <select id="alert-portfolio" value={portfolioId} onChange={(e) => setPortfolioId(e.target.value)} className={inputClass}>
            {portfolios.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </Field>
      ) : (
        <Field id="alert-coin" label="Coin" error={m.fields.coinId}>
          <select id="alert-coin" value={coinId} onChange={(e) => setCoinId(e.target.value)} className={inputClass}>
            {coins.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
              </option>
            ))}
          </select>
        </Field>
      )}
      <Field id="alert-threshold" label="Threshold" error={m.fields.threshold} hint={type === "unlock_upcoming" ? "Number of days ahead (1–365). Only verified unlock data is used." : undefined}>
        <input id="alert-threshold" inputMode="decimal" value={threshold} onChange={(e) => setThreshold(e.target.value)} className={inputClass} aria-invalid={Boolean(m.fields.threshold) || undefined} />
      </Field>
      <Field id="alert-mode" label="Frequency">
        <select id="alert-mode" value={triggerMode} onChange={(e) => setTriggerMode(e.target.value as "once" | "recurring")} className={inputClass}>
          <option value="recurring">Every time the condition becomes true</option>
          <option value="once">Once, then pause the alert</option>
        </select>
      </Field>
      <Field id="alert-cooldown" label="Cooldown (minutes)" error={m.fields.cooldownMinutes} hint="Minimum time between notifications (5–10080).">
        <input id="alert-cooldown" inputMode="numeric" value={cooldown} onChange={(e) => setCooldown(e.target.value)} className={inputClass} />
      </Field>
      <fieldset className="space-y-1.5">
        <legend className="text-sm font-medium">Deliver via</legend>
        <div className="flex flex-wrap gap-4 text-sm">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={channels.includes("in_app")} onChange={() => toggle("in_app")} /> In-app
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={channels.includes("email")} onChange={() => toggle("email")} /> Email
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={channels.includes("telegram")} onChange={() => toggle("telegram")} disabled={!telegramLinked} /> Telegram
            {!telegramLinked ? <span className="text-xs text-faint">(not linked)</span> : null}
          </label>
        </div>
        {m.fields.channels ? <p className="text-xs text-negative">{m.fields.channels}</p> : null}
      </fieldset>
      <div className="flex items-center gap-3 sm:col-span-2">
        <Button type="submit" disabled={disabled || m.pending || !threshold || channels.length === 0}>
          Create alert
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

export function AlertRowActions({ id, isActive, canEnable }: { id: string; isActive: boolean; canEnable: boolean }) {
  const m = useMutation();
  return (
    <div className="flex items-center gap-2">
      <Button size="sm" variant="secondary" disabled={m.pending || (!isActive && !canEnable)} onClick={() => m.run(`/api/alerts/${id}`, "PATCH", { isActive: !isActive })}>
        {isActive ? "Pause" : "Resume"}
      </Button>
      <Button
        size="sm"
        variant="ghost"
        disabled={m.pending}
        onClick={() => {
          if (confirm("Delete this alert?")) m.run(`/api/alerts/${id}`, "DELETE");
        }}
      >
        Delete
      </Button>
      {m.error ? <span className="text-xs text-negative">{m.error}</span> : null}
    </div>
  );
}
