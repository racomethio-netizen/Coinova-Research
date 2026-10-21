import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { getPaidPlan } from "@/config/plans";
import { DevBillingSimulator } from "@/lib/billing/dev-simulator";
import { applyBillingEvent } from "@/lib/billing/state";
import { WebhookVerificationError, type BillingProvider, type NormalizedBillingEvent, type SubscriptionRecord } from "@/lib/billing/types";
import { mapDbError } from "@/lib/db/errors";
import { getEnv, isProduction } from "@/lib/env";
import { ApiError } from "@/lib/http/api";
import { logger } from "@/lib/logger";
import { requireServiceClient } from "@/lib/supabase/server";

/**
 * Billing service.
 *
 * SECURITY: Entitlements change ONLY inside `processBillingWebhook`, after the
 * provider adapter has verified the webhook signature. Browser redirects
 * ("payment succeeded" pages) never grant access.
 */

export function getBillingProvider(): BillingProvider | null {
  const env = getEnv();
  switch (env.BILLING_PROVIDER) {
    case "dev_simulator":
      if (isProduction(env)) return null; // defense in depth; env validation already forbids this
      return new DevBillingSimulator(env.DEV_BILLING_WEBHOOK_SECRET!, env.NEXT_PUBLIC_SITE_URL);
    case "none":
      return null;
  }
}

export function getDevSimulator(): DevBillingSimulator | null {
  const p = getBillingProvider();
  return p instanceof DevBillingSimulator ? p : null;
}

const OPEN_STATUSES = ["incomplete", "trialing", "active", "past_due"];

function rowToRecord(r: Record<string, unknown>): SubscriptionRecord {
  return {
    userId: r.user_id as string,
    planId: r.plan_id as string,
    tier: r.tier as SubscriptionRecord["tier"],
    status: r.status as SubscriptionRecord["status"],
    provider: r.provider as string,
    providerSubscriptionId: r.provider_subscription_id as string,
    providerCustomerId: (r.provider_customer_id as string | null) ?? null,
    livemode: Boolean(r.livemode),
    currentPeriodStart: (r.current_period_start as string | null) ?? null,
    currentPeriodEnd: (r.current_period_end as string | null) ?? null,
    cancelAtPeriodEnd: Boolean(r.cancel_at_period_end),
    canceledAt: (r.canceled_at as string | null) ?? null,
    endedAt: (r.ended_at as string | null) ?? null,
    lastEventAt: r.last_event_at as string,
  };
}

function recordToRow(s: SubscriptionRecord) {
  return {
    user_id: s.userId,
    plan_id: s.planId,
    tier: s.tier,
    status: s.status,
    provider: s.provider,
    provider_subscription_id: s.providerSubscriptionId,
    provider_customer_id: s.providerCustomerId,
    livemode: s.livemode,
    current_period_start: s.currentPeriodStart,
    current_period_end: s.currentPeriodEnd,
    cancel_at_period_end: s.cancelAtPeriodEnd,
    canceled_at: s.canceledAt,
    ended_at: s.endedAt,
    last_event_at: s.lastEventAt,
  };
}

// ------------------------------------------------------------------ checkout

export async function startCheckout(userSb: SupabaseClient, user: { id: string; email: string | null }, planId: string) {
  const provider = getBillingProvider();
  if (!provider) throw new ApiError("not_configured", "Paid memberships are not available yet: no billing provider is configured.");
  const plan = getPaidPlan(planId);
  if (!plan) throw new ApiError("validation_error", "Unknown plan.");

  const { data: open, error } = await userSb.from("subscriptions").select("id, status").eq("user_id", user.id).in("status", OPEN_STATUSES);
  if (error) throw mapDbError(error, "startCheckout.open");
  if (open && open.length > 0) throw new ApiError("conflict", "You already have an active subscription. Manage it from your membership page.");

  const site = getEnv().NEXT_PUBLIC_SITE_URL;
  const session = await provider.createCheckout({
    userId: user.id,
    userEmail: user.email,
    planId: plan.id,
    successUrl: new URL("/dashboard/membership?checkout=returned", site).toString(),
    cancelUrl: new URL("/pricing?checkout=canceled", site).toString(),
  });
  const svc = requireServiceClient();
  const ins = await svc.from("checkout_sessions").insert({
    user_id: user.id,
    plan_id: plan.id,
    provider: session.provider,
    provider_session_id: session.sessionId,
    expires_at: session.expiresAt,
  });
  if (ins.error) throw mapDbError(ins.error, "startCheckout.insert");
  return { url: session.url, provider: session.provider };
}

