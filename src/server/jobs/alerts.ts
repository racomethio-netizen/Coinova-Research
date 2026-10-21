import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { siteConfig } from "@/config/site";
import { afterAttempt } from "@/lib/alerts/delivery";
import { ALERT_TYPE_LABELS, describeAlert, evaluateAlert, type AlertContext, type AlertType } from "@/lib/alerts/evaluate";
import { getEnv } from "@/lib/env";
import { fmtUsd } from "@/lib/format";
import { summarizePortfolio } from "@/lib/portfolio/calc";
import { getEmailProvider } from "@/lib/notify/email";
import { sendTelegramMessage } from "@/lib/notify/telegram";
import { mapAlert } from "@/server/alerts";
import type { JobContext } from "./runner";

/** Market observations older than this are not used to evaluate alerts. */
const MAX_DATA_AGE_MINUTES = 30;

interface Snapshot {
  coin_id: string;
  captured_at: string;
  price_usd: string | null;
  change_24h_pct: number | null;
  volume_24h_usd: string | null;
}

async function tierLimit(db: SupabaseClient, cache: Map<string, number>, userId: string, key: string) {
  const k = `${userId}:${key}`;
  if (!cache.has(k)) {
    const { data } = await db.rpc("tier_limit_for", { uid: userId, limit_key: key });
    cache.set(k, typeof data === "number" ? data : 0);
  }
  return cache.get(k)!;
}

/**
 * evaluate-alerts (every 5 minutes, after refresh-markets).
 * Writes an evaluation record for every alert, updates edge-trigger state,
 * and creates at most one notification per alert window (dedupe_key unique).
 */
