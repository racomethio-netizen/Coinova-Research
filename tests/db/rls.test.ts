import type { Client } from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
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
    const r2 = await as(db, { role: "authenticated", userId: admin }, (q) => q("select public.is_admin() as a"));
    expect(r2.rows[0].a).toBe(true);
  });

  it("users can only see their own role rows; admins see all", async () => {
    const mine = await as(db, { role: "authenticated", userId: alice }, (q) => q("select * from public.user_roles"));
    expect(mine.rowCount).toBe(0);
    const all = await as(db, { role: "authenticated", userId: admin }, (q) => q("select * from public.user_roles"));
    expect(all.rowCount).toBeGreaterThanOrEqual(1);
  });
});

describe("membership cannot be self-granted", () => {
  it("users cannot insert or update subscriptions", async () => {
    const ins = await as(db, { role: "authenticated", userId: carol }, (q) =>
      expectError(
        q(
          "insert into public.subscriptions (user_id, plan_id, tier, status, provider, provider_subscription_id, livemode, current_period_end, last_event_at) values ($1,'founding_yearly','founding','active','x','y',true, now() + interval '1 year', now())",
          [carol],
        ),
      ),
    );
    expect(ins).toMatch(/permission denied/);
    const upd = await as(db, { role: "authenticated", userId: alice }, (q) => expectError(q("update public.subscriptions set tier = 'founding'")));
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
    expect(await bodies({ role: "authenticated", userId: admin })).toContain(draftArticle);
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
    const ok = await as(db, { role: "authenticated", userId: admin }, (q) => q("select public.publish_scoring_config('1.0.1', '{}', 'test') v"));
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
    const msg = await as(db, { role: "authenticated", userId: admin }, (q) => expectError(q("insert into public.admin_audit_logs (actor_id, action) values ($1, 'forged')", [alice])));
    expect(msg).toMatch(/row-level security/);
  });

  it("non-admins cannot read billing events or jobs; admins can", async () => {
    await db.query("insert into public.billing_events (provider, provider_event_id, event_type, occurred_at, livemode, payload) values ('t','e1','x',now(),false,'{}')");
    const user = await as(db, { role: "authenticated", userId: alice }, (q) => q("select * from public.billing_events"));
    expect(user.rowCount).toBe(0);
    const adm = await as(db, { role: "authenticated", userId: admin }, (q) => q("select * from public.billing_events"));
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
      const msg = await as(db, { role: "authenticated", userId: admin }, (q) => expectError(q(sql)));
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
