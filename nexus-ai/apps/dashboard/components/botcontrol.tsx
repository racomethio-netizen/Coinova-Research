"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { Badge, Button, Card, Field, Loading } from "@/components/ui";
import { time } from "@/lib/format";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;
const LABEL: Record<string, string> = {
  running: "Running", paused: "Paused: no new entries", stopped: "Stopped: no new orders",
};

export function BotControl() {
  const bot = useApi<Any>("/bot", 10000);
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const act = async (action: "start" | "pause" | "stop", question: string) => {
    if (reason.trim().length < 5) { setMsg("Enter a reason (at least 5 characters)."); return; }
    if (!window.confirm(question)) return;
    setMsg(null);
    try {
      await api.post(`/bot/${action}`, { reason });
      setReason("");
      await bot.reload();
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Action failed");
    }
  };
  const state: string = bot.data?.state ?? "unknown";
  return (
    <Card title="Bot control" actions={<Badge tone={state === "running" ? "good" : state === "paused" ? "warn" : "bad"}>{state}</Badge>}>
      <Loading error={bot.error} loading={bot.loading && !bot.data} />
      <p className="mb-3 text-sm text-muted">{LABEL[state] ?? "State unknown: treated as stopped"}
        {bot.data?.details && <> · set by {bot.data.details.actor} at {time(bot.data.details.at)}</>}</p>
      <div className="mb-3 max-w-md"><Field id="bot-reason" label="Reason (required for every change)" value={reason} onChange={setReason} /></div>
      <div className="flex flex-wrap gap-2">
        <Button tone="primary" onClick={() => act("start", "Start the bot? New entries will be allowed.")}>START</Button>
        <Button onClick={() => act("pause", "Pause the bot? No new entries; open positions keep their stops.")}>PAUSE</Button>
        <Button tone="danger" onClick={() => act("stop", "Stop the bot? No new orders. Open positions are NOT closed automatically.")}>STOP</Button>
      </div>
      {msg && <p role="alert" className="mt-2 text-sm text-down">{msg}</p>}
    </Card>
  );
}
