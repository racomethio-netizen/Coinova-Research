import { randomUUID } from "node:crypto";
import type { Client } from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { PAID_PLANS, PLAN_FEATURES, TIER_LIMITS } from "@/config/plans";
import { as, connect, createUser, expectError, grantSubscription, seedCoin } from "./helpers";

let db: Client;
let alice: string; // premium
let bob: string; // premium
let carol: string; // free
let admin: string;
let alicePortfolio: string;
let bobPortfolio: string;
let bobTx: string;

beforeAll(async () => {
  db = await connect();
  alice = await createUser(db, "alice@test.local");
  bob = await createUser(db, "bob@test.local");
  carol = await createUser(db, "carol@test.local");
  admin = await createUser(db, "admin@test.local");
  await db.query("insert into public.user_roles (user_id, role) values ($1, 'admin')", [admin]);
  await grantSubscription(db, alice);
  await grantSubscription(db, bob);
  await seedCoin(db, "bitcoin", true);
  await seedCoin(db, "ethereum", false);

  alicePortfolio = (await db.query("insert into public.portfolios (user_id, name) values ($1, 'Main') returning id", [alice])).rows[0].id;
  bobPortfolio = (await db.query("insert into public.portfolios (user_id, name) values ($1, 'Main') returning id", [bob])).rows[0].id;
  bobTx = (
    await db.query(
      "insert into public.portfolio_transactions (portfolio_id, user_id, coin_id, type, quantity, price_usd, occurred_at) values ($1, $2, 'bitcoin', 'buy', 1, 100, now() - interval '1 day') returning id",
      [bobPortfolio, bob],
    )
  ).rows[0].id;
});

afterAll(async () => {
  await db?.end();
});

describe("profiles", () => {
  it("are created automatically on signup", async () => {
    const { rowCount } = await db.query("select 1 from public.profiles where id = $1", [alice]);
    expect(rowCount).toBe(1);
  });

  it("users read and update only their own profile", async () => {
    await as(db, { role: "authenticated", userId: alice }, async (q) => {
      const { rows } = await q("select id from public.profiles");
      expect(rows.map((r) => r.id)).toEqual([alice]);
      const upd = await q("update public.profiles set display_name = 'hacked' where id = $1", [bob]);
      expect(upd.rowCount).toBe(0);
    });
  });

  it("users cannot change protected profile columns", async () => {
    const msg = await as(db, { role: "authenticated", userId: alice }, (q) => expectError(q("update public.profiles set created_at = now() where id = $1", [alice])));
    expect(msg).toMatch(/permission denied/);
  });

  it("anon cannot read profiles", async () => {
    const msg = await as(db, { role: "anon" }, (q) => expectError(q("select * from public.profiles")));
    expect(msg).toMatch(/permission denied/);
  });
});

describe("role escalation", () => {
  it("users cannot grant themselves admin", async () => {
    const msg = await as(db, { role: "authenticated", userId: alice }, (q) => expectError(q("insert into public.user_roles (user_id, role) values ($1, 'admin')", [alice])));
    expect(msg).toMatch(/permission denied/);
  });

  it("users cannot modify existing role rows", async () => {
    const msg = await as(db, { role: "authenticated", userId: alice }, (q) => expectError(q("update public.user_roles set user_id = $1", [alice])));
    expect(msg).toMatch(/permission denied/);
  });

  it("is_admin reflects the database role, not client claims", async () => {
    const r1 = await as(db, { role: "authenticated", userId: alice }, (q) => q("select public.is_admin() as a"));
    expect(r1.rows[0].a).toBe(false);
    const r2 = await as(db, { role: "authenticated", userId: admin, aal: "aal2" }, (q) => q("select public.is_admin() as a"));
    expect(r2.rows[0].a).toBe(true);
  });

  it("users can only see their own role rows; admins see all", async () => {
    const mine = await as(db, { role: "authenticated", userId: alice }, (q) => q("select * from public.user_roles"));
    expect(mine.rowCount).toBe(0);
    const all = await as(db, { role: "authenticated", userId: admin, aal: "aal2" }, (q) => q("select * from public.user_roles"));
    expect(all.rowCount).toBeGreaterThanOrEqual(1);
  });
});

describe("membership cannot be self-granted", () => {
  it("users cannot insert or update subscriptions", async () => {
    const ins = await as(db, { role: "authenticated", userId: carol }, (q) =>
      expectError(
        q(
          "insert into public.subscriptions (user_id, plan_id, tier, status, provider, provider_subscription_id, livemode, current_period_end, last_event_at) values ($1,'premium_yearly','premium','active','x','y',true, now() + interval '1 year', now())",
          [carol],
        ),
      ),
    );
    expect(ins).toMatch(/permission denied/);
    const upd = await as(db, { role: "authenticated", userId: alice }, (q) => expectError(q("update public.subscriptions set tier = 'premium'")));
    expect(upd).toMatch(/permission denied/);
  });

  it("users cannot query another user's tier via internal functions", async () => {
    const msg = await as(db, { role: "authenticated", userId: carol }, (q) => expectError(q("select public.membership_tier_for($1)", [alice])));
    expect(msg).toMatch(/permission denied/);
  });

  it("users cannot change entitlement configuration", async () => {
    const r = await as(db, { role: "authenticated", userId: carol }, (q) => q("update public.app_config set value = 'false' where key = 'require_livemode_subscriptions'"));
    expect(r.rowCount).toBe(0);
  });

  it("users cannot write billing events", async () => {
    const msg = await as(db, { role: "authenticated", userId: carol }, (q) =>
      expectError(q("insert into public.billing_events (provider, provider_event_id, event_type, occurred_at, livemode, payload) values ('x','y','z',now(),true,'{}')")),
    );
    expect(msg).toMatch(/permission denied/);
  });

  it("current_membership_tier reports correct tiers", async () => {
    const t = async (uid: string) => (await as(db, { role: "authenticated", userId: uid }, (q) => q("select public.current_membership_tier() t"))).rows[0].t;
    expect(await t(alice)).toBe("premium");
    expect(await t(carol)).toBe("free");
    const anon = await as(db, { role: "anon" }, (q) => q("select public.current_membership_tier() t"));
    expect(anon.rows[0].t).toBe("free");
  });
});

describe("subscription lifecycle in SQL entitlements", () => {
  const tierOf = async (uid: string) => (await as(db, { role: "authenticated", userId: uid }, (q) => q("select public.current_membership_tier() t"))).rows[0].t;

  it("expired period grants free", async () => {
    const u = await createUser(db);
    await grantSubscription(db, u, { periodEnd: new Date(Date.now() - 86_400_000).toISOString() });
    expect(await tierOf(u)).toBe("free");
  });

  it("past_due keeps access only during the grace period", async () => {
    const inGrace = await createUser(db);
    await grantSubscription(db, inGrace, { status: "past_due", periodEnd: new Date(Date.now() - 86_400_000).toISOString() });
    expect(await tierOf(inGrace)).toBe("premium");
    const pastGrace = await createUser(db);
    await grantSubscription(db, pastGrace, { status: "past_due", periodEnd: new Date(Date.now() - 5 * 86_400_000).toISOString() });
    expect(await tierOf(pastGrace)).toBe("free");
  });

  it("canceled and refunded subscriptions grant free", async () => {
    for (const status of ["canceled", "refunded", "expired"]) {
      const u = await createUser(db);
      await grantSubscription(db, u, { status });
      expect(await tierOf(u)).toBe("free");
    }
  });

  it("test-mode (simulator) subscriptions grant nothing while livemode is required", async () => {
    const u = await createUser(db);
    await grantSubscription(db, u, { livemode: false });
    expect(await tierOf(u)).toBe("free");
    await db.query("update public.app_config set value = 'false' where key = 'require_livemode_subscriptions'");
    try {
      expect(await tierOf(u)).toBe("premium");
    } finally {
      await db.query("update public.app_config set value = 'true' where key = 'require_livemode_subscriptions'");
    }
  });

  it("only one open subscription per user", async () => {
    const msg = await expectError(grantSubscription(db, alice));
    expect(msg).toMatch(/subscriptions_one_open_per_user/);
  });
});