// ------------------------------------------------------------------ webhook

export type WebhookOutcome = { status: "processed" | "ignored" | "duplicate"; reason?: string };

export async function processBillingWebhook(provider: BillingProvider, rawBody: string, headers: Headers, db: SupabaseClient = requireServiceClient()): Promise<WebhookOutcome> {
  let ev: NormalizedBillingEvent;
  try {
    ev = await provider.parseWebhook(rawBody, headers);
  } catch (err) {
    if (err instanceof WebhookVerificationError) {
      logger.warn("billing_webhook_rejected", { provider: provider.id, reason: err.message });
      throw new ApiError("bad_request", "Invalid webhook.");
    }
    throw err;
  }
  if (ev.livemode !== provider.livemode) {
    logger.warn("billing_webhook_livemode_mismatch", { provider: provider.id, eventId: ev.eventId });
    throw new ApiError("bad_request", "Invalid webhook.");
  }

  // 1. Record the event (idempotent on provider + event id).
  const inserted = await db
    .from("billing_events")
    .upsert(
      {
        provider: ev.provider,
        provider_event_id: ev.eventId,
        event_type: ev.type,
        occurred_at: ev.occurredAt,
        livemode: ev.livemode,
        user_id: ev.userId,
        payload: ev,
      },
      { onConflict: "provider,provider_event_id", ignoreDuplicates: true },
    )
    .select("id");
  if (inserted.error) throw mapDbError(inserted.error, "billing.recordEvent");

  const { data: eventRow, error: evErr } = await db.from("billing_events").select("id, status").eq("provider", ev.provider).eq("provider_event_id", ev.eventId).single();
  if (evErr) throw mapDbError(evErr, "billing.loadEvent");
  if (eventRow.status === "processed" || eventRow.status === "ignored") {
    logger.info("billing_webhook_duplicate", { eventId: ev.eventId });
    return { status: "duplicate" };
  }

  const finish = async (status: "processed" | "ignored" | "failed", note: string | null, subscriptionId: string | null = null) => {
    await db.from("billing_events").update({ status, processing_note: note, processed_at: new Date().toISOString(), subscription_id: subscriptionId }).eq("id", eventRow.id);
  };

  try {
    // 2. Apply to the subscription state machine.
    const { data: existing, error: subErr } = await db
      .from("subscriptions")
      .select("*")
      .eq("provider", ev.provider)
      .eq("provider_subscription_id", ev.providerSubscriptionId)
      .maybeSingle();
    if (subErr) throw mapDbError(subErr, "billing.loadSubscription");
    const current = existing ? rowToRecord(existing) : null;
    const result = applyBillingEvent(current, ev);
    if (result.action === "ignore") {
      await finish("ignored", result.reason, existing?.id ?? null);
      logger.info("billing_event_ignored", { eventId: ev.eventId, reason: result.reason });
      return { status: "ignored", reason: result.reason };
    }

    let subscriptionId: string;
    if (existing) {
      // Optimistic concurrency: only update if no newer event was applied in between.
      const { data, error } = await db
        .from("subscriptions")
        .update(recordToRow(result.next))
        .eq("id", existing.id)
        .eq("last_event_at", existing.last_event_at)
        .select("id")
        .maybeSingle();
      if (error) throw mapDbError(error, "billing.updateSubscription");
      if (!data) {
        await db.from("billing_events").update({ status: "received", processing_note: "concurrent update; retry" }).eq("id", eventRow.id);
        throw new ApiError("conflict", "Concurrent update; please retry.");
      }
      subscriptionId = existing.id;
    } else {
      const { data, error } = await db.from("subscriptions").insert(recordToRow(result.next)).select("id").single();
      if (error) {
        if (error.code === "23505") {
          await finish("failed", "user already has an open subscription");
          logger.error("billing_duplicate_open_subscription", { eventId: ev.eventId, userId: ev.userId });
          return { status: "ignored", reason: "duplicate_open_subscription" };
        }
        throw mapDbError(error, "billing.insertSubscription");
      }
      subscriptionId = data.id;
    }

    // 3. User-visible payment history.
    const kind =
      ev.type === "subscription.activated" || ev.type === "subscription.renewed"
        ? "payment"
        : ev.type === "charge.refunded"
          ? "refund"
          : ev.type === "subscription.payment_failed"
            ? "failed_payment"
            : null;
    if (kind && ev.amountCents !== null && ev.currency) {
      await db.from("billing_payments").upsert(
        {
          user_id: ev.userId,
          subscription_id: subscriptionId,
          billing_event_id: eventRow.id,
          plan_id: ev.planId,
          amount_cents: ev.amountCents,
          currency: ev.currency,
          kind,
          livemode: ev.livemode,
          occurred_at: ev.occurredAt,
        },
        { onConflict: "billing_event_id", ignoreDuplicates: true },
      );
    }
    if (ev.type === "subscription.activated") {
      await db.from("checkout_sessions").update({ status: "completed" }).eq("user_id", ev.userId).eq("plan_id", ev.planId).eq("status", "open");
    }
    await finish("processed", null, subscriptionId);
    logger.info("billing_event_processed", { eventId: ev.eventId, type: ev.type, status: result.next.status });
    return { status: "processed" };
  } catch (err) {
    if (!(err instanceof ApiError && err.code === "conflict")) {
      await finish("failed", err instanceof Error ? err.message.slice(0, 400) : "unknown error");
    }
    throw err;
  }
}