export async function evaluateAlerts({ db, now, log }: JobContext) {
  const { data: alertRows, error } = await db.from("alerts").select("*, user_id").eq("is_active", true).limit(10_000);
  if (error) throw new Error(error.message);
  if (!alertRows?.length) return { evaluated: 0, triggered: 0 };

  const coinIds = [...new Set(alertRows.map((a) => a.coin_id).filter(Boolean))] as string[];
  const [latest, dayAgo, scores, unlocks, coins] = await Promise.all([
    db.from("latest_market_snapshots").select("coin_id, captured_at, price_usd, change_24h_pct, volume_24h_usd").in("coin_id", coinIds),
    db
      .from("market_snapshots")
      .select("coin_id, captured_at, volume_24h_usd")
      .in("coin_id", coinIds)
      .gte("captured_at", new Date(now.getTime() - 25 * 3_600_000).toISOString())
      .lte("captured_at", new Date(now.getTime() - 23 * 3_600_000).toISOString())
      .order("captured_at"),
    db.from("research_scores").select("coin_id, overall_score, previous_overall_score, calculated_at").in("coin_id", coinIds),
    db.from("token_unlock_events").select("coin_id, unlock_at").in("coin_id", coinIds).eq("status", "scheduled").neq("source_type", "estimated").gte("unlock_at", now.toISOString()).order("unlock_at"),
    db.from("cryptocurrencies").select("id, name").in("id", coinIds),
  ]);
  const snap = new Map<string, Snapshot>((latest.data ?? []).map((s) => [s.coin_id, s as Snapshot]));
  const volPrev = new Map<string, number>();
  for (const s of dayAgo.data ?? []) if (!volPrev.has(s.coin_id) && s.volume_24h_usd !== null) volPrev.set(s.coin_id, Number(s.volume_24h_usd));
  const scoreBy = new Map((scores.data ?? []).map((s) => [s.coin_id, s]));
  const nextUnlock = new Map<string, string>();
  for (const u of unlocks.data ?? []) if (!nextUnlock.has(u.coin_id)) nextUnlock.set(u.coin_id, u.unlock_at);
  const coinName = new Map((coins.data ?? []).map((c) => [c.id, c.name]));

  const limitCache = new Map<string, number>();
  const portfolioValue = new Map<string, { value: number | null; at: string | null }>();
  let evaluated = 0;
  let triggered = 0;
  let rateLimited = 0;

  for (const row of alertRows) {
    const alert = mapAlert(row);
    const userId = row.user_id as string;
    // Users who lost their entitlement keep their alerts but they are not evaluated.
    if ((await tierLimit(db, limitCache, userId, "alerts")) === 0) continue;

    const obs: AlertContext["observation"] = { value: null, observedAt: null };
    const s = alert.coinId ? snap.get(alert.coinId) : undefined;
    switch (alert.type as AlertType) {
      case "price_above":
      case "price_below":
        obs.value = s?.price_usd != null ? Number(s.price_usd) : null;
        obs.observedAt = s?.captured_at ?? null;
        break;
      case "pct_change_24h_above":
      case "pct_change_24h_below":
        obs.value = s?.change_24h_pct ?? null;
        obs.observedAt = s?.captured_at ?? null;
        break;
      case "volume_change_24h_above": {
        const prev = alert.coinId ? volPrev.get(alert.coinId) : undefined;
        const cur = s?.volume_24h_usd != null ? Number(s.volume_24h_usd) : null;
        obs.value = prev && cur !== null && prev > 0 ? ((cur - prev) / prev) * 100 : null;
        obs.observedAt = s?.captured_at ?? null;
        break;
      }
      case "score_change": {
        const sc = alert.coinId ? scoreBy.get(alert.coinId) : undefined;
        obs.value = sc && sc.overall_score !== null && sc.previous_overall_score !== null ? Number(sc.overall_score) - Number(sc.previous_overall_score) : null;
        // Scores are hourly; treat the score as current for 2 hours.
        obs.observedAt = sc?.calculated_at && now.getTime() - Date.parse(sc.calculated_at) < 2 * 3_600_000 ? now.toISOString() : (sc?.calculated_at ?? null);
        break;
      }
      case "unlock_upcoming":
        obs.nextEventAt = alert.coinId ? (nextUnlock.get(alert.coinId) ?? null) : null;
        obs.observedAt = now.toISOString();
        break;
      case "portfolio_value_above":
      case "portfolio_value_below": {
        const pid = alert.portfolioId!;
        if (!portfolioValue.has(pid)) portfolioValue.set(pid, await valuePortfolio(db, pid, snap, now));
        const pv = portfolioValue.get(pid)!;
        obs.value = pv.value;
        obs.observedAt = pv.at;
        break;
      }
    }

    const result = evaluateAlert(
      { ...alert, lastConditionMet: (row.last_condition_met as boolean | null) ?? null },
      { now: now.toISOString(), maxDataAgeMinutes: alert.type === "score_change" ? 180 : MAX_DATA_AGE_MINUTES, observation: obs },
    );
    let outcome = result.outcome;
    let reason = result.reason;

    if (result.outcome === "triggered" && result.dedupeKey) {
      const perHour = await tierLimit(db, limitCache, userId, "notificationsPerHour");
      const { count } = await db.from("notifications").select("id", { count: "exact", head: true }).eq("user_id", userId).gte("created_at", new Date(now.getTime() - 3_600_000).toISOString());
      if ((count ?? 0) >= perHour) {
        outcome = "suppressed";
        reason = "Hourly notification limit reached.";
        rateLimited++;
      } else {
        const label = alert.coinId ? coinName.get(alert.coinId) ?? alert.coinId : "Portfolio";
        const valueText =
          result.observedValue === null
            ? ""
            : alert.type.startsWith("price") || alert.type.startsWith("portfolio")
              ? ` Current value: ${fmtUsd(result.observedValue)}.`
              : ` Current value: ${result.observedValue.toFixed(2)}.`;
        const title = `${label}: ${ALERT_TYPE_LABELS[alert.type].toLowerCase()} ${alert.type.startsWith("price") || alert.type.startsWith("portfolio") ? fmtUsd(alert.threshold) : alert.threshold}`;
        const body = `${describeAlert(alert.type, alert.threshold, label)} — condition met.${valueText} Data as of ${obs.observedAt ?? "unknown"} (UTC). Market data may be delayed. This is not financial advice.`;
        const { data: notif, error: nErr } = await db
          .from("notifications")
          .upsert({ user_id: userId, alert_id: alert.id, kind: "alert", title: title.slice(0, 200), body: body.slice(0, 2000), data: { alertId: alert.id, observedValue: result.observedValue }, dedupe_key: result.dedupeKey }, { onConflict: "dedupe_key", ignoreDuplicates: true })
          .select("id");
        if (nErr) throw new Error(nErr.message);
        const notificationId = notif?.[0]?.id as string | undefined;
        if (notificationId) {
          triggered++;
          await db.from("notification_deliveries").upsert(
            alert.channels.map((channel) => ({
              notification_id: notificationId,
              user_id: userId,
              channel,
              status: channel === "in_app" ? "sent" : "pending",
              sent_at: channel === "in_app" ? now.toISOString() : null,
              attempts: channel === "in_app" ? 1 : 0,
            })),
            { onConflict: "notification_id,channel", ignoreDuplicates: true },
          );
        }
      }
    }

    await db.from("alert_evaluations").insert({
      alert_id: alert.id,
      user_id: userId,
      evaluated_at: now.toISOString(),
      outcome,
      observed_value: result.observedValue,
      reason,
      data_as_of: obs.observedAt,
    });
    const update: Record<string, unknown> = { last_evaluated_at: now.toISOString(), last_observed_value: result.observedValue };
    if (result.conditionMet !== null && outcome !== "skipped") update.last_condition_met = outcome === "suppressed" && reason.startsWith("Hourly") ? false : result.conditionMet;
    if (outcome === "triggered") update.last_triggered_at = now.toISOString();
    if (outcome === "triggered" && result.deactivate) update.is_active = false;
    await db.from("alerts").update(update).eq("id", alert.id);
    evaluated++;
  }
  if (rateLimited) log.warn("alert_notifications_rate_limited", { rateLimited });
  return { evaluated, triggered, rateLimited };
}