describe("portfolio isolation", () => {
  it("users cannot read another user's portfolios or transactions", async () => {
    await as(db, { role: "authenticated", userId: alice }, async (q) => {
      expect((await q("select * from public.portfolios where id = $1", [bobPortfolio])).rowCount).toBe(0);
      expect((await q("select * from public.portfolio_transactions where id = $1", [bobTx])).rowCount).toBe(0);
      expect((await q("select * from public.portfolios")).rows.map((r) => r.id)).toEqual([alicePortfolio]);
    });
  });

  it("users cannot add transactions to another user's portfolio", async () => {
    const msg = await as(db, { role: "authenticated", userId: alice }, (q) =>
      expectError(
        q("insert into public.portfolio_transactions (portfolio_id, user_id, coin_id, type, quantity, price_usd, occurred_at) values ($1, $2, 'bitcoin', 'buy', 1, 1, now())", [bobPortfolio, alice]),
      ),
    );
    expect(msg).toMatch(/row-level security/);
  });

  it("users cannot impersonate another user_id", async () => {
    const msg = await as(db, { role: "authenticated", userId: alice }, (q) =>
      expectError(
        q("insert into public.portfolio_transactions (portfolio_id, user_id, coin_id, type, quantity, price_usd, occurred_at) values ($1, $2, 'bitcoin', 'buy', 1, 1, now())", [bobPortfolio, bob]),
      ),
    );
    expect(msg).toMatch(/row-level security/);
  });

  it("users cannot update, move, or delete another user's transactions", async () => {
    await as(db, { role: "authenticated", userId: alice }, async (q) => {
      expect((await q("update public.portfolio_transactions set quantity = 999 where id = $1", [bobTx])).rowCount).toBe(0);
      expect((await q("delete from public.portfolio_transactions where id = $1", [bobTx])).rowCount).toBe(0);
      expect((await q("delete from public.portfolios where id = $1", [bobPortfolio])).rowCount).toBe(0);
    });
    const { rows } = await db.query("select quantity from public.portfolio_transactions where id = $1", [bobTx]);
    expect(Number(rows[0].quantity)).toBe(1);
  });

  it("users cannot move their own transaction into someone else's portfolio", async () => {
    await as(db, { role: "authenticated", userId: alice }, async (q) => {
      const tx = (await q("insert into public.portfolio_transactions (portfolio_id, coin_id, type, quantity, price_usd, occurred_at) values ($1, 'bitcoin', 'buy', 1, 1, now()) returning id", [alicePortfolio])).rows[0].id;
      const msg = await expectError(q("update public.portfolio_transactions set portfolio_id = $1 where id = $2", [bobPortfolio, tx]));
      expect(msg).toMatch(/row-level security/);
    });
  });

  it("anon cannot read portfolios", async () => {
    const msg = await as(db, { role: "anon" }, (q) => expectError(q("select * from public.portfolios")));
    expect(msg).toMatch(/permission denied/);
  });

  it("database rejects invalid quantities and future dates", async () => {
    await as(db, { role: "authenticated", userId: alice }, async (q) => {
      expect(await expectError(q("insert into public.portfolio_transactions (portfolio_id, coin_id, type, quantity, price_usd, occurred_at) values ($1, 'bitcoin', 'buy', -1, 1, now())", [alicePortfolio]))).toMatch(/check/);
    });
    await as(db, { role: "authenticated", userId: alice }, async (q) => {
      expect(await expectError(q("insert into public.portfolio_transactions (portfolio_id, coin_id, type, quantity, price_usd, occurred_at) values ($1, 'bitcoin', 'buy', 1, 1, now() + interval '1 day')", [alicePortfolio]))).toMatch(/future/);
    });
    await as(db, { role: "authenticated", userId: alice }, async (q) => {
      expect(await expectError(q("insert into public.portfolio_transactions (portfolio_id, coin_id, type, quantity, occurred_at) values ($1, 'bitcoin', 'buy', 1, now())", [alicePortfolio]))).toMatch(/check/);
    });
  });
});

describe("tier limits enforced in the database", () => {
  it("free users cannot create portfolios", async () => {
    const msg = await as(db, { role: "authenticated", userId: carol }, (q) => expectError(q("insert into public.portfolios (name) values ('Mine')")));
    expect(msg).toMatch(/limit_reached:portfolios/);
  });

  it("free users get one watchlist", async () => {
    await as(db, { role: "authenticated", userId: carol }, async (q) => {
      await q("insert into public.watchlists (name) values ('One')");
      expect(await expectError(q("insert into public.watchlists (name) values ('Two')"))).toMatch(/limit_reached:watchlists/);
    });
  });

  it("free users cannot create alerts", async () => {
    const msg = await as(db, { role: "authenticated", userId: carol }, (q) =>
      expectError(q("insert into public.alerts (type, coin_id, threshold, channels) values ('price_above', 'bitcoin', 1, '{in_app}')")),
    );
    expect(msg).toMatch(/limit_reached:alerts/);
  });

  it("premium users can create alerts but not edit evaluation state", async () => {
    await as(db, { role: "authenticated", userId: alice }, async (q) => {
      const id = (await q("insert into public.alerts (type, coin_id, threshold, channels) values ('price_above', 'bitcoin', 1, '{in_app}') returning id")).rows[0].id;
      await q("update public.alerts set threshold = 2 where id = $1", [id]);
      expect(await expectError(q("update public.alerts set last_triggered_at = now() where id = $1", [id]))).toMatch(/permission denied/);
    });
  });

  it("users cannot attach alerts to another user's portfolio", async () => {
    const msg = await as(db, { role: "authenticated", userId: alice }, (q) =>
      expectError(q("insert into public.alerts (type, portfolio_id, threshold, channels) values ('portfolio_value_above', $1, 1, '{in_app}')", [bobPortfolio])),
    );
    expect(msg).toMatch(/row-level security/);
  });

  it("alerts reject unknown channels", async () => {
    const msg = await as(db, { role: "authenticated", userId: alice }, (q) =>
      expectError(q("insert into public.alerts (type, coin_id, threshold, channels) values ('price_above', 'bitcoin', 1, '{sms}')")),
    );
    expect(msg).toMatch(/check/);
  });

  it("users cannot add items to another user's watchlist", async () => {
    const wl = (await db.query("insert into public.watchlists (user_id, name) values ($1, 'Bob list') returning id", [bob])).rows[0].id;
    const msg = await as(db, { role: "authenticated", userId: alice }, (q) =>
      expectError(q("insert into public.watchlist_items (watchlist_id, coin_id) values ($1, 'bitcoin')", [wl])),
    );
    expect(msg).toMatch(/row-level security/);
  });
});

describe("premium research content", () => {
  let freeArticle: string;
  let premiumArticle: string;
  let draftArticle: string;

  beforeAll(async () => {
    const mk = async (slug: string, access: string, status: string) => {
      const id = (
        await db.query(
          "insert into public.research_articles (slug, title, summary, category, access_level, status, author_name, published_at) values ($1, $1, 'A summary of the article.', 'market', $2, $3, 'Staff', case when $3 = 'published' then now() - interval '1 hour' end) returning id",
          [slug, access, status],
        )
      ).rows[0].id;
      await db.query("insert into public.research_article_bodies (article_id, body_markdown) values ($1, $2)", [id, `body of ${slug}`]);
      return id;
    };
    freeArticle = await mk("free-one", "free", "published");
    premiumArticle = await mk("premium-one", "premium", "published");
    draftArticle = await mk("draft-one", "free", "draft");
  });

  const bodies = async (actor: Parameters<typeof as>[1]) =>
    (await as(db, actor, (q) => q("select article_id from public.research_article_bodies"))).rows.map((r) => r.article_id).sort();

  it("anon and free users read free bodies only", async () => {
    expect(await bodies({ role: "anon" })).toEqual([freeArticle]);
    expect(await bodies({ role: "authenticated", userId: carol })).toEqual([freeArticle]);
  });

  it("premium users read premium bodies", async () => {
    expect(await bodies({ role: "authenticated", userId: alice })).toEqual([freeArticle, premiumArticle].sort());
  });

  it("drafts are invisible to non-staff, visible to staff", async () => {
    const anon = await as(db, { role: "anon" }, (q) => q("select id from public.research_articles where id = $1", [draftArticle]));
    expect(anon.rowCount).toBe(0);
    expect(await bodies({ role: "authenticated", userId: admin, aal: "aal2" })).toContain(draftArticle);
  });

  it("premium article metadata (teaser) is public", async () => {
    const r = await as(db, { role: "anon" }, (q) => q("select title from public.research_articles where id = $1", [premiumArticle]));
    expect(r.rowCount).toBe(1);
  });

  it("non-staff cannot publish or edit articles", async () => {
    const r = await as(db, { role: "authenticated", userId: alice }, (q) => q("update public.research_articles set status = 'archived' where id = $1", [freeArticle]));
    expect(r.rowCount).toBe(0);
    const msg = await as(db, { role: "authenticated", userId: alice }, (q) =>
      expectError(q("insert into public.research_articles (slug, title, summary, category, author_name) values ('x-y', 'Title', 'Summary text here', 'market', 'me')")),
    );
    expect(msg).toMatch(/row-level security/);
  });
});

