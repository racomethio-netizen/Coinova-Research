"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Badge, Button, Card, Loading, PageHeader, Table } from "@/components/ui";
import { useApi } from "@/lib/hooks";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

export default function TelegramPage() {
  const t = useApi<Any>("/telegram");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const test = async () => {
    setMsg(null); setBusy(true);
    try {
      const r = await api.post<{ ok: boolean; error: string | null }>("/telegram/test");
      setMsg(r.ok ? "Test message sent. Check your Telegram chat." : `Telegram refused the message: ${r.error}`);
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  return (
    <>
      <PageHeader title="Telegram settings" subtitle="Alerts are sent by the notifier service through the Telegram Bot API. The bot token stays on the server." />
      <Loading error={t.error} loading={t.loading && !t.data} />
      {msg && <p role="status" className="mb-3 rounded-lg border border-line bg-elevated p-3 text-sm">{msg}</p>}
      {t.data && (
        <div className="space-y-4">
          <Card title="Connection" actions={<Badge tone={t.data.configured ? "good" : "warn"}>{t.data.configured ? "configured" : "not configured"}</Badge>}>
            {t.data.configured ? (
              <p className="mb-3 text-sm">Sending to chat <span className="num">{t.data.chat_id}</span>.</p>
            ) : (
              <ol className="mb-3 list-decimal space-y-1 pl-5 text-sm">
                <li>Create a bot with @BotFather in Telegram and copy its token.</li>
                <li>Send any message to your bot, then get your chat ID (for example from @userinfobot).</li>
                <li>Set <code>NEXUS_TELEGRAM_BOT_TOKEN</code> and <code>NEXUS_TELEGRAM_CHAT_ID</code> in the server environment (Render: Environment tab) and redeploy.</li>
              </ol>
            )}
            <Button tone="primary" disabled={busy || !t.data.configured} onClick={test}>{busy ? "Sending…" : "Send test message"}</Button>
          </Card>
          <Card title="Alerts sent">
            <Table rows={t.data.events} empty="—" columns={[
              { key: "description", label: "Event" }, { key: "type", label: "Type" },
            ]} />
          </Card>
        </div>
      )}
    </>
  );
}
