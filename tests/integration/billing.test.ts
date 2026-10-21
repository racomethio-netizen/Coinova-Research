import type { Client } from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { DevBillingSimulator, DEV_SIGNATURE_HEADER, signDevPayload } from "@/lib/billing/dev-simulator";
import { ApiError } from "@/lib/http/api";
import { processBillingWebhook, requestCancellation, startCheckout } from "@/server/billing";
import { newUser, pg, serviceClient, userClient } from "./helpers";

const sim = new DevBillingSimulator(process.env.DEV_BILLING_WEBHOOK_SECRET!, "http://localhost:3000");
let db: Client;

const send = (signed: { body: string; signature: string }) => processBillingWebhook(sim, signed.body, new Headers({ [DEV_SIGNATURE_HEADER]: signed.signature }), serviceClient());
const tier = async (uid: string) => (await userClient(uid).rpc("current_membership_tier")).data;

beforeAll(async () => {
  db = await pg();
  // Simulator subscriptions are test-mode; allow them to grant access in this dev database.
  await db.query("update public.app_config set value = 'false' where key = 'require_livemode_subscriptions'");
});
afterAll(async () => {
  await db.query("update public.app_config set value = 'true' where key = 'require_livemode_subscriptions'");
  await db.end();
});

describe("billing webhook processing (real database)", () => {
  it("activates a subscription only after a verified webhook", async () => {
    const uid = await newUser(db);
    expect(await tier(uid)).toBe("free");
    const out = await send(sim.buildSignedEvent({ type: "subscription.activated", userId: uid, planId: "premium_monthly", providerSubscriptionId: `sub-${uid}` }));
    expect(out.status).toBe("processed");
    expect(await tier(uid)).toBe("premium");
    const { rows } = await db.query("select kind, amount_cents from public.billing_payments where user_id = $1", [uid]);
    expect(rows).toEqual([{ kind: "payment", amount_cents: 1900 }]);
  });

  it("rejects invalid signatures without touching data", async () => {
    const uid = await newUser(db);
    const signed = sim.buildSignedEvent({ type: "subscription.activated", userId: uid, planId: "founding_yearly", providerSubscriptionId: `sub-${uid}` });
    await expect(processBillingWebhook(sim, signed.body, new Headers({ [DEV_SIGNATURE_HEADER]: signDevPayload("wrong-secret-wrong-secret-wrong-secret", signed.body) }), serviceClient())).rejects.toBeInstanceOf(ApiError);
    await expect(processBillingWebhook(sim, signed.body, new Headers(), serviceClient())).rejects.toBeInstanceOf(ApiError);
    expect(await tier(uid)).toBe("free");
    expect((await db.query("select 1 from public.billing_events where user_id = $1", [uid])).rowCount).toBe(0);
  });

  it("is idempotent for duplicate event deliveries", async () => {
    const uid = await newUser(db);
    const signed = sim.buildSignedEvent({ type: "subscription.activated", userId: uid, planId: "premium_yearly", providerSubscriptionId: `sub-${uid}` });
    expect((await send(signed)).status).toBe("processed");
    expect((await send(signed)).status).toBe("duplicate");
    expect((await db.query("select 1 from public.subscriptions where user_id = $1", [uid])).rowCount).toBe(1);
    expect((await db.query("select 1 from public.billing_payments where user_id = $1", [uid])).rowCount).toBe(1);
  });

  it("ignores delayed out-of-order events", async () => {
    const uid = await newUser(db);
    const sub = `sub-${uid}`;
    const t0 = new Date(Date.now() - 60_000);
    await send(sim.buildSignedEvent({ type: "subscription.activated", userId: uid, planId: "premium_monthly", providerSubscriptionId: sub, occurredAt: t0 }));
    await send(sim.buildSignedEvent({ type: "subscription.renewed", userId: uid, planId: "premium_monthly", providerSubscriptionId: sub, occurredAt: new Date(t0.getTime() + 20_000) }));
    const late = await send(sim.buildSignedEvent({ type: "subscription.payment_failed", userId: uid, planId: "premium_monthly", providerSubscriptionId: sub, occurredAt: new Date(t0.getTime() + 10_000) }));
    expect(late).toEqual({ status: "ignored", reason: "out_of_order" });
    expect((await db.query("select status from public.subscriptions where user_id = $1", [uid])).rows[0].status).toBe("active");
  });

  it("handles failed payment (grace), refund, and expiration", async () => {
    const uid = await newUser(db);
    const sub = `sub-${uid}`;
    await send(sim.buildSignedEvent({ type: "subscription.activated", userId: uid, planId: "premium_monthly", providerSubscriptionId: sub, occurredAt: new Date(Date.now() - 30_000) }));
    await send(sim.buildSignedEvent({ type: "subscription.payment_failed", userId: uid, planId: "premium_monthly", providerSubscriptionId: sub, occurredAt: new Date(Date.now() - 20_000) }));
    expect((await db.query("select status from public.subscriptions where user_id = $1", [uid])).rows[0].status).toBe("past_due");
    expect(await tier(uid)).toBe("premium"); // period not over yet
    await send(sim.buildSignedEvent({ type: "charge.refunded", userId: uid, planId: "premium_monthly", providerSubscriptionId: sub, occurredAt: new Date(Date.now() - 10_000) }));
    expect(await tier(uid)).toBe("free");
    const kinds = (await db.query("select kind from public.billing_payments where user_id = $1 order by occurred_at", [uid])).rows.map((r) => r.kind);
    expect(kinds).toEqual(["payment", "refund"]);
  });

  it("refuses a second checkout while a subscription is open, and cancels at period end", async () => {
    const uid = await newUser(db);
    await send(sim.buildSignedEvent({ type: "subscription.activated", userId: uid, planId: "premium_monthly", providerSubscriptionId: `sub-${uid}`, occurredAt: new Date(Date.now() - 5_000) }));
    await expect(startCheckout(userClient(uid), { id: uid, email: null }, "founding_yearly")).rejects.toMatchObject({ code: "conflict" });
    expect((await requestCancellation(userClient(uid), uid)).status).toBe("scheduled");
    const { rows } = await db.query("select status, cancel_at_period_end from public.subscriptions where user_id = $1", [uid]);
    expect(rows[0]).toEqual({ status: "active", cancel_at_period_end: true });
    expect(await tier(uid)).toBe("premium");
  });

  it("checkout creates a server-side session with the server's plan", async () => {
    const uid = await newUser(db);
    const res = await startCheckout(userClient(uid), { id: uid, email: null }, "premium_yearly");
    expect(res.url).toMatch(/\/dashboard\/membership\/dev-checkout\?session=devcs_/);
    const { rows } = await db.query("select plan_id, status from public.checkout_sessions where user_id = $1", [uid]);
    expect(rows).toEqual([{ plan_id: "premium_yearly", status: "open" }]);
  });

  it("test-mode subscriptions grant nothing once livemode is required (production setting)", async () => {
    const uid = await newUser(db);
    await send(sim.buildSignedEvent({ type: "subscription.activated", userId: uid, planId: "premium_monthly", providerSubscriptionId: `sub-${uid}` }));
    await db.query("update public.app_config set value = 'true' where key = 'require_livemode_subscriptions'");
    try {
      expect(await tier(uid)).toBe("free");
    } finally {
      await db.query("update public.app_config set value = 'false' where key = 'require_livemode_subscriptions'");
    }
  });
});