describe("research scores", () => {
  beforeAll(async () => {
    for (const coin of ["bitcoin", "ethereum"]) {
      await db.query(
        "insert into public.research_scores (coin_id, methodology_version, status, overall_score, coverage, subscores, inputs_hash, calculated_at) values ($1, '1.0.0', 'scored', 60, 1, '[]', 'h', now())",
        [coin],
      );
      await db.query(
        "insert into public.research_score_snapshots (coin_id, methodology_version, status, overall_score, coverage, subscores, inputs, inputs_hash, calculated_at) values ($1, '1.0.0', 'scored', 60, 1, '[]', '{}', 'h', now())",
        [coin],
      );
    }
  });

  it("free users see sample scores only; premium see all", async () => {
    const free = await as(db, { role: "anon" }, (q) => q("select coin_id from public.research_scores order by coin_id"));
    expect(free.rows.map((r) => r.coin_id)).toEqual(["bitcoin"]);
    const prem = await as(db, { role: "authenticated", userId: alice }, (q) => q("select coin_id from public.research_scores order by coin_id"));
    expect(prem.rows.map((r) => r.coin_id)).toEqual(["bitcoin", "ethereum"]);
  });

  it("score history is premium-only", async () => {
    const free = await as(db, { role: "authenticated", userId: carol }, (q) => q("select * from public.research_score_snapshots"));
    expect(free.rowCount).toBe(0);
    const prem = await as(db, { role: "authenticated", userId: alice }, (q) => q("select * from public.research_score_snapshots"));
    expect(prem.rowCount).toBe(2);
  });

  it("only admins can publish scoring configs", async () => {
    const msg = await as(db, { role: "authenticated", userId: alice }, (q) => expectError(q("select public.publish_scoring_config('1.0.0', '{}', null)")));
    expect(msg).toMatch(/forbidden/);
    const ok = await as(db, { role: "authenticated", userId: admin, aal: "aal2" }, (q) => q("select public.publish_scoring_config('1.0.1', '{}', 'test') v"));
    expect(ok.rows[0].v).toBeGreaterThan(0);
  });

  it("users cannot write scores or market data", async () => {
    await as(db, { role: "authenticated", userId: alice }, async (q) => {
      expect(await expectError(q("update public.research_scores set overall_score = 100"))).toMatch(/permission denied/);
    });
    await as(db, { role: "anon" }, async (q) => {
      expect(await expectError(q("insert into public.market_snapshots (coin_id, source, captured_at, price_usd) values ('bitcoin','coingecko',now(),1)"))).toMatch(/permission denied/);
    });
  });
});

describe("notifications", () => {
  it("users can mark their notifications read but not alter content", async () => {
    const id = (await db.query("insert into public.notifications (user_id, kind, title, body, dedupe_key) values ($1, 'system', 'Hi', 'Body', 'k1') returning id", [alice])).rows[0].id;
    await as(db, { role: "authenticated", userId: alice }, async (q) => {
      expect((await q("update public.notifications set read_at = now() where id = $1", [id])).rowCount).toBe(1);
    });
    await as(db, { role: "authenticated", userId: alice }, async (q) => {
      expect(await expectError(q("update public.notifications set title = 'x' where id = $1", [id]))).toMatch(/permission denied/);
    });
    await as(db, { role: "authenticated", userId: bob }, async (q) => {
      expect((await q("select * from public.notifications where id = $1", [id])).rowCount).toBe(0);
    });
  });

  it("dedupe_key prevents duplicate notifications", async () => {
    await db.query("insert into public.notifications (user_id, kind, title, body, dedupe_key) values ($1, 'alert', 'A', 'B', 'alert:x:1')", [alice]);
    const msg = await expectError(db.query("insert into public.notifications (user_id, kind, title, body, dedupe_key) values ($1, 'alert', 'A', 'B', 'alert:x:1')", [alice]));
    expect(msg).toMatch(/duplicate key/);
  });
});

describe("operations", () => {
  it("audit log is append-only even for privileged roles", async () => {
    await db.query("insert into public.admin_audit_logs (actor_id, action) values ($1, 'test.action')", [admin]);
    expect(await expectError(db.query("delete from public.admin_audit_logs"))).toMatch(/append-only/);
    expect(await expectError(db.query("update public.admin_audit_logs set action = 'x'"))).toMatch(/append-only/);
  });

  it("staff can append audit entries only as themselves", async () => {
    const msg = await as(db, { role: "authenticated", userId: admin, aal: "aal2" }, (q) => expectError(q("insert into public.admin_audit_logs (actor_id, action) values ($1, 'forged')", [alice])));
    expect(msg).toMatch(/row-level security/);
  });

  it("non-admins cannot read billing events or jobs; admins can", async () => {
    await db.query("insert into public.billing_events (provider, provider_event_id, event_type, occurred_at, livemode, payload) values ('t','e1','x',now(),false,'{}')");
    const user = await as(db, { role: "authenticated", userId: alice }, (q) => q("select * from public.billing_events"));
    expect(user.rowCount).toBe(0);
    const adm = await as(db, { role: "authenticated", userId: admin, aal: "aal2" }, (q) => q("select * from public.billing_events"));
    expect(adm.rowCount).toBeGreaterThanOrEqual(1);
  });

  it("billing events are unique per provider event id (idempotent webhooks)", async () => {
    const msg = await expectError(db.query("insert into public.billing_events (provider, provider_event_id, event_type, occurred_at, livemode, payload) values ('t','e1','x',now(),false,'{}')"));
    expect(msg).toMatch(/duplicate key/);
  });

  it("AI quota is enforced atomically per UTC day", async () => {
    // Free tier: 3/day
    for (let i = 0; i < 3; i++) {
      await db.query("begin");
      await db.query("set local role authenticated");
      await db.query("select set_config('request.jwt.claims', $1, true)", [JSON.stringify({ sub: carol, role: "authenticated" })]);
      await db.query("select public.reserve_ai_request('summary')");
      await db.query("commit");
    }
    const msg = await as(db, { role: "authenticated", userId: carol }, (q) => expectError(q("select public.reserve_ai_request('summary')")));
    expect(msg).toMatch(/limit_reached:aiRequestsPerDay/);
    const anon = await as(db, { role: "anon" }, (q) => expectError(q("select public.reserve_ai_request('summary')")));
    expect(anon).toMatch(/permission denied/);
  });

  it("acquire_job is idempotent per key and re-runnable after failure", async () => {
    const first = (await db.query("select public.acquire_job('refresh-markets', 'k-1', 60) id")).rows[0].id;
    expect(first).toBeTruthy();
    const dup = (await db.query("select public.acquire_job('refresh-markets', 'k-1', 60) id")).rows[0].id;
    expect(dup).toBeNull();
    await db.query("update public.background_jobs set status = 'succeeded' where id = $1", [first]);
    expect((await db.query("select public.acquire_job('refresh-markets', 'k-1', 60) id")).rows[0].id).toBeNull();
    const k2 = (await db.query("select public.acquire_job('refresh-markets', 'k-2', 60) id")).rows[0].id;
    await db.query("update public.background_jobs set status = 'failed' where id = $1", [k2]);
    const retry = (await db.query("select public.acquire_job('refresh-markets', 'k-2', 60) id")).rows[0].id;
    expect(retry).toBe(k2);
    const { rows } = await db.query("select attempts from public.background_jobs where id = $1", [k2]);
    expect(rows[0].attempts).toBe(2);
  });

  it("clients cannot call service-only functions", async () => {
    for (const sql of ["select public.acquire_job('x-y', 'k', 1)", "select public.run_retention_cleanup()", "select * from public.claim_due_deliveries(1, 1)"]) {
      const msg = await as(db, { role: "authenticated", userId: admin, aal: "aal2" }, (q) => expectError(q(sql)));
      expect(msg).toMatch(/permission denied/);
    }
  });

  it("telegram link tokens are inaccessible to clients", async () => {
    const msg = await as(db, { role: "authenticated", userId: alice }, (q) => expectError(q("select * from public.telegram_link_tokens")));
    expect(msg).toMatch(/permission denied/);
  });

  it("retention cleanup runs", async () => {
    const { rows } = await db.query("select public.run_retention_cleanup() r");
    expect(rows[0].r).toHaveProperty("market_snapshots");
  });
});