// ------------------------------------------------------------------ reads

export async function getMembership(userSb: SupabaseClient, userId: string) {
  const [subs, payments] = await Promise.all([
    userSb.from("subscriptions").select("*").eq("user_id", userId).order("created_at", { ascending: false }).limit(20),
    userSb.from("billing_payments").select("id, plan_id, amount_cents, currency, kind, livemode, occurred_at").eq("user_id", userId).order("occurred_at", { ascending: false }).limit(50),
  ]);
  if (subs.error) throw mapDbError(subs.error, "getMembership.subs");
  if (payments.error) throw mapDbError(payments.error, "getMembership.payments");
  return {
    subscriptions: (subs.data ?? []).map((r) => ({ id: r.id as string, ...rowToRecord(r) })),
    payments: payments.data ?? [],
  };
}

/**
 * Cancel at period end. Real providers confirm via webhook; the simulator
 * emits a signed cancellation event through the same webhook path.
 */
export async function requestCancellation(userSb: SupabaseClient, userId: string) {
  const { data, error } = await userSb.from("subscriptions").select("*").eq("user_id", userId).in("status", ["active", "trialing", "past_due"]).maybeSingle();
  if (error) throw mapDbError(error, "requestCancellation");
  if (!data) throw new ApiError("not_found", "No active subscription to cancel.");
  const sub = rowToRecord(data);
  if (sub.cancelAtPeriodEnd) return { status: "already_scheduled" as const };
  const provider = getBillingProvider();
  if (!provider || provider.id !== sub.provider) throw new ApiError("not_configured", "The billing provider for this subscription is not available. Contact support.");
  if (provider instanceof DevBillingSimulator) {
    const signed = provider.buildSignedEvent({ type: "subscription.canceled", userId, planId: sub.planId, providerSubscriptionId: sub.providerSubscriptionId, cancelAtPeriodEnd: true });
    await processBillingWebhook(provider, signed.body, new Headers({ "x-dev-billing-signature": signed.signature }));
    return { status: "scheduled" as const };
  }
  await provider.cancelSubscription(sub.providerSubscriptionId);
  return { status: "requested" as const };
}
