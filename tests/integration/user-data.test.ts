import type { Client } from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { AIProvider } from "@/lib/ai/provider";
import { AIProviderError } from "@/lib/ai/provider";
import type { TelegramUpdate } from "@/lib/notify/telegram";
import { getMarketService } from "@/lib/market";
import { runAssistant, type AssistantDeps } from "@/server/ai";
import { refreshMarkets } from "@/server/jobs/market";
import { logger } from "@/lib/logger";
import { addTransaction, createPortfolio, deleteTransaction, getPortfolioOverview } from "@/server/portfolio";
import { createLinkToken, handleTelegramUpdate } from "@/server/telegram";
import { addItem, createWatchlist } from "@/server/watchlists";
import { newUser, pg, serviceClient, userClient } from "./helpers";

let db: Client;
let coin: string;

beforeAll(async () => {
  db = await pg();
  await refreshMarkets({ db: serviceClient(), now: new Date(), log: logger });
  coin = getMarketService().universeIds[0]!;
});
afterAll(async () => {
  await db.end();
});

async function premium(uid: string) {
  await db.query(
    "insert into public.subscriptions (user_id, plan_id, tier, status, provider, provider_subscription_id, livemode, current_period_start, current_period_end, last_event_at) values ($1,'premium_monthly','premium','active','test',$2,true,now(),now() + interval '30 days', now())",
    [uid, `t-${uid}`],
  );
}

describe("portfolio repository with RLS", () => {
  it("records trades, rejects overselling, and values holdings", async () => {
    const uid = await newUser(db);
    await premium(uid);
    const sb = userClient(uid);
    const p = await createPortfolio(sb, uid, "Main");
    const t = (d: string) => new Date(Date.now() - Number(d) * 86_400_000).toISOString();
    const buy = await addTransaction(sb, uid, { portfolioId: p.id, coinId: coin, type: "buy", quantity: "2", priceUsd: "100", feeUsd: "1", occurredAt: t("3"), notes: null });
    await expect(addTransaction(sb, uid, { portfolioId: p.id, coinId: coin, type: "sell", quantity: "3", priceUsd: "150", feeUsd: "0", occurredAt: t("1"), notes: null })).rejects.toMatchObject({ code: "validation_error" });
    const sell = await addTransaction(sb, uid, { portfolioId: p.id, coinId: coin, type: "sell", quantity: "1", priceUsd: "150", feeUsd: "0", occurredAt: t("1"), notes: null });
    // Deleting the buy would leave the later sale unbacked.
    await expect(deleteTransaction(sb, uid, buy.id)).rejects.toMatchObject({ code: "validation_error" });
    await deleteTransaction(sb, uid, sell.id);
    const o = await getPortfolioOverview(sb, uid);
    expect(o.portfolios[0]!.summary!.totalCostBasis.toString()).toBe("201");
    expect(o.portfolios[0]!.summary!.holdings[0]!.quantity.toString()).toBe("2");
  });

  it("free users cannot create portfolios (database trigger)", async () => {
    const uid = await newUser(db);
    await expect(createPortfolio(userClient(uid), uid, "Nope")).rejects.toMatchObject({ code: "limit_reached" });
  });

  it("one user cannot write into another user's portfolio", async () => {
    const owner = await newUser(db);
    const attacker = await newUser(db);
    await premium(owner);
    await premium(attacker);
    const p = await createPortfolio(userClient(owner), owner, "Owner");
    await expect(
      addTransaction(userClient(attacker), attacker, { portfolioId: p.id, coinId: coin, type: "buy", quantity: "1", priceUsd: "1", feeUsd: "0", occurredAt: new Date().toISOString(), notes: null }),
    ).rejects.toMatchObject({ code: "not_found" });
    expect((await getPortfolioOverview(userClient(attacker), attacker)).portfolios).toHaveLength(0);
  });
});

describe("watchlists", () => {
  it("free tier allows one list with up to 10 coins", async () => {
    const uid = await newUser(db);
    const sb = userClient(uid);
    const wl = await createWatchlist(sb, uid, "Mine");
    await expect(createWatchlist(sb, uid, "Second")).rejects.toMatchObject({ code: "limit_reached" });
    const ids = getMarketService().universeIds.slice(0, 11);
    for (const id of ids.slice(0, 10)) await addItem(sb, uid, wl.id, id);
    await expect(addItem(sb, uid, wl.id, ids[10]!)).rejects.toMatchObject({ code: "limit_reached" });
    await addItem(sb, uid, wl.id, ids[0]!); // idempotent re-add
  });
});

