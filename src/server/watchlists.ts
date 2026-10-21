import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { mapDbError } from "@/lib/db/errors";
import { ApiError } from "@/lib/http/api";
import { getMarketService } from "@/lib/market";

export interface Watchlist {
  id: string;
  name: string;
  createdAt: string;
  coinIds: string[];
}

export async function listWatchlists(sb: SupabaseClient, userId: string): Promise<Watchlist[]> {
  const { data, error } = await sb.from("watchlists").select("id, name, created_at, watchlist_items(coin_id, added_at)").eq("user_id", userId).order("created_at");
  if (error) throw mapDbError(error, "listWatchlists");
  return (data ?? []).map((w) => ({
    id: w.id,
    name: w.name,
    createdAt: w.created_at,
    coinIds: ((w.watchlist_items as { coin_id: string; added_at: string }[]) ?? []).sort((a, b) => a.added_at.localeCompare(b.added_at)).map((i) => i.coin_id),
  }));
}

function assertSupported(coinId: string) {
  if (!getMarketService().isSupported(coinId)) throw new ApiError("validation_error", "This coin is not in our supported coverage.");
}

export async function createWatchlist(sb: SupabaseClient, userId: string, name: string, coinIds: string[] = []): Promise<Watchlist> {
  coinIds.forEach(assertSupported);
  const { data, error } = await sb.from("watchlists").insert({ user_id: userId, name }).select("id, name, created_at").single();
  if (error) throw mapDbError(error, "createWatchlist");
  for (const coinId of coinIds) await addItem(sb, userId, data.id, coinId);
  return { id: data.id, name: data.name, createdAt: data.created_at, coinIds };
}

export async function renameWatchlist(sb: SupabaseClient, userId: string, id: string, name: string) {
  const { data, error } = await sb.from("watchlists").update({ name }).eq("id", id).eq("user_id", userId).select("id").maybeSingle();
  if (error) throw mapDbError(error, "renameWatchlist");
  if (!data) throw new ApiError("not_found", "Watchlist not found.");
}

export async function deleteWatchlist(sb: SupabaseClient, userId: string, id: string) {
  const { data, error } = await sb.from("watchlists").delete().eq("id", id).eq("user_id", userId).select("id").maybeSingle();
  if (error) throw mapDbError(error, "deleteWatchlist");
  if (!data) throw new ApiError("not_found", "Watchlist not found.");
}

export async function addItem(sb: SupabaseClient, userId: string, watchlistId: string, coinId: string) {
  assertSupported(coinId);
  const { data: wl } = await sb.from("watchlists").select("id").eq("id", watchlistId).eq("user_id", userId).maybeSingle();
  if (!wl) throw new ApiError("not_found", "Watchlist not found.");
  const { error } = await sb.from("watchlist_items").insert({ watchlist_id: watchlistId, coin_id: coinId, user_id: userId });
  if (error) {
    if (error.code === "23505") return; // already present: idempotent
    if (error.code === "23503") throw new ApiError("validation_error", "This coin has not been loaded into the database yet.");
    throw mapDbError(error, "addWatchlistItem");
  }
}

export async function removeItem(sb: SupabaseClient, userId: string, watchlistId: string, coinId: string) {
  const { error } = await sb.from("watchlist_items").delete().eq("watchlist_id", watchlistId).eq("coin_id", coinId).eq("user_id", userId);
  if (error) throw mapDbError(error, "removeWatchlistItem");
}
