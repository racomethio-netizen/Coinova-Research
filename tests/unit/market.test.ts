import { describe, expect, it, vi } from "vitest";
import { StaleWhileErrorCache } from "@/lib/market/cache";
import { CoinGeckoProvider, mapMarketItem, safeHttpsUrl, stripHtml } from "@/lib/market/coingecko";
import { MockMarketDataProvider } from "@/lib/market/mock";
import { MarketDataService } from "@/lib/market/service";
import { MarketDataError } from "@/lib/market/types";

const marketItem = {
  id: "bitcoin",
  symbol: "btc",
  name: "Bitcoin",
  image: "https://assets.example.com/btc.png",
  current_price: 65000,
  market_cap: 1.28e12,
  market_cap_rank: 1,
  fully_diluted_valuation: 1.36e12,
  total_volume: 3.1e10,
  high_24h: 66000,
  low_24h: 64000,
  price_change_percentage_24h: 1.2,
  price_change_percentage_1h_in_currency: 0.1,
  price_change_percentage_24h_in_currency: 1.2,
  price_change_percentage_7d_in_currency: -2.5,
  price_change_percentage_30d_in_currency: 8,
  circulating_supply: 19_700_000,
  total_supply: 21_000_000,
  max_supply: 21_000_000,
  ath: 73000,
  ath_change_percentage: -11,
  last_updated: "2026-10-01T12:00:00.000Z",
};

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
}

function provider(fetchImpl: typeof fetch, plan: "demo" | "pro" | "public" = "demo") {
  return new CoinGeckoProvider({ plan, apiKey: "test-key", timeoutMs: 1000, fetchImpl, retry: { retries: 2, baseDelayMs: 1, maxDelayMs: 2 } });
}

describe("CoinGeckoProvider", () => {
  it("maps a successful markets response and sends the demo key as a header", async () => {
    const fetchImpl = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
      expect((init!.headers as Record<string, string>)["x-cg-demo-api-key"]).toBe("test-key");
      return jsonResponse([marketItem]);
    });
    const rows = await provider(fetchImpl as unknown as typeof fetch).getMarketData(["bitcoin"]);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ id: "bitcoin", symbol: "BTC", priceUsd: 65000, maxSupply: 21_000_000, change7dPct: -2.5 });
    const url = new URL(String(fetchImpl.mock.calls[0]![0]));
    expect(url.host).toBe("api.coingecko.com");
    expect(url.searchParams.get("ids")).toBe("bitcoin");
    expect(url.searchParams.has("x_cg_demo_api_key")).toBe(false);
  });

  it("uses the pro host and header for the pro plan", async () => {
    const fetchImpl = vi.fn(async (_u: string | URL | Request, init?: RequestInit) => {
      expect((init!.headers as Record<string, string>)["x-cg-pro-api-key"]).toBe("test-key");
      return jsonResponse([marketItem]);
    });
    await provider(fetchImpl as unknown as typeof fetch, "pro").getMarketData(["bitcoin"]);
    expect(new URL(String(fetchImpl.mock.calls[0]![0])).host).toBe("pro-api.coingecko.com");
  });

  it("batches large ID lists using the bulk endpoint (≤250 per call)", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse([]));
    const ids = Array.from({ length: 600 }, (_, i) => `coin-${i}`);
    await provider(fetchImpl as unknown as typeof fetch).getMarketData(ids);
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  });

  it("retries on 429 with bounded attempts and then reports rate_limited", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ error: "rate" }, 429, { "retry-after": "0" }));
    await expect(provider(fetchImpl as unknown as typeof fetch).getMarketData(["bitcoin"])).rejects.toMatchObject({ kind: "rate_limited" });
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  });

  it("recovers when a retry succeeds", async () => {
    let n = 0;
    const fetchImpl = vi.fn(async () => (n++ === 0 ? jsonResponse({}, 503) : jsonResponse([marketItem])));
    const rows = await provider(fetchImpl as unknown as typeof fetch).getMarketData(["bitcoin"]);
    expect(rows).toHaveLength(1);
  });

  it("does not retry on 4xx other than 429", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({}, 401));
    await expect(provider(fetchImpl as unknown as typeof fetch).getMarketData(["bitcoin"])).rejects.toMatchObject({ kind: "unavailable" });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("rejects invalid response shapes instead of coercing them", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse([{ ...marketItem, current_price: "65000" }]));
    await expect(provider(fetchImpl as unknown as typeof fetch).getMarketData(["bitcoin"])).rejects.toBeInstanceOf(MarketDataError);
  });

  it("rejects malformed JSON", async () => {
    const fetchImpl = vi.fn(async () => new Response("<html>", { status: 200 }));
    await expect(provider(fetchImpl as unknown as typeof fetch).getMarketData(["bitcoin"])).rejects.toMatchObject({ kind: "invalid_response" });
  });

  it("reports provider outage (network error) as unavailable", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError("fetch failed");
    });
    await expect(provider(fetchImpl as unknown as typeof fetch).getMarketData(["bitcoin"])).rejects.toMatchObject({ kind: "unavailable" });
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  });

  it("returns null for unknown coins and rejects unsafe IDs without calling the network", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ error: "coin not found" }, 404));
    const p = provider(fetchImpl as unknown as typeof fetch);
    expect(await p.getCoinDetails("does-not-exist")).toBeNull();
    expect(await p.getCoinDetails("../../etc")).toBeNull();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("keeps missing values null (never zero)", () => {
    const m = mapMarketItem({ ...marketItem, max_supply: null, total_volume: null, current_price: -1 });
    expect(m.maxSupply).toBeNull();
    expect(m.volume24hUsd).toBeNull();
    expect(m.priceUsd).toBeNull();
  });

  it("parses history, sorts and de-duplicates points", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ prices: [[2000, 2], [1000, 1], [2000, 2]], market_caps: [[1000, 10]], total_volumes: [] }));
    const pts = await provider(fetchImpl as unknown as typeof fetch).getHistoricalPrices("bitcoin", "7d");
    expect(pts.map((p) => p.t)).toEqual([1000, 2000]);
    expect(pts[0]!.marketCapUsd).toBe(10);
  });
});

