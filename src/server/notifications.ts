import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { mapDbError } from "@/lib/db/errors";

export interface NotificationRow {
  id: string;
  kind: string;
  title: string;
  body: string;
  createdAt: string;
  readAt: string | null;
  deliveries: { channel: string; status: string; attempts: number; lastError: string | null }[];
}

export async function listNotifications(sb: SupabaseClient, userId: string, limit = 50): Promise<NotificationRow[]> {
  const { data, error } = await sb
    .from("notifications")
    .select("id, kind, title, body, created_at, read_at, notification_deliveries(channel, status, attempts, last_error)")
    .eq("user_id", userId)
    .order("created_at", { ascending: false })
    .limit(limit);
  if (error) throw mapDbError(error, "listNotifications");
  return (data ?? []).map((n) => ({
    id: n.id,
    kind: n.kind,
    title: n.title,
    body: n.body,
    createdAt: n.created_at,
    readAt: n.read_at,
    deliveries: ((n.notification_deliveries as Record<string, unknown>[]) ?? []).map((d) => ({
      channel: d.channel as string,
      status: d.status as string,
      attempts: d.attempts as number,
      lastError: (d.last_error as string | null) ?? null,
    })),
  }));
}

export async function markNotificationsRead(sb: SupabaseClient, userId: string, ids: string[] | "all") {
  let q = sb.from("notifications").update({ read_at: new Date().toISOString() }).eq("user_id", userId).is("read_at", null);
  if (ids !== "all") q = q.in("id", ids);
  const { error } = await q;
  if (error) throw mapDbError(error, "markNotificationsRead");
}

export async function unreadCount(sb: SupabaseClient, userId: string): Promise<number> {
  const { count } = await sb.from("notifications").select("id", { count: "exact", head: true }).eq("user_id", userId).is("read_at", null);
  return count ?? 0;
}
