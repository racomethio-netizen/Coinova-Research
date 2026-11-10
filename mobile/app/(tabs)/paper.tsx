import { PaperOrderSchema, PaperSummarySchema, PairSchema } from "@nexus/shared";
import { useState } from "react";
import { Pressable, RefreshControl, TextInput, View } from "react-native";
import { z } from "zod";
import { Badge, Button, Card, colors, ErrorNote, H1, Row, Screen, T } from "@/components/ui";
import { fmtDecimal } from "@/lib/format";
import { useSession } from "@/lib/session";
import { useApi } from "@/lib/use-api";

const Pairs = z.object({ pairs: z.array(PairSchema) });
const Any = z.object({}).loose();
const input = { backgroundColor: colors.subtle, color: colors.fg, borderColor: colors.line, borderWidth: 1, borderRadius: 8, padding: 10, minHeight: 44 } as const;

function Choice<T extends string>({ value, options, onChange, label }: { value: T; options: readonly T[]; onChange: (v: T) => void; label: string }) {
  return (
    <View accessibilityRole="radiogroup" accessibilityLabel={label} style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
      {options.map((o) => (
        <Pressable key={o} accessibilityRole="radio" accessibilityState={{ selected: value === o }} onPress={() => onChange(o)}
          style={{ minHeight: 40, paddingHorizontal: 12, justifyContent: "center", borderRadius: 8, borderWidth: 1, borderColor: value === o ? colors.accent : colors.line }}>
          <T tone={value === o ? "accent" : "muted"}>{o}</T>
        </Pressable>
      ))}
    </View>
  );
}

