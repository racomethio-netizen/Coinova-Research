import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { mapDbError } from "@/lib/db/errors";
import { ApiError } from "@/lib/http/api";
import { getMarketService } from "@/lib/market";
import { summarizePortfolio, validateLedger, type PortfolioTransaction } from "@/lib/portfolio/calc";
import type { TransactionInput } from "@/lib/portfolio/schemas";

/**
 * Portfolio repository. Every query runs with the user's session client, so
 * Row Level Security guarantees a user can only touch their own rows; the
 * explicit ownership filters below are defense in depth.
 */

export interface PortfolioRow {
  id: string;
  name: string;
  createdAt: string;
}

export interface TransactionRow extends PortfolioTransaction {
  portfolioId: string;
  notes: string | null;
}

const TX_COLUMNS = "id, portfolio_id, coin_id, type, quantity, price_usd, fee_usd, occurred_at, notes, created_at";

function mapTx(r: Record<string, unknown>): TransactionRow {
  return {
    id: r.id as string,
    portfolioId: r.portfolio_id as string,
    coinId: r.coin_id as string,
    type: r.type as TransactionRow["type"],
    quantity: String(r.quantity),
    priceUsd: r.price_usd === null ? null : String(r.price_usd),
    feeUsd: String(r.fee_usd ?? "0"),
    occurredAt: r.occurred_at as string,
    createdAt: r.created_at as string,
    notes: (r.notes as string | null) ?? null,
  };
}

export async function listPortfolios(sb: SupabaseClient, userId: string): Promise<PortfolioRow[]> {
  const { data, error } = await sb.from("portfolios").select("id, name, created_at").eq("user_id", userId).order("created_at");
  if (error) throw mapDbError(error, "listPortfolios");
  return (data ?? []).map((r) => ({ id: r.id, name: r.name, createdAt: r.created_at }));
}

export async function createPortfolio(sb: SupabaseClient, userId: string, name: string): Promise<PortfolioRow> {
  const { data, error } = await sb.from("portfolios").insert({ user_id: userId, name }).select("id, name, created_at").single();
  if (error) throw mapDbError(error, "createPortfolio");
  return { id: data.id, name: data.name, createdAt: data.created_at };
}

export async function renamePortfolio(sb: SupabaseClient, userId: string, id: string, name: string) {
  const { data, error } = await sb.from("portfolios").update({ name }).eq("id", id).eq("user_id", userId).select("id").maybeSingle();
  if (error) throw mapDbError(error, "renamePortfolio");
  if (!data) throw new ApiError("not_found", "Portfolio not found.");
}

export async function deletePortfolio(sb: SupabaseClient, userId: string, id: string) {
  const { data, error } = await sb.from("portfolios").delete().eq("id", id).eq("user_id", userId).select("id").maybeSingle();
  if (error) throw mapDbError(error, "deletePortfolio");
  if (!data) throw new ApiError("not_found", "Portfolio not found.");
}

export async function listTransactions(sb: SupabaseClient, userId: string, portfolioId?: string): Promise<TransactionRow[]> {
  let q = sb.from("portfolio_transactions").select(TX_COLUMNS).eq("user_id", userId).order("occurred_at", { ascending: false }).limit(5000);
  if (portfolioId) q = q.eq("portfolio_id", portfolioId);
  const { data, error } = await q;
  if (error) throw mapDbError(error, "listTransactions");
  return (data ?? []).map(mapTx);
}

async function assertOwnPortfolio(sb: SupabaseClient, userId: string, portfolioId: string) {
  const { data, error } = await sb.from("portfolios").select("id").eq("id", portfolioId).eq("user_id", userId).maybeSingle();
  if (error) throw mapDbError(error, "assertOwnPortfolio");
  if (!data) throw new ApiError("not_found", "Portfolio not found.");
}

function assertLedger(txs: PortfolioTransaction[]) {
  const problem = validateLedger(txs);
  if (problem) throw new ApiError("validation_error", problem);
}