describe("NEXUS tiers: free < pro < premium, founding retired", () => {
  let pro: string;
  const tierOf = async (uid: string) => (await as(db, { role: "authenticated", userId: uid }, (q) => q("select public.current_membership_tier() t"))).rows[0].t;

  beforeAll(async () => {
    pro = await createUser(db);
    await grantSubscription(db, pro, { tier: "pro" });
  });

  it("membership_plans match src/config/plans.ts", async () => {
    const { rows } = await db.query("select id, tier, price_cents, billing_interval, limits, is_active from public.membership_plans where tier <> 'free' order by sort_order");
    const active = rows.filter((r) => r.is_active);
    expect(active.map((r) => r.id)).toEqual(PAID_PLANS.filter((p) => p.isActive).map((p) => p.id));
    for (const plan of PAID_PLANS) {
      const row = rows.find((r) => r.id === plan.id)!;
      expect({ tier: row.tier, price: row.price_cents, interval: row.billing_interval, limits: row.limits }).toEqual({
        tier: plan.tier,
        price: plan.priceCents,
        interval: plan.interval,
        limits: TIER_LIMITS[plan.tier],
      });
    }
    expect(rows.find((r) => r.id === "founding_yearly")?.is_active).toBe(false);
  });

  it("pro subscribers report the pro tier", async () => {
    expect(await tierOf(pro)).toBe("pro");
  });

  it("pro limits come from the pro plans", async () => {
    await as(db, { role: "authenticated", userId: pro }, async (q) => {
      await q("insert into public.portfolios (name) values ('P1')");
      await q("insert into public.portfolios (name) values ('P2')");
      expect(await expectError(q("insert into public.portfolios (name) values ('P3')"))).toMatch(/limit_reached:portfolios/);
    });
  });

  it("pro reads pro and free article bodies but not premium bodies", async () => {
    const mk = async (slug: string, access: string) => {
      const id = (
        await db.query(
          "insert into public.research_articles (slug, title, summary, category, access_level, status, author_name, published_at) values ($1, $1, 'A summary of the article.', 'market', $2, 'published', 'Staff', now() - interval '1 hour') returning id",
          [slug, access],
        )
      ).rows[0].id;
      await db.query("insert into public.research_article_bodies (article_id, body_markdown) values ($1, 'body')", [id]);
      return id as string;
    };
    const proArticle = await mk("pro-tier-one", "pro");
    const premiumArticle = await mk("premium-tier-two", "premium");
    const visible = async (uid: string) =>
      (await as(db, { role: "authenticated", userId: uid }, (q) => q("select article_id from public.research_article_bodies where article_id = any($1)", [[proArticle, premiumArticle]]))).rows.map((r) => r.article_id);
    expect(await visible(pro)).toEqual([proArticle]);
    expect((await visible(alice)).sort()).toEqual([proArticle, premiumArticle].sort());
    expect(await visible(carol)).toEqual([]);
  });

  it("new founding subscriptions and articles are rejected, even for the service role", async () => {
    const u = await createUser(db);
    const msg = await expectError(
      db.query(
        "insert into public.subscriptions (user_id, plan_id, tier, status, provider, provider_subscription_id, livemode, current_period_end, last_event_at) values ($1,'founding_yearly','founding','active','x',$2,true, now() + interval '1 year', now())",
        [u, `legacy-${u}`],
      ),
    );
    expect(msg).toMatch(/founding tier is retired/);
    const art = await expectError(
      db.query("insert into public.research_articles (slug, title, summary, category, access_level, author_name) values ('legacy-f', 'Title', 'Summary text here', 'market', 'founding', 'Staff')"),
    );
    expect(art).toMatch(/research_articles_access_level_current/);
  });

  it("a legacy founding subscription is reported as premium", async () => {
    const u = await createUser(db);
    await db.query("alter table public.subscriptions disable trigger subscriptions_reject_retired_tier");
    try {
      await db.query(
        "insert into public.subscriptions (user_id, plan_id, tier, status, provider, provider_subscription_id, livemode, current_period_start, current_period_end, last_event_at) values ($1,'founding_yearly','founding','active','x',$2,true, now() - interval '1 day', now() + interval '1 year', now())",
        [u, `legacy-${u}`],
      );
    } finally {
      await db.query("alter table public.subscriptions enable trigger subscriptions_reject_retired_tier");
    }
    expect(await tierOf(u)).toBe("premium");
    // Legacy rows can still receive lifecycle updates.
    await db.query("update public.subscriptions set cancel_at_period_end = true where user_id = $1", [u]);
  });
});

describe("feature entitlements (plan_features / has_feature)", () => {
  it("plan_features match PLAN_FEATURES in src/config/plans.ts", async () => {
    const { rows } = await db.query("select tier, feature_key, enabled from public.plan_features");
    const fromDb: Record<string, Record<string, boolean>> = {};
    for (const r of rows) (fromDb[r.tier] ??= {})[r.feature_key] = r.enabled;
    expect(fromDb).toEqual(PLAN_FEATURES);
  });

  it("has_feature follows the caller's tier", async () => {
    const pro = await createUser(db);
    await grantSubscription(db, pro, { tier: "pro" });
    const check = async (actor: Parameters<typeof as>[1], key: string) => (await as(db, actor, (q) => q("select public.has_feature($1) v", [key]))).rows[0].v;
    expect(await check({ role: "anon" }, "charts.basic")).toBe(true);
    expect(await check({ role: "anon" }, "signals.history")).toBe(false);
    expect(await check({ role: "authenticated", userId: carol }, "signals.history")).toBe(false);
    expect(await check({ role: "authenticated", userId: pro }, "signals.history")).toBe(true);
    expect(await check({ role: "authenticated", userId: pro }, "backtesting")).toBe(false);
    expect(await check({ role: "authenticated", userId: alice }, "backtesting")).toBe(true);
    expect(await check({ role: "authenticated", userId: alice }, "no.such_feature")).toBe(false);
  });

  it("users cannot change plan_features or query other users' features", async () => {
    const msg = await as(db, { role: "authenticated", userId: carol }, (q) => expectError(q("update public.plan_features set enabled = true")));
    expect(msg).toMatch(/permission denied/);
    const fn = await as(db, { role: "authenticated", userId: carol }, (q) => expectError(q("select public.has_feature_for($1, 'backtesting')", [alice])));
    expect(fn).toMatch(/permission denied/);
  });
});

describe("admin MFA (aal2) enforcement", () => {
  let editor: string;
  beforeAll(async () => {
    editor = await createUser(db);
    await db.query("insert into public.user_roles (user_id, role) values ($1, 'editor')", [editor]);
  });

  it("admins and editors without an MFA-verified session have no privileges", async () => {
    for (const uid of [admin, editor]) {
      const r = await as(db, { role: "authenticated", userId: uid, aal: "aal1" }, (q) => q("select public.is_admin() a, public.is_staff() s"));
      expect(r.rows[0]).toEqual({ a: false, s: false });
    }
    const audit = await as(db, { role: "authenticated", userId: admin }, (q) => q("select 1 from public.admin_audit_logs"));
    expect(audit.rowCount).toBe(0);
    const subs = await as(db, { role: "authenticated", userId: admin }, (q) => q("select 1 from public.subscriptions where user_id <> $1", [admin]));
    expect(subs.rowCount).toBe(0);
    const write = await as(db, { role: "authenticated", userId: editor }, (q) =>
      expectError(q("insert into public.research_articles (slug, title, summary, category, author_name) values ('mfa-test', 'Title', 'Summary text here', 'market', 'Ed')")),
    );
    expect(write).toMatch(/row-level security/);
  });

  it("an aal2 session restores exactly the role's privileges", async () => {
    const a = await as(db, { role: "authenticated", userId: admin, aal: "aal2" }, (q) => q("select public.is_admin() a, public.is_staff() s"));
    expect(a.rows[0]).toEqual({ a: true, s: true });
    const e = await as(db, { role: "authenticated", userId: editor, aal: "aal2" }, (q) => q("select public.is_admin() a, public.is_staff() s"));
    expect(e.rows[0]).toEqual({ a: false, s: true });
    const subs = await as(db, { role: "authenticated", userId: admin, aal: "aal2" }, (q) => q("select 1 from public.subscriptions where user_id <> $1", [admin]));
    expect(subs.rowCount).toBeGreaterThan(0);
  });

  it("an aal2 claim grants nothing to users without a role", async () => {
    const r = await as(db, { role: "authenticated", userId: carol, aal: "aal2" }, (q) => q("select public.is_admin() a, public.is_staff() s"));
    expect(r.rows[0]).toEqual({ a: false, s: false });
  });

  it("privileged users can still see their own role (to be sent to MFA enrollment)", async () => {
    const r = await as(db, { role: "authenticated", userId: editor }, (q) => q("select role from public.user_roles"));
    expect(r.rows).toEqual([{ role: "editor" }]);
  });
});