export default function Paper() {
  const { api } = useSession();
  const summary = useApi("/api/paper", PaperSummarySchema);
  const pairs = useApi("/api/pairs", Pairs);
  const [form, setForm] = useState({ pair: "", side: "buy" as "buy" | "sell", type: "market" as "market" | "limit", quantity: "", limitPrice: "", stopLoss: "", takeProfit: "" });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const pairList = pairs.result?.ok ? pairs.result.data.pairs : [];
  const pairId = pairList.find((p) => p.symbol === form.pair)?.id ?? pairList[0]?.id;
  const acct = summary.result?.ok ? summary.result.data.account : null;
  const refresh = () => { void summary.reload(); void pairs.reload(); };

  async function submit() {
    const body = {
      pairId, side: form.side, type: form.type, quantity: form.quantity.trim(),
      ...(form.type === "limit" && form.limitPrice.trim() ? { limitPrice: form.limitPrice.trim() } : {}),
      ...(form.side === "buy" && form.stopLoss.trim() ? { stopLoss: form.stopLoss.trim() } : {}),
      ...(form.side === "buy" && form.takeProfit.trim() ? { takeProfit: form.takeProfit.trim() } : {}),
    };
    const local = PaperOrderSchema.safeParse(body); // same rules as the server, checked before sending
    if (!local.success) return setMsg({ ok: false, text: local.error.issues[0]?.message ?? "Invalid order." });
    const res = await api.call("/api/paper/orders", Any, { method: "POST", body: local.data });
    setMsg(res.ok ? { ok: true, text: "Virtual order placed. It fills against the next fresh price." } : { ok: false, text: res.error.message });
    if (res.ok) refresh();
  }

  return (
    <Screen refreshControl={<RefreshControl refreshing={summary.loading} onRefresh={refresh} />}>
      <H1>Paper trading</H1>
      <Badge label="Virtual funds — no real money" tone="warning" />
      {summary.result && !summary.result.ok ? <ErrorNote message={summary.result.error.message} /> : null}
      {summary.result?.ok && !acct ? (
        <Card>
          <T>Open a virtual account with 10,000 USDT of practice funds.</T>
          <Button label="Open virtual account" onPress={async () => { const r = await api.call("/api/paper/account", Any, { method: "POST", body: { action: "open" } }); if (r.ok) refresh(); else setMsg({ ok: false, text: r.error.message }); }} />
        </Card>
      ) : null}
      {acct ? (
        <>
          <Card>
            <Row label="Equity" value={acct.equity ? fmtDecimal(acct.equity, 2) : "— (price unavailable)"} />
            <Row label="Cash" value={fmtDecimal(acct.cash, 2)} />
            <Row label="Realized P/L" value={fmtDecimal(acct.realizedPnl, 2)} />
          </Card>
          <Card>
            <T style={{ fontWeight: "700" }}>New virtual order</T>
            <Choice label="Pair" value={form.pair || pairList[0]?.symbol || ""} options={pairList.map((p) => p.symbol)} onChange={(v) => setForm({ ...form, pair: v })} />
            <Choice label="Side" value={form.side} options={["buy", "sell"] as const} onChange={(v) => setForm({ ...form, side: v })} />
            <Choice label="Type" value={form.type} options={["market", "limit"] as const} onChange={(v) => setForm({ ...form, type: v })} />
            <TextInput accessibilityLabel="Quantity" placeholder="Quantity" placeholderTextColor={colors.faint} keyboardType="decimal-pad" value={form.quantity} onChangeText={(v) => setForm({ ...form, quantity: v })} style={input} />
            {form.type === "limit" ? <TextInput accessibilityLabel="Limit price" placeholder="Limit price" placeholderTextColor={colors.faint} keyboardType="decimal-pad" value={form.limitPrice} onChangeText={(v) => setForm({ ...form, limitPrice: v })} style={input} /> : null}
            {form.side === "buy" ? (
              <>
                <TextInput accessibilityLabel="Stop loss (optional)" placeholder="Stop loss (optional)" placeholderTextColor={colors.faint} keyboardType="decimal-pad" value={form.stopLoss} onChangeText={(v) => setForm({ ...form, stopLoss: v })} style={input} />
                <TextInput accessibilityLabel="Take profit (optional)" placeholder="Take profit (optional)" placeholderTextColor={colors.faint} keyboardType="decimal-pad" value={form.takeProfit} onChangeText={(v) => setForm({ ...form, takeProfit: v })} style={input} />
              </>
            ) : null}
            {msg ? <T tone={msg.ok ? "positive" : "negative"}>{msg.text}</T> : null}
            <Button label="Place virtual order" disabled={!pairId || !form.quantity} onPress={submit} />
          </Card>
          <Card>
            <T style={{ fontWeight: "700" }}>Positions</T>
            {acct.positions.filter((p) => Number(p.quantity) > 0).length === 0 ? <T tone="muted">No open positions.</T> : acct.positions.filter((p) => Number(p.quantity) > 0).map((p) => (
              <View key={p.pairId} style={{ gap: 2, paddingVertical: 6 }}>
                <T style={{ fontWeight: "600" }}>{p.symbol}{p.priceStale ? " (price stale)" : ""}</T>
                <Row label="Quantity" value={fmtDecimal(p.quantity)} />
                <Row label="Avg entry" value={fmtDecimal(p.avgEntry)} />
                <Row label="Unrealized" value={fmtDecimal(p.unrealizedPnl, 2)} />
              </View>
            ))}
          </Card>
          <Card>
            <T style={{ fontWeight: "700" }}>Recent orders</T>
            {(summary.result?.ok ? summary.result.data.orders ?? [] : []).slice(0, 10).map((o) => (
              <View key={o.id} style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8, paddingVertical: 4 }}>
                <T>{o.side.toUpperCase()} {o.order_type} · {o.status}</T>
                {o.status === "pending" ? <Button variant="secondary" label="Cancel" onPress={async () => { const r = await api.call(`/api/paper/orders/${o.id}`, Any, { method: "DELETE" }); if (r.ok) refresh(); else setMsg({ ok: false, text: r.error.message }); }} /> : null}
              </View>
            ))}
          </Card>
        </>
      ) : null}
      <T tone="faint" style={{ fontSize: 12 }}>Simulated fills: latest public price (at most 60 s old), 0.05% slippage, 0.1% fee. Long positions only. Not financial advice.</T>
    </Screen>
  );
}
