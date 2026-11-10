import { SignalsResponseSchema } from "@nexus/shared";
import { describe, expect, it } from "vitest";
import { createApi } from "./api";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

describe("mobile API client", () => {
  it("sends the bearer token, never cookies, and validates responses against the shared contract", async () => {
    let seen: RequestInit | undefined;
    const api = createApi("https://example.test", async () => "access-token", (async (_u: string, init: RequestInit) => {
      seen = init;
      return json(200, { ok: true, data: { gate: "staff", signals: [] } });
    }) as unknown as typeof fetch);
    const r = await api.call("/api/signals", SignalsResponseSchema);
    expect(r).toEqual({ ok: true, data: { gate: "staff", signals: [] } });
    expect((seen?.headers as Record<string, string>).authorization).toBe("Bearer access-token");
    expect(seen?.credentials).toBeUndefined();
  });

  it("reports server errors and schema mismatches instead of rendering them as data", async () => {
    const err = createApi("https://x.test", async () => null, (async () => json(402, { ok: false, error: { code: "payment_required", message: "Upgrade" } })) as unknown as typeof fetch);
    expect(await err.call("/api/signals", SignalsResponseSchema)).toEqual({ ok: false, error: { code: "payment_required", message: "Upgrade" } });
    const bad = createApi("https://x.test", async () => null, (async () => json(200, { ok: true, data: { gate: "staff", signals: [{ id: 1 }] } })) as unknown as typeof fetch);
    expect(await bad.call("/api/signals", SignalsResponseSchema)).toMatchObject({ ok: false, error: { code: "invalid_response" } });
  });

  it("handles offline and unconfigured builds", async () => {
    const offline = createApi("https://x.test", async () => null, (async () => { throw new TypeError("Network request failed"); }) as unknown as typeof fetch);
    expect(await offline.call("/x", SignalsResponseSchema)).toMatchObject({ ok: false, error: { code: "network_error" } });
    expect(await createApi("", async () => null).call("/x", SignalsResponseSchema)).toMatchObject({ ok: false, error: { code: "not_configured" } });
  });
});
