import { ScannerRowSchema } from "@nexus/shared";
import { RefreshControl } from "react-native";
import { z } from "zod";
import { Badge, Card, Disclaimer, ErrorNote, H1, Row, Screen, T } from "@/components/ui";
import { fmtDateTime, fmtDecimal } from "@/lib/format";
import { useApi } from "@/lib/use-api";

const Schema = z.object({ rows: z.array(ScannerRowSchema) });

export default function Scanner() {
  const { result, loading, reload } = useApi("/api/scanner", Schema);
  return (
    <Screen refreshControl={<RefreshControl refreshing={loading} onRefresh={reload} />}>
      <H1>Market scanner</H1>
      <T tone="muted">Indicator states at the latest closed candle. Not forecasts.</T>
      {result && !result.ok ? <ErrorNote message={result.error.message} /> : null}
      {result?.ok && result.data.rows.length === 0 ? <T tone="muted">No indicator data yet.</T> : null}
      {result?.ok ? result.data.rows.map((r) => {
        const above = Number(r.close) > Number(r.ema200);
        return (
          <Card key={`${r.pairId}-${r.timeframe}`}>
            <T style={{ fontWeight: "700" }}>{r.symbol} · {r.timeframe}</T>
            <Badge label={above ? "Above EMA200" : "Below EMA200"} tone={above ? "positive" : "negative"} />
            {r.htfTrendUp !== null ? <Badge label={`${r.htf} ${r.htfTrendUp ? "EMA50 > EMA200" : "EMA50 < EMA200"}`} tone={r.htfTrendUp ? "positive" : "negative"} /> : null}
            {r.macdHist === null ? <T tone="faint" style={{ fontSize: 12 }}>Advanced indicators are included in Pro.</T> : null}
            <Row label="Close" value={fmtDecimal(r.close)} />
            <Row label="RSI(14)" value={Number(r.rsi14).toFixed(1)} />
            <Row label="ATR(14)" value={fmtDecimal(r.atr14)} />
            <Row label="Volume × avg" value={r.volumeRatio === null ? "—" : Number(r.volumeRatio).toFixed(2)} />
            <Row label="As of" value={fmtDateTime(r.asOf)} />
          </Card>
        );
      }) : null}
      <Disclaimer />
    </Screen>
  );
}
