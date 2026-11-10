import { randomUUID } from "node:crypto";
import { PaperSummarySchema, ScannerRowSchema, SignalDetailSchema, SignalSummarySchema } from "@nexus/shared";
import type { Client } from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { getScanner, getSignal, listSignals, toScannerRow } from "@/server/nexus";
import { getPaperSummary } from "@/server/paper";
import { newUser, pg, serviceClient, userClient } from "./helpers";

/**
 * Contract tests over real supabase-js -> PostgREST -> Postgres: the server's read
 * models must parse with the shared schemas the mobile app uses.
 */
let db: Client;
let pro: string;
let signalId: string;

beforeAll(async () => {
  db = await pg();
  pro = await newUser(db);
  await db.query(
    "insert into public.subscriptions (user_id, plan_id, tier, status, provider, provider_subscription_id, livemode, current_period_start, current_period_end, last_event_at) values ($1, 'pro_monthly', 'pro', 'active', 'test', $2, true, now() - interval '1 day', now() + interval '30 days', now())",
    [pro, randomUUID()],
  );
  const pair = (await db.query("select id from public.market_pairs where symbol = 'BTCUSDT'")).rows[0].id;
  const strategy = (
    await db.query("insert into public.strategies (key, version, name, description, params) values ('contract_test', '1.0.0', 'Contract', 'x', '{}') returning id")
  ).rows[0].id;
  const features = { close: "100", ema20: "99", ema50: "98", ema200: "90", rsi14: "50", macd_hist: "0.1", atr14: "1", volume_ratio: "1.2", htf: "4h", htf_ema50: "95", htf_ema200: "91" };
  const ev = (
    await db.query(
      "insert into public.signal_evaluations (pair_id, timeframe, strategy_id, as_of, decision, features) values ($1, '1h', $2, date_trunc('hour', now()), 'BUY', $3) returning id",
      [pair, strategy, JSON.stringify(features)],
    )
  ).rows[0].id;
  signalId = randomUUID();
  const prev = (await db.query("select content_hash from public.signals order by seq desc limit 1")).rows[0]?.content_hash ?? null;
  await db.query(
    `insert into public.signals (id, evaluation_id, pair_id, timeframe, strategy_id, direction, as_of, published_at, entry_low, entry_high, stop_loss, take_profits,
      risk_reward, confidence_band, entry_expires_at, horizon_bars, assumptions, explanation, visibility, prev_hash, content_hash)
     values ($1, $2, $3, '1h', $4, 'BUY', date_trunc('hour', now()), now(), 99.5, 100.1, 97, '{104,106}', 1.8, 'not_calibrated', now() + interval '6 hours', 24,
      '{"fee_rate":"0.001","slippage_rate":"0.0005","resolver_version":"1.0.0"}', '{"summary":"Test","reasons":[{"code":"x","detail":"y"}]}', 'public', $5, $6)`,
    [signalId, ev, pair, strategy, prev, "c".repeat(64)],
  );
  await db.query("insert into public.signal_events (signal_id, event_type, occurred_at, price) values ($1, 'entered', now(), 100)", [signalId]);
});

afterAll(async () => {
  await db?.end();
});

describe("NEXUS read models match the shared API contract", () => {
  it("signals list and detail (Pro user, public signal)", async () => {
    const list = await listSignals(userClient(pro));
    const mine = list.find((s) => s.id === signalId);
    expect(mine).toBeDefined();
    expect(SignalSummarySchema.parse(mine)).toMatchObject({ direction: "BUY", takeProfits: ["104.00000000", "106.00000000"], outcome: null });
    const detail = await getSignal(userClient(pro), signalId);
    expect(SignalDetailSchema.parse(detail).events).toHaveLength(1);
  });

  it("free users do not see a live public signal", async () => {
    const free = await newUser(db);
    expect((await listSignals(userClient(free))).some((s) => s.id === signalId)).toBe(false);
  });

  it("scanner rows (anonymous)", async () => {
    const rows = (await getScanner(serviceClient())).map(toScannerRow);
    expect(rows.length).toBeGreaterThan(0);
    for (const r of rows) ScannerRowSchema.parse(r);
  });

  it("paper summary before and after opening an account", async () => {
    const c = userClient(pro);
    expect(PaperSummarySchema.parse(await getPaperSummary(c, pro)).account).toBeNull();
    const { error } = await c.rpc("open_paper_account", { p_starting_balance: "2500" });
    expect(error).toBeNull();
    const s = PaperSummarySchema.parse(await getPaperSummary(c, pro));
    expect(s.account?.cash).toBe("2500.00000000");
  });
});
