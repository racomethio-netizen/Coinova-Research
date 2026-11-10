import { PerformanceResponseSchema } from "@nexus/shared";
import { RefreshControl } from "react-native";
import { Badge, Card, Disclaimer, ErrorNote, H1, Row, Screen, T } from "@/components/ui";
import { fmtPctFromFraction } from "@/lib/format";
import { useApi } from "@/lib/use-api";

export default function Performance() {
  const { result, loading, reload } = useApi("/api/performance", PerformanceResponseSchema);
  return (
    <Screen refreshControl={<RefreshControl refreshing={loading} onRefresh={reload} />}>
      <H1>Performance</H1>
      <T tone="muted">Forward results only, after fees and slippage. Losses, expirations and invalidations all count. No win rate is shown below {result?.ok ? result.data.minSample : 100} trades. Backtests are never mixed in.</T>
      {result && !result.ok ? <ErrorNote message={result.error.message} /> : null}
      {result?.ok && result.data.rows.length === 0 ? <T tone="muted">No published signals to measure yet.</T> : null}
      {result?.ok ? result.data.rows.map((r) => (
        <Card key={`${r.pairId}-${r.timeframe}`}>
          <T style={{ fontWeight: "700" }}>{r.symbol} · {r.timeframe}</T>
          <Badge label={r.insufficientSample ? `Insufficient sample (n = ${r.entered})` : `n = ${r.entered}`} tone={r.insufficientSample ? "warning" : "positive"} />
          <Row label="Signals (closed)" value={`${r.signals} (${r.closed})`} />
          <Row label="Target hit / stopped" value={`${r.wins} / ${r.losses}`} />
          <Row label="Win rate" value={fmtPctFromFraction(r.winRate)} />
          <Row label="Avg R per trade" value={r.avgRMultiple ?? "—"} />
        </Card>
      )) : null}
      <Disclaimer />
    </Screen>
  );
}