describe("helpers", () => {
  it("strips HTML from descriptions", () => {
    expect(stripHtml('<a href="x">Bitcoin</a> &amp; more')).toBe("Bitcoin & more");
  });
  it("only allows https URLs", () => {
    expect(safeHttpsUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpsUrl("http://example.com")).toBeNull();
    expect(safeHttpsUrl("https://example.com/a")).toBe("https://example.com/a");
  });
});

describe("StaleWhileErrorCache", () => {
  it("serves fresh values, coalesces concurrent loads, and refreshes after TTL", async () => {
    let now = 0;
    const cache = new StaleWhileErrorCache(10, () => now);
    const loader = vi.fn(async () => "v1");
    const [a, b] = await Promise.all([cache.get("k", 100, 1000, loader), cache.get("k", 100, 1000, loader)]);
    expect(loader).toHaveBeenCalledTimes(1);
    expect(a.value).toBe("v1");
    expect(b.stale).toBe(false);
    now = 50;
    await cache.get("k", 100, 1000, loader);
    expect(loader).toHaveBeenCalledTimes(1);
    now = 150;
    await cache.get("k", 100, 1000, async () => "v2");
    expect((await cache.get("k", 100, 1000, loader)).value).toBe("v2");
  });

  it("returns stale data (flagged) when refresh fails within the stale window", async () => {
    let now = 0;
    const cache = new StaleWhileErrorCache(10, () => now);
    await cache.get("k", 100, 1000, async () => "old");
    now = 500;
    const r = await cache.get("k", 100, 1000, async () => {
      throw new Error("down");
    });
    expect(r).toMatchObject({ value: "old", stale: true, fetchedAt: 0 });
  });

  it("propagates the error when cached data is too old (never serves it as live)", async () => {
    let now = 0;
    const cache = new StaleWhileErrorCache(10, () => now);
    await cache.get("k", 100, 1000, async () => "old");
    now = 5000;
    await expect(
      cache.get("k", 100, 1000, async () => {
        throw new Error("down");
      }),
    ).rejects.toThrow("down");
  });
});

describe("MarketDataService with mock provider", () => {
  it("labels results with the mock source and fictional names", async () => {
    const p = new MockMarketDataProvider(() => Date.UTC(2026, 9, 1));
    const svc = new MarketDataService(p, p.universe());
    const r = await svc.getUniverseMarkets();
    expect(r.source).toBe("mock");
    expect(r.data.length).toBeGreaterThan(10);
    expect(r.data.every((c) => c.name.endsWith("(Mock)") && c.id.startsWith("mock-"))).toBe(true);
  });

  it("returns null for coins outside the supported universe", async () => {
    const p = new MockMarketDataProvider();
    const svc = new MarketDataService(p, p.universe());
    expect((await svc.getCoin("bitcoin")).data).toBeNull();
  });

  it("is deterministic for the same clock", async () => {
    const t = () => Date.UTC(2026, 9, 1);
    const a = await new MockMarketDataProvider(t).getHistoricalPrices("mock-aurora", "30d");
    const b = await new MockMarketDataProvider(t).getHistoricalPrices("mock-aurora", "30d");
    expect(a).toEqual(b);
    expect(a.length).toBe(31);
  });
});
