import { MembershipSchema, PaperSummarySchema } from "@nexus/shared";
import { RefreshControl } from "react-native";
import { Badge, Card, Disclaimer, ErrorNote, H1, Row, Screen, T } from "@/components/ui";
import { fmtDecimal } from "@/lib/format";
import { useSession } from "@/lib/session";
import { useApi } from "@/lib/use-api";

export default function Home() {
  const { session } = useSession();
  const membership = useApi("/api/membership", MembershipSchema);
  const paper = useApi("/api/paper", PaperSummarySchema);
  const acct = paper.result?.ok ? paper.result.data.account : null;
  return (
    <Screen refreshControl={<RefreshControl refreshing={membership.loading || paper.loading} onRefresh={() => { void membership.reload(); void paper.reload(); }} />}>
      <H1>Overview</H1>
      <Card>
        <T tone="muted">{session?.user.email}</T>
        {membership.result?.ok ? <Badge label={`Plan: ${membership.result.data.tier}`} /> : null}
        {/* Plans are managed on the website; the app shows no purchase options. */}
      </Card>
      {paper.result && !paper.result.ok ? <ErrorNote message={paper.result.error.message} /> : null}
      <Card>
        <T style={{ fontWeight: "700" }}>Paper trading (virtual)</T>
        {acct ? (
          <>
            <Row label="Equity" value={acct.equity ? fmtDecimal(acct.equity, 2) : "— (price unavailable)"} />
            <Row label="Cash" value={fmtDecimal(acct.cash, 2)} />
            <Row label="Realized P/L" value={fmtDecimal(acct.realizedPnl, 2)} />
          </>
        ) : <T tone="muted">No virtual account yet. Open one in the Paper tab.</T>}
      </Card>
      <Disclaimer />
    </Screen>
  );
}
