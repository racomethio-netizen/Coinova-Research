import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import type { AlertInput, AlertType, Channel } from "@/lib/alerts/evaluate";
import { mapDbError } from "@/lib/db/errors";
import { ApiError } from "@/lib/http/api";
import { getMarketService } from "@/lib/market";

export interface AlertRow {
  id: string;
  type: AlertType;
  coinId: string | null;
  portfolioId: string | null;
  threshold: number;
  channels: Channel[];
  triggerMode: "once" | "recurring";
  cooldownMinutes: number;
  isActive: boolean;
  lastTriggeredAt: string | null;
  lastEvaluatedAt: string | null;
  lastObservedValue: number | null;
  createdAt: string;
}

const COLS =
  "id, type, coin_id, portfolio_id, threshold, channels, trigger_mode, cooldown_minutes, is_active, last_triggered_at, last_evaluated_at, last_observed_value, created_at";

export function mapAlert(r: Record<string, unknown>): AlertRow {
  return {
    id: r.id as string,
    type: r.type as AlertType,
    coinId: (r.coin_id as string | null) ?? null,
    portfolioId: (r.portfolio_id as string | null) ?? null,
    threshold: Number(r.threshold),
    channels: r.channels as Channel[],
    triggerMode: r.trigger_mode as AlertRow["triggerMode"],
    cooldownMinutes: Number(r.cooldown_minutes),
    isActive: Boolean(r.is_active),
    lastTriggeredAt: (r.last_triggered_at as string | null) ?? null,
    lastEvaluatedAt: (r.last_evaluated_at as string | null) ?? null,
    lastObservedValue: r.last_observed_value === null || r.last_observed_value === undefined ? null : Number(r.last_observed_value),
    createdAt: r.created_at as string,
  };
}

export async function listAlerts(sb: SupabaseClient, userId: string): Promise<AlertRow[]> {
  const { data, error } = await sb.from("alerts").select(COLS).eq("user_id", userId).order("created_at", { ascending: false });
  if (error) throw mapDbError(error, "listAlerts");
  return (data ?? []).map(mapAlert);
}

export async function createAlert(sb: SupabaseClient, userId: string, input: AlertInput): Promise<AlertRow> {
  if (input.coinId && !getMarketService().isSupported(input.coinId)) throw new ApiError("validation_error", "This coin is not in our supported coverage.");
  const { data, error } = await sb
    .from("alerts")
    .insert({
      user_id: userId,
      type: input.type,
      coin_id: input.coinId ?? null,
      portfolio_id: input.portfolioId ?? null,
      threshold: input.threshold,
      channels: input.channels,
      trigger_mode: input.triggerMode,
      cooldown_minutes: input.cooldownMinutes,
      is_active: input.isActive,
    })
    .select(COLS)
    .single();
  if (error) {
    if (error.code === "42501" || /row-level security/.test(error.message)) throw new ApiError("not_found", "Portfolio not found.");
    throw mapDbError(error, "createAlert");
  }
  return mapAlert(data);
}

export async function updateAlert(
  sb: SupabaseClient,
  userId: string,
  id: string,
  patch: Partial<{ threshold: number; channels: Channel[]; triggerMode: "once" | "recurring"; cooldownMinutes: number; isActive: boolean }>,
): Promise<AlertRow> {
  const update: Record<string, unknown> = {};
  if (patch.threshold !== undefined) update.threshold = patch.threshold;
  if (patch.channels !== undefined) update.channels = patch.channels;
  if (patch.triggerMode !== undefined) update.trigger_mode = patch.triggerMode;
  if (patch.cooldownMinutes !== undefined) update.cooldown_minutes = patch.cooldownMinutes;
  if (patch.isActive !== undefined) update.is_active = patch.isActive;
  const { data, error } = await sb.from("alerts").update(update).eq("id", id).eq("user_id", userId).select(COLS).maybeSingle();
  if (error) throw mapDbError(error, "updateAlert");
  if (!data) throw new ApiError("not_found", "Alert not found.");
  return mapAlert(data);
}

export async function deleteAlert(sb: SupabaseClient, userId: string, id: string) {
  const { data, error } = await sb.from("alerts").delete().eq("id", id).eq("user_id", userId).select("id").maybeSingle();
  if (error) throw mapDbError(error, "deleteAlert");
  if (!data) throw new ApiError("not_found", "Alert not found.");
}

export async function getAlertHistory(sb: SupabaseClient, userId: string, limit = 50) {
  const { data, error } = await sb
    .from("alert_evaluations")
    .select("id, alert_id, evaluated_at, outcome, observed_value, reason, data_as_of")
    .eq("user_id", userId)
    .in("outcome", ["triggered", "skipped"])
    .order("evaluated_at", { ascending: false })
    .limit(limit);
  if (error) throw mapDbError(error, "getAlertHistory");
  return data ?? [];
}
