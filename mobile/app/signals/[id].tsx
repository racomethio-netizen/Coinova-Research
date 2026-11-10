import { SignalDetailSchema } from "@nexus/shared";
import { useLocalSearchParams } from "expo-router";
import * as WebBrowser from "expo-web-browser";
import { z } from "zod";
import { Badge, Button, Card, Disclaimer, ErrorNote, H1, Loading, Row, Screen, T } from "@/components/ui";
import { config } from "@/lib/config";
import { fmtDateTime, fmtDecimal, fmtPctFromFraction, OUTCOME_LABEL } from "@/lib/format";
import { useApi } from "@/lib/use-api";

const Schema = z.object({ signal: SignalDetailSchema });

export default function SignalDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const valid = z.uuid().safeParse(id).success;
  const { result, loading } = useApi(valid ? `/api/signals/${id}` : null, Schema);
  if (!valid) return <Screen><ErrorNote message="Signal not found." /></Screen>;
  if (loading && !result) return <Loading />;
  if (!result?.ok) return <Screen><ErrorNote message={result?.error.code === "not_found" ? "This signal is not available to your account." : result?.error.message ?? "Could not load."} /></Screen>;
  const s = result.data.signal;
  return (
    <Screen>
      <H1>{s.direction === "BUY" ? "▲ BUY" : "▼ SELL"} {s.symbol} · {s.timeframe}</H1>
      {s.explanation.summary ? <T tone="muted">{s.explanation.summary}</T> : null}
      <Card>
        <Row label="Entry range" value={`${fmtDecimal(s.entryLow)} – ${fmtDecimal(s.entryHigh)}`} />
        <Row label="Stop loss" value={fmtDecimal(s.stopLoss)} />
        <Row label="Targets" value={s.takeProfits.map((t) => fmtDecimal(t)).join(" · ")} />
        <Row label="Net reward : risk" value={s.riskReward} />
        <Row label="Confidence" value={s.explanation.confidence ?? s.confidenceBand} />
        <Row label="Decided at close" value={fmtDateTime(s.asOf)} />
        <Row label="Published" value={fmtDateTime(s.publishedAt)} />
      </Card>
      <Button variant="secondary" label="Open chart (website)" onPress={() => WebBrowser.openBrowserAsync(`${config.apiUrl}/charts/${s.symbol}?tf=${s.timeframe}`)} />
      <Card>
        <T style={{ fontWeight: "700" }}>Why this signal</T>
        {(s.explanation.reasons ?? []).map((r) => <T key={r.code}>• {r.detail}</T>)}
        {s.explanation.invalidation ? <T tone="muted">Invalidation: {s.explanation.invalidation}</T> : null}
        {(s.explanation.risks ?? []).map((r) => <T key={r} tone="faint" style={{ fontSize: 12 }}>{r}</T>)}
      </Card>
      <Card>
        <T style={{ fontWeight: "700" }}>Lifecycle</T>
        <Badge label={s.outcome ? OUTCOME_LABEL[s.outcome.outcome] ?? s.outcome.outcome : "Open"} />
        {s.events.map((e) => <T key={e.event_type}>{fmtDateTime(e.occurred_at)} — {e.event_type.replace(/_/g, " ")}{e.price !== null ? ` at ${fmtDecimal(String(e.price))}` : ""}</T>)}
        {s.outcome ? <Row label="Net return (after costs)" value={fmtPctFromFraction(s.outcome.netReturn, 2)} /> : null}
      </Card>
      {s.annotations.length ? (
        <Card>
          <T style={{ fontWeight: "700" }}>Annotations</T>
          {s.annotations.map((a) => <T key={a.created_at}>[{a.kind}] {a.body}</T>)}
        </Card>
      ) : null}
      <T tone="faint" style={{ fontSize: 11 }}>Hash {s.contentHash}</T>
      <Disclaimer />
    </Screen>
  );
}
