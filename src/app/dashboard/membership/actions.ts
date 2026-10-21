"use server";

import { randomUUID } from "node:crypto";
import { redirect } from "next/navigation";
import { z } from "zod";
import { DEV_SIGNATURE_HEADER } from "@/lib/billing/dev-simulator";
import type { BillingEventType } from "@/lib/billing/types";
import { requireUserPage } from "@/lib/auth/session";
import { logger } from "@/lib/logger";
import { getDevSimulator, processBillingWebhook } from "@/server/billing";

/**
 * DEVELOPMENT-ONLY billing simulator actions. Each builds a signed event and
 * sends it through the same verification + processing path as a real
 * provider webhook. Unavailable unless BILLING_PROVIDER=dev_simulator (which
 * env validation forbids in production).
 */

function simulator() {
  const sim = getDevSimulator();
  if (!sim) throw new Error("Billing simulator is not enabled.");
  return sim;
}

export async function simulateCheckoutPayment(form: FormData) {
  const sim = simulator();
  const viewer = await requireUserPage("/dashboard/membership");
  const sessionId = z.string().regex(/^devcs_[0-9a-f-]{36}$/).parse(form.get("session"));
  const { data: session } = await viewer.supabase
    .from("checkout_sessions")
    .select("plan_id, status, expires_at")
    .eq("provider", "dev_simulator")
    .eq("provider_session_id", sessionId)
    .eq("user_id", viewer.user.id)
    .maybeSingle();
  if (!session || session.status !== "open" || Date.parse(session.expires_at) < Date.now()) redirect("/dashboard/membership?checkout=expired");

  const signed = sim.buildSignedEvent({ type: "subscription.activated", userId: viewer.user.id, planId: session.plan_id, providerSubscriptionId: `devsub_${randomUUID()}` });
  const outcome = await processBillingWebhook(sim, signed.body, new Headers({ [DEV_SIGNATURE_HEADER]: signed.signature }));
  logger.info("dev_billing_checkout_simulated", { outcome: outcome.status });
  redirect(`/dashboard/membership?checkout=${outcome.status === "processed" ? "simulated" : "failed"}`);
}

const LIFECYCLE: Record<string, BillingEventType> = {
  renew: "subscription.renewed",
  fail: "subscription.payment_failed",
  expire: "subscription.expired",
  refund: "charge.refunded",
  cancel_now: "subscription.canceled",
};

export async function simulateLifecycleEvent(form: FormData) {
  const sim = simulator();
  const viewer = await requireUserPage("/dashboard/membership");
  const action = z.enum(["renew", "fail", "expire", "refund", "cancel_now"]).parse(form.get("event"));
  const { data: sub } = await viewer.supabase
    .from("subscriptions")
    .select("plan_id, provider_subscription_id")
    .eq("user_id", viewer.user.id)
    .eq("provider", "dev_simulator")
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  if (!sub) redirect("/dashboard/membership?simulated=none");
  const signed = sim.buildSignedEvent({ type: LIFECYCLE[action]!, userId: viewer.user.id, planId: sub.plan_id, providerSubscriptionId: sub.provider_subscription_id });
  await processBillingWebhook(sim, signed.body, new Headers({ [DEV_SIGNATURE_HEADER]: signed.signature }));
  redirect(`/dashboard/membership?simulated=${action}`);
}