async function valuePortfolio(db: SupabaseClient, portfolioId: string, snap: Map<string, Snapshot>, now: Date) {
  const { data: txs } = await db.from("portfolio_transactions").select("id, coin_id, type, quantity, price_usd, fee_usd, occurred_at, created_at").eq("portfolio_id", portfolioId);
  if (!txs?.length) return { value: 0, at: now.toISOString() };
  const ids = [...new Set(txs.map((t) => t.coin_id))];
  const missing = ids.filter((id) => !snap.has(id));
  if (missing.length) {
    const { data } = await db.from("latest_market_snapshots").select("coin_id, captured_at, price_usd, change_24h_pct, volume_24h_usd").in("coin_id", missing);
    for (const s of data ?? []) snap.set(s.coin_id, s as Snapshot);
  }
  const prices = new Map<string, number | null>(ids.map((id) => [id, snap.get(id)?.price_usd != null ? Number(snap.get(id)!.price_usd) : null]));
  try {
    const summary = summarizePortfolio(
      txs.map((t) => ({ id: t.id, coinId: t.coin_id, type: t.type, quantity: String(t.quantity), priceUsd: t.price_usd === null ? null : String(t.price_usd), feeUsd: String(t.fee_usd), occurredAt: t.occurred_at, createdAt: t.created_at })),
      prices,
    );
    // If any holding is unpriced the total would be understated; treat as missing data.
    if (summary.unpricedCoinIds.length) return { value: null, at: null };
    const oldest = ids.map((id) => snap.get(id)?.captured_at).filter(Boolean).sort()[0] ?? null;
    return { value: summary.totalMarketValue.toNumber(), at: oldest };
  } catch {
    return { value: null, at: null };
  }
}

/**
 * deliver-notifications (every minute): claim due deliveries atomically,
 * send, and record the outcome with bounded exponential-backoff retries.
 */
export async function deliverNotifications({ db, now, log }: JobContext, batch = 50) {
  const { data: claimed, error } = await db.rpc("claim_due_deliveries", { batch_size: batch, lock_seconds: 120 });
  if (error) throw new Error(error.message);
  const email = getEmailProvider();
  let sent = 0;
  let failed = 0;
  let retrying = 0;
  for (const d of (claimed ?? []) as { id: string; notification_id: string; user_id: string; channel: string; attempts: number }[]) {
    const { data: n } = await db.from("notifications").select("title, body").eq("id", d.notification_id).single();
    let outcome: { ok: true } | { ok: false; error: string; permanent?: boolean };
    if (!n) outcome = { ok: false, error: "notification missing", permanent: true };
    else if (d.channel === "email") {
      const [{ data: profile }, { data: user }] = await Promise.all([db.from("profiles").select("email_notifications").eq("id", d.user_id).maybeSingle(), db.auth.admin.getUserById(d.user_id)]);
      if (profile && profile.email_notifications === false) {
        await db.from("notification_deliveries").update({ status: "skipped", last_error: "Email notifications disabled by user.", locked_until: null }).eq("id", d.id);
        continue;
      }
      const to = user?.user?.email;
      outcome = to
        ? await email.send({ to, subject: `[${siteConfig.shortName}] ${n.title}`, text: `${n.body}\n\nManage alerts: ${new URL("/dashboard/alerts", getEnv().NEXT_PUBLIC_SITE_URL)}` })
        : { ok: false, error: "no email on account", permanent: true };
    } else if (d.channel === "telegram") {
      const { data: tg } = await db.from("telegram_accounts").select("chat_id").eq("user_id", d.user_id).maybeSingle();
      outcome = tg ? await sendTelegramMessage(Number(tg.chat_id), `${n.title}\n\n${n.body}`) : { ok: false, error: "Telegram not linked", permanent: true };
    } else {
      outcome = { ok: true };
    }
    const next = afterAttempt(d.attempts, outcome, now.getTime());
    await db
      .from("notification_deliveries")
      .update({
        status: next.status,
        attempts: next.attempts,
        next_attempt_at: next.nextAttemptAt ?? now.toISOString(),
        last_error: next.lastError,
        locked_until: null,
        sent_at: next.status === "sent" ? new Date().toISOString() : null,
        provider_message_id: outcome.ok && "messageId" in outcome ? String(outcome.messageId) : null,
      })
      .eq("id", d.id);
    if (next.status === "sent") sent++;
    else if (next.status === "failed") failed++;
    else retrying++;
  }
  if (failed) log.warn("notification_deliveries_failed", { failed });
  return { claimed: claimed?.length ?? 0, sent, failed, retrying };
}
