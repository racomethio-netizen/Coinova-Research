import { SignalsResponseSchema } from "@nexus/shared";
import { Link } from "expo-router";
import { Pressable, RefreshControl } from "react-native";
import { Badge, Card, Disclaimer, ErrorNote, H1, Row, Screen, T } from "@/components/ui";
import { fmtDateTime, fmtDecimal, OUTCOME_LABEL } from "@/lib/format";
import { useApi } from "@/lib/use-api";

export default function Signals() {
  const { result, loading, reload } = useApi("/api/signals", SignalsResponseSchema);
  return (
    <Screen refreshControl={<RefreshControl refreshing={loading} onRefresh={reload} />}>
      <H1>Signals</H1>
      <T tone="muted">Every published signal stays visible, wins and losses alike. Signals are never edited; corrections appear as annotations.</T>
      {result && !result.ok ? <ErrorNote message={result.error.message} /> : null}
      {result?.ok && result.data.gate === "staff" && result.data.signals.length === 0 ? (
        <Card><T>Signals are in internal review (shadow mode). They will appear here once publication is approved.</T></Card>
      ) : null}
      {result?.ok ? result.data.signals.map((s) => (
        <Link key={s.id} href={{ pathname: "/signals/[id]", params: { id: s.id } }} asChild>
          <Pressable accessibilityRole="link" accessibilityLabel={`${s.direction} ${s.symbol} ${s.timeframe}`}>
            <Card>
              <T style={{ fontWeight: "700" }}>{s.direction === "BUY" ? "▲ BUY" : "▼ SELL"} {s.symbol} · {s.timeframe}</T>
              <Badge label={s.outcome ? OUTCOME_LABEL[s.outcome.outcome] ?? s.outcome.outcome : "Open"} tone={s.outcome?.outcome === "win" ? "positive" : s.outcome?.outcome === "loss" ? "negative" : "accent"} />
              <Row label="Entry" value={`${fmtDecimal(s.entryLow)} – ${fmtDecimal(s.entryHigh)}`} />
              <Row label="Stop / Target 1" value={`${fmtDecimal(s.stopLoss)} / ${fmtDecimal(s.takeProfits[0])}`} />
              <Row label="Published" value={fmtDateTime(s.publishedAt)} />
            </Card>
          </Pressable>
        </Link>
      )) : null}
      <Disclaimer />
    </Screen>
  );
}
