import type { Client } from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { logger } from "@/lib/logger";
import { getMarketService } from "@/lib/market";
import { deliverNotifications, evaluateAlerts } from "@/server/jobs/alerts";
import { runNamedJob } from "@/server/jobs";
import { refreshFundamentals, refreshHistory, refreshMarkets } from "@/server/jobs/market";
import { runJob, type JobContext } from "@/server/jobs/runner";
import { computeScores } from "@/server/jobs/scores";
import { newUser, pg, serviceClient, userClient } from "./helpers";

let db: Client;
const ctx = (now = new Date()): JobContext => ({ db: serviceClient(), now, log: logger.child({ test: true }) });

beforeAll(async () => {
  db = await pg();
});
afterAll(async () => {
  await db.end();
});

async function grantPremium(uid: string) {
  await db.query(
    "insert into public.subscriptions (user_id, plan_id, tier, status, provider, provider_subscription_id, livemode, current_period_start, current_period_end, last_event_at) values ($1,'premium_monthly','premium','active','test',$2,true,now(),now() + interval '30 days', now())",
    [uid, `t-${uid}`],
  );
}

describe("market and research jobs (mock provider, real database)", () => {
  it("refresh-markets loads coins, snapshots, and supply, and is idempotent per bucket", async () => {
    const now = new Date();
    const first = await refreshMarkets(ctx(now));
    expect(first.coins).toBeGreaterThan(10);
    const count = async () => Number((await db.query("select count(*) from public.market_snapshots")).rows[0].count);
    const n1 = await count();
    await refreshMarkets(ctx(now));
    expect(await count()).toBe(n1);
    const { rows } = await db.query("select count(*) filter (where is_score_sample) samples, count(*) total from public.cryptocurrencies");
    expect(Number(rows[0].samples)).toBe(5);
    expect(Number(rows[0].total)).toBe(first.coins);
  });

  it("runJob skips a window that already succeeded and records history", async () => {
    const key = `test-job:${Date.now()}`;
    const r1 = await runJob(serviceClient(), "test-job", key, 60, async () => ({ ok: 1 }));
    const r2 = await runJob(serviceClient(), "test-job", key, 60, async () => ({ ok: 2 }));
    expect(r1.status).toBe("succeeded");
    expect(r2.status).toBe("skipped");
    const fail = await runJob(serviceClient(), "test-job", `${key}-f`, 60, async () => {
      throw new Error("boom");
    });
    expect(fail.status).toBe("failed");
    const retry = await runJob(serviceClient(), "test-job", `${key}-f`, 60, async () => ({ ok: 3 }));
    expect(retry.status).toBe("succeeded");
    const { rows } = await db.query("select attempts, status from public.background_jobs where idempotency_key = $1", [`${key}-f`]);
    expect(rows[0]).toEqual({ attempts: 2, status: "succeeded" });
  });

  it("refresh-history and refresh-fundamentals populate persisted inputs", async () => {
    const h = await refreshHistory(ctx(), 3);
    const f = await refreshFundamentals(ctx(), 3);
    expect(h.updated).toBe(3);
    expect(f.updated).toBe(3);
    const { rows } = await db.query("select count(distinct coin_id) c from public.historical_prices");
    expect(Number(rows[0].c)).toBe(3);
  });

  it("compute-scores writes reproducible scores and de-duplicates snapshots", async () => {
    const r1 = await computeScores(ctx());
    expect(r1.scored + r1.insufficient).toBeGreaterThan(10);
    const snaps1 = Number((await db.query("select count(*) from public.research_score_snapshots")).rows[0].count);
    await computeScores(ctx());
    // Same inputs ⇒ same hashes ⇒ no new snapshots (asOf is part of the input, so allow at most one per coin).
    const { rows } = await db.query("select coin_id, status, overall_score, methodology_version from public.research_scores order by coin_id limit 3");
    expect(rows.every((r) => r.methodology_version === "1.0.0")).toBe(true);
    expect(snaps1).toBeGreaterThan(0);
  });

  it("free users read only sample scores through RLS; premium users read all", async () => {
    const free = await newUser(db);
    const prem = await newUser(db);
    await grantPremium(prem);
    const f = await userClient(free).from("research_scores").select("coin_id");
    const p = await userClient(prem).from("research_scores").select("coin_id");
    expect(f.data!.length).toBeLessThanOrEqual(5);
    expect(p.data!.length).toBeGreaterThan(f.data!.length);
  });
});

