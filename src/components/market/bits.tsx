import Image from "next/image";
import { fmtDate, fmtPct, fmtRelative } from "@/lib/format";
import type { MarketDataSource } from "@/lib/market/types";
import { Badge, cn } from "../ui";

export function PriceChange({ value, className }: { value: number | null | undefined; className?: string }) {
  if (value === null || value === undefined || !Number.isFinite(value)) return <span className={cn("text-faint", className)}>—</span>;
  const tone = value > 0 ? "text-positive" : value < 0 ? "text-negative" : "text-muted";
  return (
    <span className={cn("tabular", tone, className)}>
      <span className="sr-only">{value > 0 ? "up" : value < 0 ? "down" : "unchanged"} </span>
      {fmtPct(value, { signed: true })}
    </span>
  );
}

const SOURCE_LABEL: Record<MarketDataSource, string> = { coingecko: "CoinGecko", mock: "Mock data" };

/**
 * Shows where data came from and how old it is. Stale (cached after a failed
 * refresh) data is always labeled as such.
 */
export function FreshnessBadge({ source, fetchedAt, stale }: { source: MarketDataSource; fetchedAt: string; stale: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-faint">
      {source === "mock" ? (
        <Badge tone="warning">Mock data — not real prices</Badge>
      ) : (
        <span>
          Data: <span className="text-muted">{SOURCE_LABEL[source]}</span>
        </span>
      )}
      <time dateTime={fetchedAt} title={fmtDate(fetchedAt, { time: true })}>
        Retrieved {fmtRelative(fetchedAt)}
      </time>
      {stale ? (
        <Badge tone="warning" title="The provider could not be reached; showing the last successfully retrieved data.">
          Stale — provider unavailable
        </Badge>
      ) : null}
    </div>
  );
}

export function CoinIdentity({ name, symbol, imageUrl, size = 24 }: { name: string; symbol: string; imageUrl: string | null; size?: number }) {
  return (
    <span className="flex min-w-0 items-center gap-2.5">
      {imageUrl ? (
        <Image src={imageUrl} alt="" width={size} height={size} className="shrink-0 rounded-full" unoptimized />
      ) : (
        <span aria-hidden style={{ width: size, height: size }} className="flex shrink-0 items-center justify-center rounded-full bg-subtle text-[10px] font-semibold text-muted">
          {symbol.slice(0, 2)}
        </span>
      )}
      <span className="min-w-0">
        <span className="block truncate font-medium">{name}</span>
        <span className="block text-xs text-faint">{symbol}</span>
      </span>
    </span>
  );
}

export function ProviderUnavailable({ what = "Market data" }: { what?: string }) {
  return (
    <div role="alert" className="rounded-lg border border-negative/40 bg-danger-soft px-5 py-6 text-sm">
      <p className="font-medium text-negative">{what} is temporarily unavailable.</p>
      <p className="mt-1 text-muted">
        Our data provider could not be reached and no recent cached data is available. We do not display estimated or invented prices. Please try again
        shortly.
      </p>
    </div>
  );
}
