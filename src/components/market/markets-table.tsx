import Link from "next/link";
import { UNSCORED_COIN_IDS } from "@/config/coins";
import { fmtUsd } from "@/lib/format";
import { changeFor, type MarketsQuery, type SortKey } from "@/lib/market/query";
import type { CoinMarket } from "@/lib/market/types";
import type { StoredScore } from "@/lib/research/service";
import { ScoreBadge, ScoreLocked } from "../research/score";
import { CoinIdentity, PriceChange } from "./bits";

function SortHeader({ label, k, query, hrefFor, className }: { label: string; k: SortKey; query: MarketsQuery; hrefFor: (q: Partial<MarketsQuery>) => string; className?: string }) {
  const active = query.sort === k;
  const nextDir = active && query.dir === "asc" ? "desc" : active ? "asc" : k === "rank" || k === "name" ? "asc" : "desc";
  return (
    <th scope="col" className={className} aria-sort={active ? (query.dir === "asc" ? "ascending" : "descending") : "none"}>
      <Link href={hrefFor({ sort: k, dir: nextDir, page: 1 })} className="inline-flex items-center gap-1 hover:text-fg">
        {label}
        <span aria-hidden className="text-[10px]">
          {active ? (query.dir === "asc" ? "▲" : "▼") : ""}
        </span>
      </Link>
    </th>
  );
}

export function MarketsTable({
  rows,
  query,
  hrefFor,
  scores,
  showScores = true,
  premium = false,
}: {
  rows: CoinMarket[];
  query: MarketsQuery;
  hrefFor: (q: Partial<MarketsQuery>) => string;
  scores?: Map<string, StoredScore>;
  showScores?: boolean;
  /** Whether the viewer can see every score (so a missing score means "not calculated", not "locked"). */
  premium?: boolean;
}) {
  const periodSortKey = ({ "1h": "change1h", "24h": "change24h", "7d": "change7d", "30d": "change30d" } as const)[query.period];
  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <table className="w-full min-w-[720px] text-sm">
        <caption className="sr-only">Cryptocurrency market data</caption>
        <thead className="bg-subtle text-left text-xs uppercase tracking-wide text-faint">
          <tr>
            <SortHeader label="#" k="rank" query={query} hrefFor={hrefFor} className="w-12 px-3 py-2.5" />
            <SortHeader label="Name" k="name" query={query} hrefFor={hrefFor} className="px-3 py-2.5" />
            <SortHeader label="Price" k="price" query={query} hrefFor={hrefFor} className="px-3 py-2.5 text-right" />
            <SortHeader label={query.period} k={periodSortKey} query={query} hrefFor={hrefFor} className="px-3 py-2.5 text-right" />
            <SortHeader label="Market cap" k="marketCap" query={query} hrefFor={hrefFor} className="px-3 py-2.5 text-right" />
            <SortHeader label="Volume 24h" k="volume" query={query} hrefFor={hrefFor} className="hidden px-3 py-2.5 text-right md:table-cell" />
            {showScores ? (
              <th scope="col" className="px-3 py-2.5 text-right">
                Score
              </th>
            ) : null}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map((c) => {
            const s = scores?.get(c.id);
            return (
              <tr key={c.id} className="hover:bg-subtle/60">
                <td className="tabular px-3 py-2.5 text-faint">{c.marketCapRank ?? "—"}</td>
                <td className="px-3 py-2.5">
                  <Link href={`/coins/${c.id}`} className="block hover:text-accent">
                    <CoinIdentity name={c.name} symbol={c.symbol} imageUrl={c.imageUrl} />
                  </Link>
                </td>
                <td className="tabular px-3 py-2.5 text-right">{fmtUsd(c.priceUsd)}</td>
                <td className="px-3 py-2.5 text-right">
                  <PriceChange value={changeFor(c, query.period)} />
                </td>
                <td className="tabular px-3 py-2.5 text-right">{fmtUsd(c.marketCapUsd, { compact: true })}</td>
                <td className="tabular hidden px-3 py-2.5 text-right md:table-cell">{fmtUsd(c.volume24hUsd, { compact: true })}</td>
                {showScores ? (
                  <td className="px-3 py-2.5 text-right">{s ? (
                      <ScoreBadge score={s.overallScore} status={s.status} />
                    ) : UNSCORED_COIN_IDS.has(c.id) ? (
                      <span className="text-xs text-faint" title="Stablecoins are not scored">n/a</span>
                    ) : premium ? (
                      <span className="text-xs text-faint" title="Not yet calculated">—</span>
                    ) : (
                      <ScoreLocked compact />
                    )}</td>
                ) : null}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