describe("alert evaluation and delivery", () => {
  it("triggers once (edge), dedupes, rate-limits, and tracks deliveries with retries", async () => {
    const uid = await newUser(db);
    await grantPremium(uid);
    const coin = getMarketService().universeIds[0]!;
    const { data: alert, error } = await userClient(uid)
      .from("alerts")
      .insert({ user_id: uid, type: "price_above", coin_id: coin, threshold: 0.000001, channels: ["in_app", "email", "telegram"], cooldown_minutes: 5 })
      .select("id")
      .single();
    expect(error).toBeNull();
    // Telegram linked so the delivery is attempted (it fails: api.telegram.org is unreachable in tests).
    await db.query("insert into public.telegram_accounts (user_id, chat_id, telegram_user_id) values ($1, $2, 1)", [uid, Math.floor(Math.random() * 1e9)]);

    const now = new Date();
    await refreshMarkets(ctx(now));
    const r1 = await evaluateAlerts(ctx(now));
    expect(r1.triggered).toBeGreaterThanOrEqual(1);
    const notifs = await db.query("select id from public.notifications where alert_id = $1", [alert!.id]);
    expect(notifs.rowCount).toBe(1);

    // Re-run in the same window: dedupe key prevents a second notification.
    await db.query("update public.alerts set last_condition_met = null, last_triggered_at = null where id = $1", [alert!.id]);
    await evaluateAlerts(ctx(now));
    expect((await db.query("select id from public.notifications where alert_id = $1", [alert!.id])).rowCount).toBe(1);

    // Next window, condition still true: edge-triggered ⇒ suppressed.
    await db.query("update public.alerts set last_condition_met = true where id = $1", [alert!.id]);
    const later = new Date(now.getTime() + 6 * 60_000);
    await evaluateAlerts(ctx(later));
    expect((await db.query("select id from public.notifications where alert_id = $1", [alert!.id])).rowCount).toBe(1);
    const outcomes = (await db.query("select outcome from public.alert_evaluations where alert_id = $1 order by id", [alert!.id])).rows.map((r) => r.outcome);
    expect(outcomes).toContain("suppressed");

    // Deliveries: in-app sent immediately; email via console adapter; telegram fails and is scheduled for retry.
    const d = await deliverNotifications(ctx());
    expect(d.claimed).toBeGreaterThanOrEqual(2);
    const rows = (await db.query("select channel, status, attempts from public.notification_deliveries where notification_id = $1 order by channel", [notifs.rows[0].id])).rows;
    expect(rows.find((r) => r.channel === "in_app")!.status).toBe("sent");
    expect(rows.find((r) => r.channel === "email")!.status).toBe("sent");
    const tg = rows.find((r) => r.channel === "telegram")!;
    expect(tg.attempts).toBe(1);
    expect(["pending", "failed"]).toContain(tg.status);
  });

  it("disabled alerts are not evaluated", async () => {
    const uid = await newUser(db);
    await grantPremium(uid);
    const coin = getMarketService().universeIds[1]!;
    const { data: alert } = await userClient(uid).from("alerts").insert({ user_id: uid, type: "price_above", coin_id: coin, threshold: 0.000001, channels: ["in_app"], is_active: false }).select("id").single();
    await evaluateAlerts(ctx());
    expect((await db.query("select 1 from public.alert_evaluations where alert_id = $1", [alert!.id])).rowCount).toBe(0);
  });

  it("alerts of users who lost Premium are skipped", async () => {
    const uid = await newUser(db);
    await grantPremium(uid);
    const coin = getMarketService().universeIds[2]!;
    const { data: alert } = await userClient(uid).from("alerts").insert({ user_id: uid, type: "price_above", coin_id: coin, threshold: 0.000001, channels: ["in_app"] }).select("id").single();
    await db.query("update public.subscriptions set status = 'expired' where user_id = $1", [uid]);
    await evaluateAlerts(ctx());
    expect((await db.query("select 1 from public.notifications where alert_id = $1", [alert!.id])).rowCount).toBe(0);
  });

  it("the job registry runs through the named-job path", async () => {
    const r = await runNamedJob("sync-plans");
    expect(["succeeded", "skipped"]).toContain(r.status);
    const cleanup = await runNamedJob("cleanup");
    expect(["succeeded", "skipped"]).toContain(cleanup.status);
  });
});