describe("account lifecycle: export and deletion", () => {
  it("export_my_data returns only the caller's own data", async () => {
    const r = await as(db, { role: "authenticated", userId: alice }, (q) => q("select public.export_my_data() d"));
    const d = r.rows[0].d;
    expect(d.user_id).toBe(alice);
    expect(d.profile.id).toBe(alice);
    expect(d.portfolios.map((p: { user_id: string }) => p.user_id)).toEqual([alice]);
    expect(d.subscriptions.every((s: { user_id: string }) => s.user_id === alice)).toBe(true);
    const json = JSON.stringify(d);
    expect(json).not.toContain(bob);
    expect(json).not.toContain(bobTx);
  });

  it("anon cannot export", async () => {
    const msg = await as(db, { role: "anon" }, (q) => expectError(q("select public.export_my_data()")));
    expect(msg).toMatch(/permission denied|unauthorized/);
  });

  it("deleting an account removes personal data, keeps pseudonymized payments, and audits the deletion", async () => {
    const u = await createUser(db);
    await db.query("insert into public.user_roles (user_id, role) values ($1, 'editor')", [u]);
    await db.query("insert into public.admin_audit_logs (actor_id, action) values ($1, 'article.publish')", [u]);
    const ev = (
      await db.query("insert into public.billing_events (provider, provider_event_id, event_type, occurred_at, livemode, user_id, payload) values ('test', $1, 'x', now(), true, $2, '{}') returning id", [`ev-${u}`, u])
    ).rows[0].id;
    await db.query("insert into public.billing_payments (user_id, billing_event_id, amount_cents, currency, kind, livemode, occurred_at) values ($1, $2, 900, 'USD', 'payment', true, now())", [u, ev]);
    const before = (await db.query("select max(id) m from public.admin_audit_logs")).rows[0].m;

    await db.query("delete from auth.users where id = $1", [u]);

    expect((await db.query("select 1 from public.profiles where id = $1", [u])).rowCount).toBe(0);
    expect((await db.query("select 1 from public.user_roles where user_id = $1", [u])).rowCount).toBe(0);
    const pay = await db.query("select user_id, amount_cents from public.billing_payments where billing_event_id = $1", [ev]);
    expect(pay.rows).toEqual([{ user_id: null, amount_cents: 900 }]);
    const authored = await db.query("select actor_id from public.admin_audit_logs where action = 'article.publish' and id <= $1 order by id desc limit 1", [before]);
    expect(authored.rows[0].actor_id).toBeNull();
    const audit = await db.query("select actor_id, action, target_id, details from public.admin_audit_logs where id > $1 and action = 'account.deleted'", [before]);
    expect(audit.rows).toEqual([{ actor_id: null, action: "account.deleted", target_id: null, details: { retained_payment_records: 1 } }]);
  });

  it("the audit log stays append-only apart from actor pseudonymization", async () => {
    const id = (await db.query("insert into public.admin_audit_logs (actor_id, action) values ($1, 'test.entry') returning id", [admin])).rows[0].id;
    expect(await expectError(db.query("update public.admin_audit_logs set action = 'changed' where id = $1", [id]))).toMatch(/append-only/);
    expect(await expectError(db.query("update public.admin_audit_logs set actor_id = null, details = '{\"x\":1}' where id = $1", [id]))).toMatch(/append-only/);
    expect(await expectError(db.query("delete from public.admin_audit_logs where id = $1", [id]))).toMatch(/append-only/);
  });
});

describe("nexus_engine role (least privilege)", () => {
  it("exists without login and has no table privileges yet", async () => {
    const r = await db.query("select rolcanlogin, rolsuper, rolbypassrls from pg_roles where rolname = 'nexus_engine'");
    expect(r.rows).toEqual([{ rolcanlogin: false, rolsuper: false, rolbypassrls: false }]);
    for (const sql of ["select 1 from public.profiles", "select 1 from public.subscriptions", "insert into public.cryptocurrencies (id, symbol, name) values ('x', 'X', 'x')"]) {
      await db.query("begin");
      try {
        await db.query("set local role nexus_engine");
        expect(await expectError(db.query(sql))).toMatch(/permission denied/);
      } finally {
        await db.query("rollback");
      }
    }
  });
});

describe("abuse reports", () => {
  it("users file reports with content only; status fields cannot be forged", async () => {
    const r = await as(db, { role: "authenticated", userId: carol }, (q) =>
      q("insert into public.abuse_reports (target_type, target_id, reason, details) values ('article', 'free-one', 'misleading', 'Numbers look wrong') returning reporter_id, status"),
    );
    expect(r.rows[0]).toEqual({ reporter_id: carol, status: "open" });
    const forgedStatus = await as(db, { role: "authenticated", userId: carol }, (q) => expectError(q("insert into public.abuse_reports (target_type, reason, status) values ('other', 'spam', 'resolved')")));
    expect(forgedStatus).toMatch(/permission denied/);
    const forgedReporter = await as(db, { role: "authenticated", userId: carol }, (q) => expectError(q("insert into public.abuse_reports (reporter_id, target_type, reason) values ($1, 'other', 'spam')", [bob])));
    expect(forgedReporter).toMatch(/permission denied/);
  });

  it("users see only their own reports and cannot change them", async () => {
    const id = (await db.query("insert into public.abuse_reports (reporter_id, target_type, reason) values ($1, 'other', 'spam') returning id", [bob])).rows[0].id;
    await as(db, { role: "authenticated", userId: carol }, async (q) => {
      expect((await q("select 1 from public.abuse_reports where id = $1", [id])).rowCount).toBe(0);
    });
    await as(db, { role: "authenticated", userId: bob }, async (q) => {
      expect((await q("select 1 from public.abuse_reports where id = $1", [id])).rowCount).toBe(1);
      expect((await q("update public.abuse_reports set status = 'dismissed', handled_by = $2, handled_at = now() where id = $1", [id, bob])).rowCount).toBe(0);
      expect(await expectError(q("delete from public.abuse_reports where id = $1", [id]))).toMatch(/permission denied/);
    });
  });

  it("only MFA-verified admins handle reports, as themselves", async () => {
    const id = (await db.query("insert into public.abuse_reports (reporter_id, target_type, reason) values ($1, 'other', 'spam') returning id", [bob])).rows[0].id;
    const aal1 = await as(db, { role: "authenticated", userId: admin }, (q) => q("update public.abuse_reports set status = 'resolved', handled_by = $2, handled_at = now() where id = $1", [id, admin]));
    expect(aal1.rowCount).toBe(0);
    const impersonate = await as(db, { role: "authenticated", userId: admin, aal: "aal2" }, (q) =>
      expectError(q("update public.abuse_reports set status = 'resolved', handled_by = $2, handled_at = now() where id = $1", [id, bob])),
    );
    expect(impersonate).toMatch(/row-level security/);
    const ok = await as(db, { role: "authenticated", userId: admin, aal: "aal2" }, (q) =>
      q("update public.abuse_reports set status = 'resolved', handled_by = $2, handled_at = now(), resolution_note = 'Removed' where id = $1", [id, admin]),
    );
    expect(ok.rowCount).toBe(1);
  });

  it("limits reports per user per day", async () => {
    const u = await createUser(db);
    await as(db, { role: "authenticated", userId: u }, async (q) => {
      for (let i = 0; i < 20; i++) await q("insert into public.abuse_reports (target_type, reason) values ('other', 'spam')");
      expect(await expectError(q("insert into public.abuse_reports (target_type, reason) values ('other', 'spam')"))).toMatch(/limit_reached:abuseReports/);
    });
  });
});

