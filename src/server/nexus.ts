import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { z } from "zod";
import { mapDbError } from "@/lib/db/errors";

/**
 * Read models for NEXUS pages. Every query uses the caller's own client, so
 * RLS decides what is visible (signals by gate G-1 and tier; candles public).
 */

export const TIMEFRAMES = ["15m", "1h", "4h", "1d"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];
export const TimeframeSchema = z.enum(TIMEFRAMES);

export interface Pair {
  id: number;
  symbol: string;
  baseAsset: string;
  quoteAsset: string;
  timeframes: Timeframe[];
  source: string;
}

export async function listPairs(sb: SupabaseClient): Promise<Pair[]> {
  const { data, error } = await sb.from("market_pairs").select("id, symbol, base_asset, quote_asset, timeframes, market_sources(name)").order("id");
  if (error) throw mapDbError(error, "list pairs");
  return (data ?? []).map((p) => ({
    id: p.id, symbol: p.symbol, baseAsset: p.base_asset, quoteAsset: p.quote_asset, timeframes: p.timeframes as Timeframe[],
    source: (p.market_sources as unknown as { name: string } | null)?.name ?? "—",
  }));
}

export interface CandleRow {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

/** Final candles, oldest first. Numbers are for display only. */
export async function getCandles(sb: SupabaseClient, pairId: number, tf: Timeframe, limit = 300): Promise<CandleRow[]> {
  const { data, error } = await sb
    .from("market_candles")
    .select("open_time, open, high, low, close, volume")
    .eq("pair_id", pairId)
    .eq("timeframe", tf)
    .eq("is_final", true)
    .order("open_time", { ascending: false })
    .limit(Math.min(Math.max(limit, 1), 1000));
  if (error) throw mapDbError(error, "get candles");
  return (data ?? [])
    .reverse()
    .map((r) => ({ t: Date.parse(r.open_time), o: Number(r.open), h: Number(r.high), l: Number(r.low), c: Number(r.close), v: Number(r.volume) }));
}

export async function getScanner(sb: SupabaseClient) {
  const { data, error } = await sb.from("market_scanner").select("*").order("symbol").order("timeframe");
  if (error) throw mapDbError(error, "scanner");
  return data ?? [];
}

export interface SignalSummary {
  id: string;
  pairId: number;
  symbol: string;
  timeframe: Timeframe;
  direction: "BUY" | "SELL";
  asOf: string;
  publishedAt: string;
  entryLow: string;
  entryHigh: string;
  stopLoss: string;
  takeProfits: string[];
  riskReward: string;
  confidenceBand: string;
  visibility: string;
  outcome: { outcome: string; netReturn: string | null; rMultiple: string | null; closedAt: string } | null;
}

const SIGNAL_COLUMNS =
  "id, pair_id, timeframe, direction, as_of, published_at, entry_low, entry_high, stop_loss, take_profits, risk_reward, confidence_band, visibility, market_pairs(symbol), signal_outcomes(outcome, net_return, r_multiple, closed_at)";

type SignalRow = Record<string, unknown> & { market_pairs: { symbol: string } | null; signal_outcomes: unknown };

function toSummary(r: SignalRow): SignalSummary {
  const o = (Array.isArray(r.signal_outcomes) ? r.signal_outcomes[0] : r.signal_outcomes) as Record<string, string | null> | null | undefined;
  return {
    id: String(r.id), pairId: Number(r.pair_id), symbol: r.market_pairs?.symbol ?? "—", timeframe: r.timeframe as Timeframe,
    direction: r.direction as "BUY" | "SELL", asOf: String(r.as_of), publishedAt: String(r.published_at),
    entryLow: String(r.entry_low), entryHigh: String(r.entry_high), stopLoss: String(r.stop_loss),
    takeProfits: (r.take_profits as unknown[]).map(String), riskReward: String(r.risk_reward), confidenceBand: String(r.confidence_band),
    visibility: String(r.visibility),
    outcome: o ? { outcome: String(o.outcome), netReturn: o.net_return, rMultiple: o.r_multiple, closedAt: String(o.closed_at) } : null,
  };
}

export async function listSignals(sb: SupabaseClient, limit = 50): Promise<SignalSummary[]> {
  const { data, error } = await sb.from("signals").select(SIGNAL_COLUMNS).order("published_at", { ascending: false }).limit(limit);
  if (error) throw mapDbError(error, "list signals");
  return (data ?? []).map((r) => toSummary(r as unknown as SignalRow));
}

export async function getSignal(sb: SupabaseClient, id: string) {
  const { data, error } = await sb
    .from("signals")
    .select(`${SIGNAL_COLUMNS}, explanation, assumptions, entry_expires_at, horizon_bars, content_hash, prev_hash, strategies(key, version, name)`)
    .eq("id", id)
    .maybeSingle();
  if (error) throw mapDbError(error, "get signal");
  if (!data) return null;
  const [events, annotations] = await Promise.all([
    sb.from("signal_events").select("event_type, occurred_at, price").eq("signal_id", id).order("occurred_at"),
    sb.from("signal_annotations").select("kind, body, created_at").eq("signal_id", id).order("created_at"),
  ]);
  if (events.error) throw mapDbError(events.error, "signal events");
  if (annotations.error) throw mapDbError(annotations.error, "signal annotations");
  const row = data as unknown as SignalRow & {
    explanation: Record<string, unknown>; assumptions: Record<string, string>; entry_expires_at: string; horizon_bars: number;
    content_hash: string; prev_hash: string | null; strategies: { key: string; version: string; name: string } | null;
  };
  return {
    ...toSummary(row), explanation: row.explanation, assumptions: row.assumptions, entryExpiresAt: row.entry_expires_at,
    horizonBars: row.horizon_bars, contentHash: row.content_hash, prevHash: row.prev_hash, strategy: row.strategies,
    events: events.data ?? [], annotations: annotations.data ?? [],
  };
}

export async function getPerformance(sb: SupabaseClient) {
  const { data, error } = await sb.from("signal_performance").select("*, market_pairs(symbol)");
  if (error) throw mapDbError(error, "performance");
  return data ?? [];
}
