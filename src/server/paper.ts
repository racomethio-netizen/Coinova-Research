import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { mapDbError } from "@/lib/db/errors";
import { valueAccount } from "@/lib/paper/summary";

/** Everything the paper-trading page needs, read with the user's own session (RLS). */
export async function getPaperSummary(supabase: SupabaseClient, userId: string) {
  const { data: account, error } = await supabase
    .from("paper_accounts")
    .select("id, name, quote_asset, starting_balance, cash_balance, created_at")
    .eq("user_id", userId)
    .eq("status", "active")
    .maybeSingle();
  if (error) throw mapDbError(error, "paper account");
  if (!account) return { virtual: true as const, account: null };
  const [positions, orders, fills, snapshots, prices, pairs] = await Promise.all([
    supabase.from("paper_positions").select("pair_id, quantity, cost_basis, realized_pnl, stop_loss, take_profit").eq("account_id", account.id),
    supabase.from("paper_orders").select("*").eq("account_id", account.id).order("created_at", { ascending: false }).limit(50),
    supabase.from("paper_fills").select("*").eq("account_id", account.id).order("created_at", { ascending: false }).limit(50),
    supabase.from("paper_equity_snapshots").select("taken_at, equity, cash").eq("account_id", account.id).order("taken_at").limit(24 * 30),
    supabase.from("latest_prices").select("pair_id, price, fetched_at"),
    supabase.from("market_pairs").select("id, symbol"),
  ]);
  for (const r of [positions, orders, fills, snapshots, prices, pairs]) if (r.error) throw mapDbError(r.error, "paper summary");
  const symbols = new Map((pairs.data ?? []).map((p) => [p.id as number, p.symbol as string]));
  const valued = valueAccount(
    String(account.cash_balance),
    (positions.data ?? []).map((p) => ({
      pairId: p.pair_id, symbol: symbols.get(p.pair_id) ?? "?", quantity: String(p.quantity), costBasis: String(p.cost_basis),
      realizedPnl: String(p.realized_pnl), stopLoss: p.stop_loss === null ? null : String(p.stop_loss), takeProfit: p.take_profit === null ? null : String(p.take_profit),
    })),
    (prices.data ?? []).map((p) => ({ pairId: p.pair_id, price: String(p.price), fetchedAt: p.fetched_at })),
  );
  return {
    virtual: true as const,
    account: { ...account, ...valued },
    orders: orders.data,
    fills: fills.data,
    equityHistory: snapshots.data,
  };
}