describe("market data (NEXUS Phase 3)", () => {
  let btc: number;
  const asEngine = async <T>(fn: (q: Client["query"]) => Promise<T>): Promise<T> => {
    await db.query("begin");
    try {
      await db.query("set local role nexus_engine");
      return await fn(db.query.bind(db) as Client["query"]);
    } finally {
      await db.query("rollback");
    }
  };
  const candle = (openTime: string, o = 100, h = 110, l = 90, c = 105): unknown[] => [
    btc, "1h", openTime, new Date(Date.parse(openTime) + 3_600_000 - 1).toISOString(), o, h, l, c, 12.5,
  ];
  const insertSql =
    "insert into public.market_candles (pair_id, timeframe, open_time, close_time, open, high, low, close, volume, is_final) values ($1,$2,$3,$4,$5,$6,$7,$8,$9,true)";

  beforeAll(async () => {
    btc = (await db.query("select p.id from public.market_pairs p join public.market_sources s on s.id = p.source_id where s.key = 'binance_spot' and p.symbol = 'BTCUSDT'")).rows[0].id;
  });

  it("seeds Binance BTC/USDT and ETH/USDT on four timeframes, with the terms review not yet recorded", async () => {
    const { rows } = await db.query("select s.key, s.terms_reviewed_at, p.symbol, p.timeframes from public.market_pairs p join public.market_sources s on s.id = p.source_id order by p.symbol");
    expect(rows.map((r) => r.symbol)).toEqual(["BTCUSDT", "ETHUSDT"]);
    expect(rows.every((r) => r.terms_reviewed_at === null)).toBe(true);
    expect(rows[0].timeframes).toEqual(["15m", "1h", "4h", "1d"]);
  });

  it("the engine role can write candles; users and anon cannot", async () => {
    await asEngine(async (q) => {
      await q(insertSql, candle("2026-01-01T00:00:00Z"));
      expect((await q("select 1 from public.market_candles where pair_id = $1", [btc])).rowCount).toBe(1);
    });
    for (const actor of [{ role: "anon" as const }, { role: "authenticated" as const, userId: carol }]) {
      const msg = await as(db, actor, (q) => expectError(q(insertSql, candle("2026-01-01T01:00:00Z"))));
      expect(msg).toMatch(/permission denied/);
    }
  });

  it("the engine role cannot touch user or billing data", async () => {
    for (const sql of ["select 1 from public.profiles", "select 1 from public.subscriptions", "update public.market_sources set terms_reviewed_at = now()"]) {
      expect(await asEngine((q) => expectError(q(sql)))).toMatch(/permission denied/);
    }
  });

  it("rejects invalid candles: misaligned, wrong close time, inconsistent OHLC, non-positive prices", async () => {
    const bad = [
      [btc, "1h", "2026-01-01T00:30:00Z", "2026-01-01T01:29:59.999Z", 100, 110, 90, 105, 1],
      [btc, "1h", "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z", 100, 110, 90, 105, 1],
      [btc, "1h", "2026-01-01T00:00:00Z", "2026-01-01T00:59:59.999Z", 100, 104, 90, 105, 1],
      [btc, "1h", "2026-01-01T00:00:00Z", "2026-01-01T00:59:59.999Z", 0, 110, 90, 105, 1],
      [btc, "1h", "2026-01-01T00:00:00Z", "2026-01-01T00:59:59.999Z", 100, 110, 90, 105, -1],
      [btc, "2h", "2026-01-01T00:00:00Z", "2026-01-01T01:59:59.999Z", 100, 110, 90, 105, 1],
    ];
    for (const params of bad) expect(await asEngine((q) => expectError(q(insertSql, params)))).toMatch(/violates check constraint/);
  });

  it("duplicates are impossible and final candles are immutable, even for the engine", async () => {
    await asEngine(async (q) => {
      await q(insertSql, candle("2026-01-02T00:00:00Z"));
      const upsert = await q(
        "insert into public.market_candles (pair_id, timeframe, open_time, close_time, open, high, low, close, volume, is_final) values ($1,$2,$3,$4,$5,$6,$7,$8,$9,true) on conflict (pair_id, timeframe, open_time) do update set close = excluded.close where market_candles.is_final = false",
        candle("2026-01-02T00:00:00Z", 100, 110, 90, 106),
      );
      expect(upsert.rowCount).toBe(0);
      expect((await q("select count(*)::int n, max(close) c from public.market_candles where pair_id = $1 and open_time = '2026-01-02T00:00:00Z'", [btc])).rows[0]).toEqual({ n: 1, c: "105" });
    });
    expect(await asEngine(async (q) => {
      await q(insertSql, candle("2026-01-03T00:00:00Z"));
      return expectError(q("update public.market_candles set close = 107 where pair_id = $1 and open_time = '2026-01-03T00:00:00Z'", [btc]));
    })).toMatch(/final candles are immutable/);
    expect(await asEngine(async (q) => {
      await q(insertSql, candle("2026-01-04T00:00:00Z"));
      return expectError(q("delete from public.market_candles where pair_id = $1", [btc]));
    })).toMatch(/permission denied|cannot be deleted/);
  });

  it("a non-final candle may be finalized", async () => {
    await asEngine(async (q) => {
      await q(insertSql.replace(",true)", ",false)"), candle("2026-01-05T00:00:00Z"));
      const r = await q("update public.market_candles set close = 108, high = 111, is_final = true where pair_id = $1 and open_time = '2026-01-05T00:00:00Z'", [btc]);
      expect(r.rowCount).toBe(1);
    });
  });

  it("operational tables are staff-only (MFA); candles and pairs are public", async () => {
    await db.query("insert into public.engine_heartbeats (component) values ('test.worker') on conflict do nothing");
    for (const t of ["engine_heartbeats", "candle_gaps", "ingest_runs", "engine_jobs"]) {
      const anonMsg = await as(db, { role: "anon" }, (q) => expectError(q(`select 1 from public.${t}`)));
      expect(anonMsg).toMatch(/permission denied/);
      const user = await as(db, { role: "authenticated", userId: carol }, (q) => q(`select 1 from public.${t}`));
      expect(user.rowCount).toBe(0);
    }
    const staff = await as(db, { role: "authenticated", userId: admin, aal: "aal2" }, (q) => q("select 1 from public.engine_heartbeats"));
    expect(staff.rowCount).toBeGreaterThan(0);
    const pairs = await as(db, { role: "anon" }, (q) => q("select symbol from public.market_pairs"));
    expect(pairs.rowCount).toBe(2);
  });

  it("only MFA admins add pairs or record a terms review, and only as themselves", async () => {
    const add = "insert into public.market_pairs (source_id, symbol, base_asset, quote_asset) select id, 'SOLUSDT', 'SOL', 'USDT' from public.market_sources where key = 'binance_spot'";
    expect(await as(db, { role: "authenticated", userId: admin }, (q) => expectError(q(add)))).toMatch(/row-level security/);
    expect((await as(db, { role: "authenticated", userId: admin, aal: "aal2" }, (q) => q(add))).rowCount).toBe(1);
    const review = "update public.market_sources set terms_reviewed_at = now(), terms_reviewed_by = $1 where key = 'binance_spot'";
    expect((await as(db, { role: "authenticated", userId: carol }, (q) => q(review, [carol]))).rowCount).toBe(0);
    expect(await as(db, { role: "authenticated", userId: admin, aal: "aal2" }, (q) => expectError(q(review, [carol])))).toMatch(/row-level security/);
    expect((await as(db, { role: "authenticated", userId: admin, aal: "aal2" }, (q) => q(review, [admin]))).rowCount).toBe(1);
    const forge = await as(db, { role: "authenticated", userId: admin, aal: "aal2" }, (q) => expectError(q("update public.market_sources set base_url = 'https://evil.example' where key = 'binance_spot'")));
    expect(forge).toMatch(/permission denied/);
  });
});

