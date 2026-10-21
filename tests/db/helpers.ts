import { randomUUID } from "node:crypto";
import { Client } from "pg";
import { adminUrl, TEST_DB_NAME } from "./global-setup";

/**
 * `superuser` performs fixture setup (bypasses RLS like the service role).
 * `as()` runs statements inside a transaction under a Supabase role with JWT
 * claims, then rolls back, so each check is isolated.
 */
export async function connect(): Promise<Client> {
  const c = new Client({ connectionString: adminUrl(TEST_DB_NAME) });
  await c.connect();
  return c;
}

export type Actor = { role: "anon" } | { role: "authenticated"; userId: string } | { role: "service_role" };

export async function as<T>(db: Client, actor: Actor, fn: (q: Client["query"]) => Promise<T>): Promise<T> {
  await db.query("begin");
  try {
    await db.query(`set local role ${actor.role}`);
    const claims = actor.role === "authenticated" ? { sub: actor.userId, role: "authenticated" } : { role: actor.role };
    await db.query("select set_config('request.jwt.claims', $1, true)", [JSON.stringify(claims)]);
    return await fn(db.query.bind(db) as Client["query"]);
  } finally {
    await db.query("rollback");
  }
}

/** Expect a statement to fail; returns the error message. */
export async function expectError(p: Promise<unknown>): Promise<string> {
  try {
    await p;
  } catch (err) {
    return (err as Error).message;
  }
  throw new Error("Expected statement to fail, but it succeeded");
}

export async function createUser(db: Client, email = `${randomUUID()}@test.local`): Promise<string> {
  const { rows } = await db.query<{ id: string }>("insert into auth.users (email) values ($1) returning id", [email]);
  return rows[0]!.id;
}

export async function grantSubscription(
  db: Client,
  userId: string,
  opts: { tier?: "premium" | "founding"; status?: string; periodEnd?: string; livemode?: boolean } = {},
) {
  const tier = opts.tier ?? "premium";
  await db.query(
    `insert into public.subscriptions (user_id, plan_id, tier, status, provider, provider_subscription_id, livemode, current_period_start, current_period_end, last_event_at)
     values ($1, $2, $3, $4, 'test', $5, $6, now() - interval '1 day', $7, now())`,
    [userId, tier === "premium" ? "premium_monthly" : "founding_yearly", tier, opts.status ?? "active", randomUUID(), opts.livemode ?? true, opts.periodEnd ?? new Date(Date.now() + 30 * 86_400_000).toISOString()],
  );
}

export async function seedCoin(db: Client, id: string, sample = false) {
  await db.query(
    "insert into public.cryptocurrencies (id, symbol, name, is_score_sample) values ($1, upper($1), $1, $2) on conflict (id) do nothing",
    [id, sample],
  );
}