export async function addTransaction(sb: SupabaseClient, userId: string, input: TransactionInput): Promise<TransactionRow> {
  await assertOwnPortfolio(sb, userId, input.portfolioId);
  if (!getMarketService().isSupported(input.coinId)) throw new ApiError("validation_error", "This coin is not in our supported coverage.");
  const existing = (await listTransactions(sb, userId, input.portfolioId)).filter((t) => t.coinId === input.coinId);
  const candidate: PortfolioTransaction = {
    id: "__new__",
    coinId: input.coinId,
    type: input.type,
    quantity: input.quantity,
    priceUsd: input.type === "transfer_out" ? (input.priceUsd ?? null) : input.priceUsd!,
    feeUsd: input.feeUsd,
    occurredAt: input.occurredAt,
    createdAt: new Date().toISOString(),
  };
  assertLedger([...existing, candidate]);
  const { data, error } = await sb
    .from("portfolio_transactions")
    .insert({
      portfolio_id: input.portfolioId,
      user_id: userId,
      coin_id: input.coinId,
      type: input.type,
      quantity: input.quantity,
      price_usd: candidate.priceUsd,
      fee_usd: input.feeUsd,
      occurred_at: input.occurredAt,
      notes: input.notes ?? null,
    })
    .select(TX_COLUMNS)
    .single();
  if (error) {
    if (error.code === "23503") throw new ApiError("validation_error", "This coin has not been loaded into the database yet. Run the market refresh job.");
    throw mapDbError(error, "addTransaction");
  }
  return mapTx(data);
}

async function getTransaction(sb: SupabaseClient, userId: string, id: string): Promise<TransactionRow> {
  const { data, error } = await sb.from("portfolio_transactions").select(TX_COLUMNS).eq("id", id).eq("user_id", userId).maybeSingle();
  if (error) throw mapDbError(error, "getTransaction");
  if (!data) throw new ApiError("not_found", "Transaction not found.");
  return mapTx(data);
}

export async function updateTransaction(
  sb: SupabaseClient,
  userId: string,
  id: string,
  patch: Partial<Pick<TransactionRow, "type" | "quantity" | "priceUsd" | "feeUsd" | "occurredAt" | "notes">>,
): Promise<TransactionRow> {
  const current = await getTransaction(sb, userId, id);
  const next: TransactionRow = { ...current, ...patch };
  if (next.type !== "transfer_out" && (next.priceUsd === null || next.priceUsd === undefined)) {
    throw new ApiError("validation_error", "Price is required for this transaction type.");
  }
  const siblings = (await listTransactions(sb, userId, current.portfolioId)).filter((t) => t.coinId === current.coinId && t.id !== id);
  assertLedger([...siblings, next]);
  const { data, error } = await sb
    .from("portfolio_transactions")
    .update({ type: next.type, quantity: next.quantity, price_usd: next.priceUsd, fee_usd: next.feeUsd, occurred_at: next.occurredAt, notes: next.notes })
    .eq("id", id)
    .eq("user_id", userId)
    .select(TX_COLUMNS)
    .single();
  if (error) throw mapDbError(error, "updateTransaction");
  return mapTx(data);
}

export async function deleteTransaction(sb: SupabaseClient, userId: string, id: string) {
  const current = await getTransaction(sb, userId, id);
  const remaining = (await listTransactions(sb, userId, current.portfolioId)).filter((t) => t.coinId === current.coinId && t.id !== id);
  const problem = validateLedger(remaining);
  if (problem) throw new ApiError("validation_error", `Deleting this transaction would leave the ledger invalid: ${problem}`);
  const { error } = await sb.from("portfolio_transactions").delete().eq("id", id).eq("user_id", userId);
  if (error) throw mapDbError(error, "deleteTransaction");
}

/** Portfolio summaries valued with current (cached) market prices. */
export async function getPortfolioOverview(sb: SupabaseClient, userId: string) {
  const [portfolios, txs] = await Promise.all([listPortfolios(sb, userId), listTransactions(sb, userId)]);
  const svc = getMarketService();
  const markets = await svc.getUniverseMarkets().catch(() => null);
  const prices = new Map<string, number | null>();
  const meta = new Map<string, { name: string; symbol: string; imageUrl: string | null }>();
  for (const m of markets?.data ?? []) {
    prices.set(m.id, m.priceUsd);
    meta.set(m.id, { name: m.name, symbol: m.symbol, imageUrl: m.imageUrl });
  }
  const summaries = portfolios.map((p) => {
    const own = txs.filter((t) => t.portfolioId === p.id);
    try {
      return { portfolio: p, summary: summarizePortfolio(own, prices), error: null as string | null };
    } catch (err) {
      return { portfolio: p, summary: null, error: err instanceof Error ? err.message : "Calculation failed." };
    }
  });
  return {
    portfolios: summaries,
    transactions: txs,
    coinMeta: meta,
    pricing: markets ? { source: markets.source, fetchedAt: markets.fetchedAt, stale: markets.stale } : null,
  };
}