describe("signal ledger (NEXUS Phase 4)", () => {
  let pairId: number;
  let strategyId: number;
  let proUser: string;
  let betaUser: string;
  let lastHash: string | null = null;
  let n = 0;
  const hex = (i: number) => i.toString(16).padStart(64, "0");

  async function evaluation(decision = "BUY", createdAt?: string) {
    n++;
    const r = await db.query(
      "insert into public.signal_evaluations (pair_id, timeframe, strategy_id, as_of, decision, created_at) values ($1, '1h', $2, now() - make_interval(hours => $3), $4, coalesce($5::timestamptz, now())) returning id",
      [pairId, strategyId, n, decision, createdAt ?? null],
    );
    return r.rows[0].id as string;
  }

  /** Publish via the real trigger path (published_at = now(), chained). */
  async function publish(visibility: string, opts: { backdateDays?: number } = {}) {
    const ev = await evaluation();
    const id = randomUUID();
    const hash = hex(++n);
    const insert = (publishedAt: string) =>
      db.query(
        `insert into public.signals (id, evaluation_id, pair_id, timeframe, strategy_id, direction, as_of, published_at, entry_low, entry_high, stop_loss, take_profits,
          risk_reward, confidence_band, entry_expires_at, horizon_bars, assumptions, explanation, visibility, prev_hash, content_hash)
         values ($1, $2, $3, '1h', $4, 'BUY', ${publishedAt} - interval '1 hour', ${publishedAt}, 100, 101, 95, '{110,115}', 1.8, 'not_calibrated',
          ${publishedAt} + interval '6 hours', 24, '{}', '{}', $5, $6, $7)`,
        [id, ev, pairId, strategyId, visibility, lastHash, hash],
      );
    if (opts.backdateDays) {
      await db.query("alter table public.signals disable trigger signals_chain_guard");
      try {
        await insert(`(now() - interval '${opts.backdateDays} days')`);
      } finally {
        await db.query("alter table public.signals enable trigger signals_chain_guard");
      }
    } else {
      await insert("now()");
    }
    lastHash = hash;
    return id;
  }

  async function close(id: string) {
    await db.query("insert into public.signal_outcomes (signal_id, outcome, entry_price, exit_price, net_return, r_multiple, closed_at, resolver_version) values ($1, 'loss', 100.5, 95, -0.0567, -1.0, now(), '1.0.0')", [id]);
  }

  const visible = async (actor: Parameters<typeof as>[1], ids: string[]) =>
    (await as(db, actor, (q) => q("select id from public.signals where id = any($1)", [ids]))).rows.map((r) => r.id).sort();

  beforeAll(async () => {
    pairId = (await db.query("select id from public.market_pairs where symbol = 'BTCUSDT'")).rows[0].id;
    strategyId = (await db.query("insert into public.strategies (key, version, name, description, params) values ('trend_pullback', '1.0.0', 'Trend pullback', 'Rules only', '{}') returning id")).rows[0].id;
    proUser = await createUser(db);
    await grantSubscription(db, proUser, { tier: "pro" });
    betaUser = await createUser(db);
    await db.query("insert into public.beta_testers (user_id) values ($1)", [betaUser]);
  });

  it("signals, events, outcomes, annotations and strategies are insert-only, even for the superuser", async () => {
    const id = await publish("public");
    await close(id);
    await db.query("insert into public.signal_events (signal_id, event_type, occurred_at, price) values ($1, 'entered', now(), 100.5)", [id]);
    await db.query("insert into public.signal_annotations (signal_id, kind, body) values ($1, 'note', 'test note')", [id]);
    for (const sql of [
      "update public.signals set stop_loss = 90 where id = $1",
      "delete from public.signals where id = $1",
      "update public.signal_outcomes set outcome = 'win' where signal_id = $1",
      "delete from public.signal_outcomes where signal_id = $1",
      "update public.signal_events set price = 1 where signal_id = $1",
      "delete from public.signal_annotations where signal_id = $1",
    ]) {
      expect(await expectError(db.query(sql, [id]))).toMatch(/insert-only/);
    }
    expect(await expectError(db.query("update public.strategies set params = '{\"x\":1}'"))).toMatch(/insert-only/);
  });

  it("published_at cannot be backdated and the hash chain must link", async () => {
    const ev = await evaluation();
    const base = `insert into public.signals (id, evaluation_id, pair_id, timeframe, strategy_id, direction, as_of, published_at, entry_low, entry_high, stop_loss, take_profits,
      risk_reward, confidence_band, entry_expires_at, horizon_bars, assumptions, explanation, visibility, prev_hash, content_hash)
      values ($1, $2, $3, '1h', $4, 'BUY', now() - interval '2 hours', %PUB%, 100, 101, 95, '{110}', 1.8, 'not_calibrated', now() + interval '6 hours', 24, '{}', '{}', 'staff', $5, $6)`;
    const backdated = await expectError(db.query(base.replace("%PUB%", "now() - interval '1 hour'"), [randomUUID(), ev, pairId, strategyId, lastHash, hex(++n)]));
    expect(backdated).toMatch(/transaction time/);
    const broken = await expectError(db.query(base.replace("%PUB%", "now()"), [randomUUID(), ev, pairId, strategyId, hex(999_999), hex(++n)]));
    expect(broken).toMatch(/hash chain mismatch/);
  });

  it("level ordering is enforced (BUY: stop < entry < target)", async () => {
    const ev = await evaluation();
    const msg = await expectError(
      db.query(
        `insert into public.signals (id, evaluation_id, pair_id, timeframe, strategy_id, direction, as_of, published_at, entry_low, entry_high, stop_loss, take_profits,
          risk_reward, confidence_band, entry_expires_at, horizon_bars, assumptions, explanation, visibility, prev_hash, content_hash)
         values ($1, $2, $3, '1h', $4, 'BUY', now() - interval '1 hour', now(), 100, 101, 102, '{110}', 1.8, 'not_calibrated', now() + interval '6 hours', 24, '{}', '{}', 'staff', $5, $6)`,
        [randomUUID(), ev, pairId, strategyId, lastHash, hex(++n)],
      ),
    );
    expect(msg).toMatch(/check constraint/);
  });

  it("visibility follows the gate and tiers (D3)", async () => {
    const staffSig = await publish("staff");
    const betaSig = await publish("beta");
    const liveSig = await publish("public");
    const oldClosed = await publish("public", { backdateDays: 10 });
    await close(oldClosed);
    const recentClosed = await publish("public");
    await close(recentClosed);
    const all = [staffSig, betaSig, liveSig, oldClosed, recentClosed];

    expect(await visible({ role: "authenticated", userId: admin, aal: "aal2" }, all)).toEqual([...all].sort());
    expect(await visible({ role: "authenticated", userId: admin }, all)).toEqual([oldClosed]); // no MFA: like a free user
    expect(await visible({ role: "authenticated", userId: proUser }, all)).toEqual([liveSig, oldClosed, recentClosed].sort());
    expect(await visible({ role: "authenticated", userId: betaUser }, all)).toEqual([betaSig, oldClosed].sort());
    expect(await visible({ role: "authenticated", userId: carol }, all)).toEqual([oldClosed]);
    expect(await visible({ role: "anon" }, all)).toEqual([oldClosed]);
  });

  it("events and outcomes are visible exactly when their signal is", async () => {
    const live = await publish("public");
    await close(live);
    const outcomes = async (actor: Parameters<typeof as>[1]) => (await as(db, actor, (q) => q("select 1 from public.signal_outcomes where signal_id = $1", [live]))).rowCount;
    expect(await outcomes({ role: "authenticated", userId: proUser })).toBe(1);
    expect(await outcomes({ role: "authenticated", userId: carol })).toBe(0);
  });

  it("only MFA admins annotate, as themselves; nobody else writes to the ledger", async () => {
    const id = await publish("public");
    const ann = "insert into public.signal_annotations (signal_id, kind, body) values ($1, 'correction', 'Entry range was mislabeled in the UI')";
    expect(await as(db, { role: "authenticated", userId: admin }, (q) => expectError(q(ann, [id])))).toMatch(/row-level security/);
    expect((await as(db, { role: "authenticated", userId: admin, aal: "aal2" }, (q) => q(ann + " returning author_id", [id]))).rows[0].author_id).toBe(admin);
    for (const actor of [{ role: "authenticated" as const, userId: proUser }, { role: "anon" as const }]) {
      expect(await as(db, actor, (q) => expectError(q("insert into public.signal_outcomes (signal_id, outcome, closed_at, resolver_version) values ($1, 'expired_no_entry', now(), '1.0.0')", [id])))).toMatch(/permission denied/);
    }
  });

  it("model versions follow the lifecycle and approvals are MFA-admin only", async () => {
    const mv = (await db.query("insert into public.model_versions (strategy_id, version, model_type) values ($1, '0.1.0', 'logistic') returning id", [strategyId])).rows[0].id;
    expect(await expectError(db.query("update public.model_versions set status = 'approved', approved_at = now() where id = $1", [mv]))).toMatch(/invalid model status transition/);
    await db.query("update public.model_versions set status = 'shadow' where id = $1", [mv]);
    expect(await expectError(db.query("update public.model_versions set status = 'approved', approved_at = now() where id = $1", [mv]))).toMatch(/artifact checksum/);
    expect(await expectError(db.query("update public.model_versions set model_type = 'xgboost' where id = $1", [mv]))).toMatch(/immutable/);
    const approve = "update public.model_versions set status = 'retired', retired_at = now() where id = $1";
    expect((await as(db, { role: "authenticated", userId: proUser }, (q) => q(approve, [mv]))).rowCount).toBe(0);
    expect((await as(db, { role: "authenticated", userId: admin, aal: "aal2" }, (q) => q(approve, [mv]))).rowCount).toBe(1);
  });

  it("HOLD evaluations older than a year may be pruned; BUY/SELL evaluations never", async () => {
    const oldHold = await evaluation("HOLD", new Date(Date.now() - 400 * 86_400_000).toISOString());
    const recentHold = await evaluation("HOLD");
    const oldBuy = await evaluation("BUY", new Date(Date.now() - 400 * 86_400_000).toISOString());
    await db.query("delete from public.signal_evaluations where id = $1", [oldHold]);
    expect(await expectError(db.query("delete from public.signal_evaluations where id = $1", [recentHold]))).toMatch(/insert-only/);
    expect(await expectError(db.query("delete from public.signal_evaluations where id = $1", [oldBuy]))).toMatch(/insert-only/);
    expect((await as(db, { role: "authenticated", userId: proUser }, (q) => q("select 1 from public.signal_evaluations"))).rowCount).toBe(0);
  });
});

