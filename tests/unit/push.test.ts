import { describe, expect, it } from "vitest";
import { EXPO_PUSH_URL, sendExpoPush } from "@/lib/notify/push";

const tokens = ["ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]", "ExponentPushToken[bbbbbbbbbbbbbbbbbbbbbb]"];
const reply = (status: number, body: unknown) => (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

describe("Expo push sender", () => {
  it("sends one message per token and reports dead tokens for revocation", async () => {
    let sent: unknown;
    const f = (async (url: string, init: RequestInit) => {
      expect(url).toBe(EXPO_PUSH_URL);
      sent = JSON.parse(String(init.body));
      return new Response(JSON.stringify({ data: [{ status: "ok", id: "1" }, { status: "error", details: { error: "DeviceNotRegistered" } }] }), { status: 200 });
    }) as unknown as typeof fetch;
    const r = await sendExpoPush(tokens, "BTC alert", "Price above 100", { fetchImpl: f });
    expect(r).toEqual({ ok: true, deadTokens: [tokens[1]], sent: 1 });
    expect(sent).toEqual(tokens.map((to) => ({ to, title: "BTC alert", body: "Price above 100", sound: "default" })));
  });

  it("treats no devices as permanent, 5xx as retryable, 4xx as permanent", async () => {
    expect(await sendExpoPush([], "t", "b")).toMatchObject({ ok: false, permanent: true });
    const retry = await sendExpoPush(tokens, "t", "b", { fetchImpl: reply(503, {}) });
    expect(retry.ok).toBe(false);
    expect(retry.permanent).toBeFalsy();
    expect(await sendExpoPush(tokens, "t", "b", { fetchImpl: reply(400, {}) })).toMatchObject({ ok: false, permanent: true });
    const allDead = await sendExpoPush(tokens, "t", "b", { fetchImpl: reply(200, { data: tokens.map(() => ({ status: "error", details: { error: "DeviceNotRegistered" } })) }) });
    expect(allDead).toMatchObject({ ok: false, permanent: true, deadTokens: tokens });
  });

  it("network failures are retryable", async () => {
    const boom = (async () => { throw new TypeError("fetch failed"); }) as unknown as typeof fetch;
    expect(await sendExpoPush(tokens, "t", "b", { fetchImpl: boom })).toMatchObject({ ok: false, error: "push service unreachable" });
  });
});