describe("Telegram linking", () => {
  const update = (text: string, chatId = 4242): TelegramUpdate => ({ update_id: 1, message: { message_id: 1, text, chat: { id: chatId, type: "private" }, from: { id: 99, username: "tester" } } });
  const sent: string[] = [];
  const send = async (_c: number, text: string) => {
    sent.push(text);
    return { ok: true as const, messageId: "1" };
  };

  it("links with a valid single-use token and rejects reuse, garbage, and group chats", async () => {
    const uid = await newUser(db);
    const { url } = await createLinkToken(uid);
    const token = new URL(url!).searchParams.get("start")!;
    expect(await handleTelegramUpdate(update(`/start ${token}`), serviceClient(), send)).toBe("linked");
    expect((await db.query("select chat_id from public.telegram_accounts where user_id = $1", [uid])).rows[0].chat_id).toBe("4242");
    expect(await handleTelegramUpdate(update(`/start ${token}`, 777), serviceClient(), send)).toBe("invalid_token");
    expect(await handleTelegramUpdate(update("/start AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"), serviceClient(), send)).toBe("invalid_token");
    const group: TelegramUpdate = { update_id: 2, message: { message_id: 2, text: `/start ${token}`, chat: { id: -1, type: "group" }, from: { id: 99 } } };
    expect(await handleTelegramUpdate(group, serviceClient(), send)).toBe("ignored");
    expect(await handleTelegramUpdate(update("/stop"), serviceClient(), send)).toBe("unlinked");
    expect((await db.query("select 1 from public.telegram_accounts where user_id = $1", [uid])).rowCount).toBe(0);
  });

  it("rejects expired tokens and invalidates older tokens when a new one is issued", async () => {
    const uid = await newUser(db);
    const first = new URL((await createLinkToken(uid)).url!).searchParams.get("start")!;
    const second = new URL((await createLinkToken(uid)).url!).searchParams.get("start")!;
    expect(await handleTelegramUpdate(update(`/start ${first}`, 5001), serviceClient(), send)).toBe("invalid_token");
    await db.query("update public.telegram_link_tokens set expires_at = now() - interval '1 minute' where user_id = $1 and used_at is null", [uid]);
    expect(await handleTelegramUpdate(update(`/start ${second}`, 5002), serviceClient(), send)).toBe("invalid_token");
  });
});

describe("AI assistant usage limits and failures", () => {
  const blocks = async () => [{ kind: "market_data" as const, source: "test", asOf: new Date().toISOString(), label: "x", content: { priceUsd: 1 } }];
  const okProvider: AIProvider = { id: "fake", model: "fake-model", complete: async () => ({ text: "answer", model: "fake-model", inputTokens: 10, outputTokens: 5, servedByFallback: false }) };
  const failing: AIProvider = {
    id: "fake",
    model: "fake-model",
    complete: async () => {
      throw new AIProviderError("unavailable", "down");
    },
  };
  const deps = (uid: string, provider: AIProvider | null): AssistantDeps => ({
    provider,
    gather: blocks,
    reserve: async (feature) => {
      const { data, error } = await userClient(uid).rpc("reserve_ai_request", { p_feature: feature });
      if (error) throw Object.assign(new Error(error.message), { code: "limit_reached" });
      return data as string;
    },
    complete: async (id, u) => {
      await serviceClient().from("ai_usage_records").update({ status: u.status, output_tokens: u.outputTokens ?? null, error_code: u.errorCode ?? null }).eq("id", id);
    },
  });
  const req = { feature: "market_brief" as const, coinIds: [], question: "" };

  it("enforces the free daily limit and does not count provider failures", async () => {
    const uid = await newUser(db);
    await expect(runAssistant(deps(uid, failing), req)).rejects.toMatchObject({ code: "unavailable" });
    for (let i = 0; i < 3; i++) expect((await runAssistant(deps(uid, okProvider), req)).aiGenerated).toBe(true);
    await expect(runAssistant(deps(uid, okProvider), req)).rejects.toMatchObject({ code: "limit_reached" });
    const { rows } = await db.query("select status, count(*)::int n from public.ai_usage_records where user_id = $1 group by status order by status", [uid]);
    expect(rows).toEqual([{ status: "error", n: 1 }, { status: "success", n: 3 }]);
  });

  it("reports not_configured when no provider is set", async () => {
    const uid = await newUser(db);
    await expect(runAssistant(deps(uid, null), req)).rejects.toMatchObject({ code: "not_configured" });
  });
});
