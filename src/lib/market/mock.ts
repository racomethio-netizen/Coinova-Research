import type {
  CoinDetails,
  CoinMarket,
  GlobalMarketData,
  HistoryPeriod,
  ListCoinsParams,
  MarketDataProvider,
  PricePoint,
  SupplyData,
} from "./types";

/**
 * DEVELOPMENT-ONLY mock market-data provider.
 *
 * Every asset is FICTIONAL (names end in "(Mock)", symbols start with "MK")
 * so mock output can never be mistaken for a real asset's price. Values are
 * generated deterministically from a seed so tests are reproducible.
 * The environment validator refuses this provider when APP_ENV=production,
 * and the UI shows a persistent "Mock data" banner whenever it is active.
 */

const NAMES = [
  "Aurora", "Basalt", "Cinder", "Delta Reef", "Ember", "Fjord", "Glacier", "Harbor", "Iris", "Juniper",
  "Kestrel", "Lumen", "Meridian", "Nimbus", "Orchid", "Pinnacle", "Quartz", "Riverstone", "Sable", "Tundra",
  "Umbra", "Vertex", "Willow", "Xenon", "Yarrow", "Zephyr", "Atlas Grove", "Bramble", "Cobalt", "Dune",
];

function mulberry32(seed: number) {
  return () => {
    let t = (seed += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

interface MockAsset {
  base: CoinMarket;
  details: Omit<CoinDetails, keyof CoinMarket>;
}

export const MOCK_FIXED_TIME = Date.UTC(2026, 0, 1, 12, 0, 0);

function buildAssets(): MockAsset[] {
  return NAMES.map((name, idx) => {
    const id = `mock-${name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
    const rnd = mulberry32(hash(id));
    const price = Number((Math.pow(10, 4 - idx * 0.22) * (0.5 + rnd())).toPrecision(6));
    const circ = Math.round(Math.pow(10, 6 + rnd() * 4));
    const hasMax = rnd() > 0.35;
    const max = hasMax ? Math.round(circ * (1 + rnd() * 1.5)) : null;
    const total = max ?? Math.round(circ * (1 + rnd() * 0.3));
    const mcap = price * circ;
    const devMissing = idx % 7 === 6;
    return {
      base: {
        id,
        symbol: `MK${name.replace(/[^A-Za-z]/g, "").slice(0, 3).toUpperCase()}`,
        name: `${name} (Mock)`,
        imageUrl: null,
        marketCapRank: idx + 1,
        priceUsd: price,
        marketCapUsd: mcap,
        fullyDilutedValuationUsd: max ? price * max : null,
        volume24hUsd: mcap * (0.01 + rnd() * 0.25),
        high24hUsd: price * (1 + rnd() * 0.05),
        low24hUsd: price * (1 - rnd() * 0.05),
        change1hPct: (rnd() - 0.5) * 2,
        change24hPct: (rnd() - 0.5) * 12,
        change7dPct: (rnd() - 0.5) * 30,
        change30dPct: (rnd() - 0.5) * 60,
        circulatingSupply: circ,
        totalSupply: total,
        maxSupply: max,
        athUsd: price * (1.2 + rnd() * 3),
        athChangePct: -(10 + rnd() * 80),
        lastUpdated: null,
      },
      details: {
        description: `${name} (Mock) is a fictional asset generated for local development. It does not exist and its figures are not real market data.`,
        categories: ["Mock Data"],
        homepageUrl: null,
        whitepaperUrl: null,
        githubRepos: [],
        genesisDate: null,
        hashingAlgorithm: null,
        developer: devMissing
          ? null
          : { commits4Weeks: Math.round(rnd() * 400), stars: Math.round(rnd() * 20000), forks: Math.round(rnd() * 5000), contributors: Math.round(rnd() * 120) },
      },
    };
  });
}

export class MockMarketDataProvider implements MarketDataProvider {
  readonly source = "mock" as const;
  private readonly assets = buildAssets();

  constructor(private readonly now: () => number = () => Date.now()) {}

  private stamp(m: CoinMarket): CoinMarket {
    return { ...m, lastUpdated: new Date(this.now()).toISOString() };
  }

  async getCoins({ page, perPage, ids }: ListCoinsParams): Promise<CoinMarket[]> {
    let list = this.assets.map((a) => a.base);
    if (ids?.length) list = list.filter((c) => ids.includes(c.id));
    const start = (Math.max(1, page) - 1) * perPage;
    return list.slice(start, start + perPage).map((m) => this.stamp(m));
  }

  async getMarketData(coinIds: readonly string[]): Promise<CoinMarket[]> {
    return this.assets.filter((a) => coinIds.includes(a.base.id)).map((a) => this.stamp(a.base));
  }

  async getCoinDetails(coinId: string): Promise<CoinDetails | null> {
    const a = this.assets.find((x) => x.base.id === coinId);
    return a ? { ...this.stamp(a.base), ...a.details } : null;
  }

  async getHistoricalPrices(coinId: string, period: HistoryPeriod): Promise<PricePoint[]> {
    const a = this.assets.find((x) => x.base.id === coinId);
    if (!a || a.base.priceUsd === null) return [];
    const days = { "1d": 1, "7d": 7, "30d": 30, "90d": 90, "365d": 365 }[period];
    const points = period === "1d" ? 96 : days;
    const step = (days * 86_400_000) / points;
    const rnd = mulberry32(hash(coinId + period));
    const end = Math.floor(this.now() / step) * step;
    // Random walk backward from the current price so the series ends at "now".
    const out: PricePoint[] = [];
    let p = a.base.priceUsd;
    for (let i = 0; i <= points; i++) {
      out.push({ t: end - i * step, priceUsd: p, marketCapUsd: a.base.circulatingSupply ? p * a.base.circulatingSupply : null, volumeUsd: a.base.volume24hUsd });
      p = Math.max(p * (1 + (rnd() - 0.5) * 0.06), 1e-9);
    }
    return out.reverse();
  }

  async getGlobalMarketData(): Promise<GlobalMarketData> {
    const total = this.assets.reduce((s, a) => s + (a.base.marketCapUsd ?? 0), 0);
    const vol = this.assets.reduce((s, a) => s + (a.base.volume24hUsd ?? 0), 0);
    return {
      totalMarketCapUsd: total,
      totalVolume24hUsd: vol,
      btcDominancePct: ((this.assets[0]!.base.marketCapUsd ?? 0) / total) * 100,
      ethDominancePct: ((this.assets[1]!.base.marketCapUsd ?? 0) / total) * 100,
      marketCapChange24hPct: 1.23,
      activeCryptocurrencies: this.assets.length,
      updatedAt: new Date(this.now()).toISOString(),
    };
  }

  async getSupplyData(coinId: string): Promise<SupplyData | null> {
    const a = this.assets.find((x) => x.base.id === coinId);
    if (!a) return null;
    const m = a.base;
    return {
      coinId,
      circulatingSupply: m.circulatingSupply,
      totalSupply: m.totalSupply,
      maxSupply: m.maxSupply,
      marketCapUsd: m.marketCapUsd,
      fullyDilutedValuationUsd: m.fullyDilutedValuationUsd,
      priceUsd: m.priceUsd,
      lastUpdated: new Date(this.now()).toISOString(),
    };
  }

  /** IDs of every mock asset; used as the universe when the mock provider is active. */
  universe(): string[] {
    return this.assets.map((a) => a.base.id);
  }
}