describe("validation and backtests (NEXUS Phase 5)", () => {
  let strategyId: number;
  let pairId: number;
  let premiumUser: string;
  let proUser: string;
  const hash = "a".repeat(64);

  beforeAll(async () => {
    strategyId = (await db.query("select id from public.strategies where key = 'trend_pullback' and version = '1.0.0'")).rows[0].id;
    pairId = (await db.query("select id from public.market_pairs where symbol = 'ETHUSDT'")).rows[0].id;
    premiumUser = await createUser(db);
    await grantSubscription(db, premiumUser, { tier: "premium" });
    proUser = await createUser(db);
    await grantSubscription(db, proUser, { tier: "pro" });
  });

  const experiment = async () =>
    (await db.query("insert into public.experiments (kind, strategy_id, config, dataset_hash, engine_version, seed) values ('walk_forward', $1, '{}', $2, '0.1.0', 7) returning id", [strategyId, hash])).rows[0].id as string;
  const report = async (exp: string, lockbox: string | null) =>
    (
      await db.query(
        "insert into public.validation_reports (experiment_id, strategy_id, period_start, period_end, n_trades, folds, metrics, baselines, assumptions, lockbox) values ($1, $2, now() - interval '1 year', now(), 0, '[]', '{}', '{}', '{}', $3) returning id",
        [exp, strategyId, lockbox],
      )
    ).rows[0].id as string;

  it("experiments finish exactly once and their inputs never change", async () => {
    const id = await experiment();
    expect(await expectError(db.query("update public.experiments set seed = 8 where id = $1", [id]))).toMatch(/inputs are immutable/);
    await db.query("update public.experiments set status = 'failed', error = 'boom', finished_at = now() where id = $1", [id]);
    expect(await expectError(db.query("update public.experiments set status = 'succeeded' where id = $1", [id]))).toMatch(/finished experiments are immutable/);
    expect(await expectError(db.query("delete from public.experiments where id = $1", [id]))).toMatch(/never deleted/);
  });

  it("validation reports are insert-only", async () => {
    const r = await report(await experiment(), null);
    expect(await expectError(db.query("update public.validation_reports set n_trades = 5 where id = $1", [r]))).toMatch(/insert-only/);
  });

  it("a model can only be approved with a validation report that has a lockbox result", async () => {
    const noLockbox = await report(await experiment(), null);
    const withLockbox = await report(await experiment(), '{"net_return":"0"}');
    const mk = async (v: string, rep: string | null) =>
      (await db.query("insert into public.model_versions (strategy_id, version, model_type, artifact_sha256, status, validation_report_id) values ($1, $2, 'logistic', $3, 'shadow', $4) returning id", [strategyId, v, hash, rep])).rows[0].id;
    const approve = "update public.model_versions set status = 'approved', approved_at = now() where id = $1";
    expect(await expectError(db.query(approve, [await mk("1.0.1", null)]))).toMatch(/validation report with a lockbox/);
    expect(await expectError(db.query(approve, [await mk("1.0.2", noLockbox)]))).toMatch(/validation report with a lockbox/);
    const ok = await mk("1.0.3", withLockbox);
    await db.query(approve, [ok]);
    expect(await expectError(db.query("update public.model_versions set validation_report_id = $2 where id = $1", [ok, noLockbox]))).toMatch(/immutable/);
  });

  it("only Premium users can request backtests, within bounds and quota", async () => {
    const req = (start = "now() - interval '90 days'", end = "now() - interval '1 day'") =>
      `select public.request_backtest(${pairId}, '1h', ${start}, ${end}, '{"min_net_rr": 2}')`;
    expect(await as(db, { role: "authenticated", userId: proUser }, (q) => expectError(q(req())))).toMatch(/feature_required:backtesting/);
    expect(await as(db, { role: "anon" }, (q) => expectError(q(req())))).toMatch(/permission denied/);
    expect(await as(db, { role: "authenticated", userId: premiumUser }, (q) => expectError(q(req("now() - interval '6 years'"))))).toMatch(/check constraint/);
    expect(await as(db, { role: "authenticated", userId: premiumUser }, (q) => expectError(q(req("now() - interval '2 days'", "now() + interval '1 day'"))))).toMatch(/must end in the past/);
    const id = (await as(db, { role: "authenticated", userId: premiumUser }, (q) => q(req()))).rows[0].request_backtest;
    expect(id).toBeTruthy();
  });

  it("backtest runs and results are private to their owner (and staff)", async () => {
    const run = (await db.query("insert into public.backtest_runs (requested_by, pair_id, timeframe, period_start, period_end, status) values ($1, $2, '1h', now() - interval '30 days', now() - interval '1 day', 'succeeded') returning id", [premiumUser, pairId])).rows[0].id;
    await db.query("insert into public.backtest_results (run_id, metrics, equity_curve, baselines, assumptions) values ($1, '{}', '[]', '{}', '{}')", [run]);
    const sees = async (uid: string, aal?: "aal2") => (await as(db, { role: "authenticated", userId: uid, aal }, (q) => q("select 1 from public.backtest_results where run_id = $1", [run]))).rowCount;
    expect(await sees(premiumUser)).toBe(1);
    expect(await sees(proUser)).toBe(0);
    expect(await sees(admin, "aal2")).toBe(1);
    expect(await as(db, { role: "authenticated", userId: premiumUser }, (q) => expectError(q("update public.backtest_runs set status = 'queued' where id = $1", [run])))).toMatch(/permission denied/);
  });

  it("signal_performance aggregates only signals the viewer can see", async () => {
    const total = async (actor: Parameters<typeof as>[1]) =>
      Number((await as(db, actor, (q) => q("select coalesce(sum(signals), 0) n from public.signal_performance"))).rows[0].n);
    const staffTotal = await total({ role: "authenticated", userId: admin, aal: "aal2" });
    expect(staffTotal).toBeGreaterThan(0);
    expect(await total({ role: "anon" })).toBeLessThan(staffTotal);
    expect(await total({ role: "authenticated", userId: carol })).toBe(await total({ role: "anon" }));
  });
});
