import { createHash } from "node:crypto";
import { z } from "zod";
import { SCORE_CATEGORIES, type ScoreCategory, type ScoringConfig } from "@/config/scoring";

/**
 * Transparent, deterministic research score.
 *
 * Properties:
 * - Pure: output depends only on (input, config). `asOf` is an input; the
 *   function never reads the clock.
 * - Missing data is never treated as low quality. A category whose inputs are
 *   missing is reported as "missing" and excluded; the overall score is only
 *   produced when enough weighted coverage remains (config.minWeightCoverage)
 *   and all required categories are present.
 * - Remaining weights are renormalized over available categories. The
 *   coverage fraction is always reported alongside the score.
 *
 * This is a research heuristic, not a prediction of returns and not advice.
 */

export const ScoringInputSchema = z.object({
  coinId: z.string().min(1),
  /** Evaluation time (ISO 8601). Used for staleness checks only. */
  asOf: z.iso.datetime({ offset: true }),
  market: z.object({
    /** When the market inputs were observed (ISO 8601). */
    observedAt: z.iso.datetime({ offset: true }).nullable(),
    priceUsd: z.number().nullable(),
    marketCapUsd: z.number().nullable(),
    fullyDilutedValuationUsd: z.number().nullable(),
    volume24hUsd: z.number().nullable(),
    change7dPct: z.number().nullable(),
    change30dPct: z.number().nullable(),
    circulatingSupply: z.number().nullable(),
    totalSupply: z.number().nullable(),
    maxSupply: z.number().nullable(),
  }),
  fundamentals: z
    .object({
      commits4Weeks: z.number().nullable(),
      githubStars: z.number().nullable(),
      contributors: z.number().nullable(),
      /** Project age in years, if a genesis date is known. */
      ageYears: z.number().nullable(),
    })
    .nullable(),
  /** Daily closing prices, oldest first. */
  dailyCloses: z.array(z.number()).nullable(),
  /** Annualized circulating-supply inflation estimate in percent, if known. */
  estimatedAnnualInflationPct: z.number().nullable(),
  unlocks: z
    .object({
      /** Verified unlocks in the next 90 days as % of circulating supply. */
      next90dPctOfCirculating: z.number(),
      /** Whether the underlying events come from a verified/project source. */
      verified: z.boolean(),
    })
    .nullable(),
});
export type ScoringInput = z.infer<typeof ScoringInputSchema>;

export type SubscoreStatus = "ok" | "missing" | "unreliable";

export interface Subscore {
  category: ScoreCategory;
  status: SubscoreStatus;
  /** 0–100, null when status != ok. */
  score: number | null;
  weight: number;
  /** Human-readable explanation of how the subscore was derived. */
  explanation: string;
  /** Named intermediate metrics for transparency. */
  metrics: Record<string, number | null>;
}

export type ScoreStatus = "scored" | "insufficient_data";

export interface ScoreResult {
  coinId: string;
  methodologyVersion: string;
  status: ScoreStatus;
  /** 0–100 rounded to 1 decimal; null when insufficient data. */
  overallScore: number | null;
  /** Fraction (0–1) of total weight that was computable. */
  coverage: number;
  subscores: Subscore[];
  flags: string[];
  asOf: string;
  dataAsOf: string | null;
  /** SHA-256 over the canonical input + config; identical inputs ⇒ identical hash. */
  inputsHash: string;
}

export class InvalidScoringInputError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InvalidScoringInputError";
  }
}

const clamp = (x: number, lo = 0, hi = 100) => Math.min(hi, Math.max(lo, x));
const round1 = (x: number) => Math.round(x * 10) / 10;
const finitePos = (x: number | null | undefined): x is number => typeof x === "number" && Number.isFinite(x) && x > 0;
const finite = (x: number | null | undefined): x is number => typeof x === "number" && Number.isFinite(x);

/** Map log10(x) linearly from [lo, hi] onto [0, 100]. */
function logScale(x: number, lo: number, hi: number) {
  return clamp(((Math.log10(x) - lo) / (hi - lo)) * 100);
}

export const ScoringConfigSchema = z
  .object({
    methodologyVersion: z.string().regex(/^\d+\.\d+\.\d+$/),
    weights: z.object(Object.fromEntries(SCORE_CATEGORIES.map((c) => [c, z.number().min(0).max(1)])) as Record<ScoreCategory, z.ZodNumber>),
    minWeightCoverage: z.number().min(0).max(1),
    requiredCategories: z.array(z.enum(SCORE_CATEGORIES)),
    maxMarketDataAgeMinutes: z.number().int().positive(),
    minHistoryDays: z.number().int().min(2),
  })
  .refine((c) => Math.abs(Object.values(c.weights).reduce((s, w) => s + w, 0) - 1) < 1e-6, {
    message: "Weights must sum to 1.",
    path: ["weights"],
  });

export function validateScoringConfig(config: unknown): ScoringConfig {
  return ScoringConfigSchema.parse(config) as ScoringConfig;
}

function canonicalJson(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(canonicalJson).join(",")}]`;
  if (v && typeof v === "object") {
    const keys = Object.keys(v as object).sort();
    return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson((v as Record<string, unknown>)[k])}`).join(",")}}`;
  }
  return JSON.stringify(v);
}

export function hashInputs(input: ScoringInput, config: ScoringConfig): string {
  return createHash("sha256").update(canonicalJson({ input, config })).digest("hex");
}

// ---------------------------------------------------------------- subscores

function tokenomics(input: ScoringInput): Omit<Subscore, "weight"> {
  const m = input.market;
  const cap = finitePos(m.maxSupply) ? m.maxSupply : finitePos(m.totalSupply) ? m.totalSupply : null;
  const fdvRatio = finitePos(m.fullyDilutedValuationUsd) && finitePos(m.marketCapUsd) ? m.fullyDilutedValuationUsd / m.marketCapUsd : null;
  const circRatio = finitePos(m.circulatingSupply) && cap ? Math.min(1, m.circulatingSupply / cap) : null;
  const inflation = finite(input.estimatedAnnualInflationPct) ? input.estimatedAnnualInflationPct : null;
  const metrics = { circulatingToSupplyRatio: circRatio, fdvToMarketCap: fdvRatio, estimatedAnnualInflationPct: inflation };

  if (circRatio === null && fdvRatio === null) {
    return { category: "tokenomics", status: "missing", score: null, explanation: "Circulating supply, total/max supply, or FDV not available.", metrics };
  }
  const parts: { score: number; w: number }[] = [];
  // Higher circulating share ⇒ less future dilution.
  if (circRatio !== null) parts.push({ score: circRatio * 100, w: 0.4 });
  // FDV/MC of 1 is ideal; 5x or more scores 0.
  if (fdvRatio !== null) parts.push({ score: clamp(100 - ((Math.max(1, fdvRatio) - 1) / 4) * 100), w: 0.3 });
  // Inflation: 0% ⇒ 100, ≥25%/yr ⇒ 0. Deflation is capped at 100.
  if (inflation !== null) parts.push({ score: clamp(100 - (Math.max(0, inflation) / 25) * 100), w: 0.3 });
  const wsum = parts.reduce((s, p) => s + p.w, 0);
  const score = parts.reduce((s, p) => s + p.score * p.w, 0) / wsum;
  const noMax = !finitePos(m.maxSupply);
  return {
    category: "tokenomics",
    status: "ok",
    score: round1(score),
    explanation: `Based on circulating share of ${noMax ? "total" : "maximum"} supply, FDV/market-cap ratio${inflation === null ? "" : ", and estimated inflation"}.${noMax ? " No fixed maximum supply is reported." : ""}`,
    metrics,
  };
}

function liquidity(input: ScoringInput): Omit<Subscore, "weight"> {
  const { volume24hUsd: vol, marketCapUsd: mc } = input.market;
  const turnover = finitePos(vol) && finitePos(mc) ? vol / mc : null;
  const metrics = { volume24hUsd: finite(vol) ? vol : null, turnoverRatio: turnover };
  if (!finitePos(vol) || !finitePos(mc) || turnover === null) {
    return { category: "liquidity", status: "missing", score: null, explanation: "24h volume or market cap not available.", metrics };
  }
  // Absolute depth: $1M ⇒ 0, $10B ⇒ 100 (log scale).
  const depth = logScale(vol, 6, 10);
  // Turnover: 1%–30% is healthy. Below 0.1% is illiquid; above 100% may indicate wash trading.
  let turn: number;
  if (turnover < 0.001) turn = 0;
  else if (turnover < 0.01) turn = (Math.log10(turnover) + 3) * 100;
  else if (turnover <= 0.3) turn = 100;
  else if (turnover <= 1) turn = 100 - ((turnover - 0.3) / 0.7) * 50;
  else turn = 30;
  return {
    category: "liquidity",
    status: "ok",
    score: round1(0.6 * depth + 0.4 * clamp(turn)),
    explanation: "Combines absolute 24h trading volume (log scale) with volume/market-cap turnover. Very high turnover is penalized as a possible sign of inorganic volume.",
    metrics,
  };
}

function fundamentals(input: ScoringInput): Omit<Subscore, "weight"> {
  const f = input.fundamentals;
  const metrics = {
    commits4Weeks: f?.commits4Weeks ?? null,
    githubStars: f?.githubStars ?? null,
    contributors: f?.contributors ?? null,
    ageYears: f?.ageYears ?? null,
    marketCapUsd: input.market.marketCapUsd,
  };
  const parts: { score: number; w: number }[] = [];
  if (f && finite(f.commits4Weeks) && f.commits4Weeks >= 0) parts.push({ score: logScale(f.commits4Weeks + 1, 0, 2.7), w: 0.35 });
  if (f && finite(f.contributors) && f.contributors >= 0) parts.push({ score: logScale(f.contributors + 1, 0, 2), w: 0.2 });
  if (f && finite(f.githubStars) && f.githubStars >= 0) parts.push({ score: logScale(f.githubStars + 1, 1, 4.5), w: 0.15 });
  if (f && finite(f.ageYears) && f.ageYears >= 0) parts.push({ score: clamp((f.ageYears / 8) * 100), w: 0.15 });
  if (finitePos(input.market.marketCapUsd)) parts.push({ score: logScale(input.market.marketCapUsd, 7, 11.5), w: 0.15 });
  // Require at least one non-market-cap fundamental signal.
  const nonMcap = parts.length - (finitePos(input.market.marketCapUsd) ? 1 : 0);
  if (nonMcap < 1) {
    return { category: "fundamentals", status: "missing", score: null, explanation: "No developer-activity or project-age data available.", metrics };
  }
  const wsum = parts.reduce((s, p) => s + p.w, 0);
  return {
    category: "fundamentals",
    status: "ok",
    score: round1(parts.reduce((s, p) => s + p.score * p.w, 0) / wsum),
    explanation: "Developer activity (commits, contributors, stars), project age, and network size (market cap). Public repository metrics do not capture private or off-GitHub development.",
    metrics,
  };
}

function momentum(input: ScoringInput): Omit<Subscore, "weight"> {
  const { change7dPct: c7, change30dPct: c30 } = input.market;
  const metrics = { change7dPct: finite(c7) ? c7 : null, change30dPct: finite(c30) ? c30 : null };
  if (!finite(c7) && !finite(c30)) {
    return { category: "momentum", status: "missing", score: null, explanation: "7d and 30d price changes not available.", metrics };
  }
  const s = (pct: number, scale: number) => 50 + 50 * Math.tanh(pct / scale);
  const parts: { score: number; w: number }[] = [];
  if (finite(c7)) parts.push({ score: s(c7, 15), w: 0.4 });
  if (finite(c30)) parts.push({ score: s(c30, 30), w: 0.6 });
  const wsum = parts.reduce((a, p) => a + p.w, 0);
  return {
    category: "momentum",
    status: "ok",
    score: round1(parts.reduce((a, p) => a + p.score * p.w, 0) / wsum),
    explanation: "Smoothed (tanh) mapping of 7-day and 30-day price change; 50 is neutral. Past momentum does not predict future returns.",
    metrics,
  };
}

function unlockPressure(input: ScoringInput): Omit<Subscore, "weight"> {
  const u = input.unlocks;
  const metrics = { next90dPctOfCirculating: u?.next90dPctOfCirculating ?? null };
  if (!u) {
    return { category: "unlockPressure", status: "missing", score: null, explanation: "No verified token-unlock schedule is available for this asset.", metrics };
  }
  if (!u.verified) {
    return { category: "unlockPressure", status: "unreliable", score: null, explanation: "Unlock data exists but is not from a verified or project-reported source.", metrics };
  }
  // 0% ⇒ 100; ≥10% of circulating within 90 days ⇒ 0.
  return {
    category: "unlockPressure",
    status: "ok",
    score: round1(clamp(100 - (Math.max(0, u.next90dPctOfCirculating) / 10) * 100)),
    explanation: "Scheduled unlocks over the next 90 days relative to circulating supply.",
    metrics,
  };
}

export function dailyVolatility(closes: readonly number[]): number | null {
  const rets: number[] = [];
  for (let i = 1; i < closes.length; i++) {
    const a = closes[i - 1]!;
    const b = closes[i]!;
    if (a > 0 && b > 0) rets.push(Math.log(b / a));
  }
  if (rets.length < 2) return null;
  const mean = rets.reduce((s, r) => s + r, 0) / rets.length;
  const variance = rets.reduce((s, r) => s + (r - mean) ** 2, 0) / (rets.length - 1);
  return Math.sqrt(variance);
}

function riskVolatility(input: ScoringInput, config: ScoringConfig): Omit<Subscore, "weight"> {
  const closes = input.dailyCloses?.filter((c) => finitePos(c)) ?? [];
  const vol = closes.length >= config.minHistoryDays ? dailyVolatility(closes) : null;
  const annualized = vol === null ? null : vol * Math.sqrt(365) * 100;
  const metrics = { annualizedVolatilityPct: annualized, historyDays: closes.length };
  if (annualized === null) {
    return { category: "riskVolatility", status: "missing", score: null, explanation: `At least ${config.minHistoryDays} daily closes are required to estimate volatility.`, metrics };
  }
  // 20% annualized ⇒ 100, 200%+ ⇒ 0.
  return {
    category: "riskVolatility",
    status: "ok",
    score: round1(clamp(100 - ((annualized - 20) / 180) * 100)),
    explanation: "Annualized standard deviation of daily log returns. Higher volatility scores lower.",
    metrics,
  };
}

// ------------------------------------------------------------------ engine

export function computeScore(rawInput: unknown, rawConfig: ScoringConfig): ScoreResult {
  const config = validateScoringConfig(rawConfig);
  const parsed = ScoringInputSchema.safeParse(rawInput);
  if (!parsed.success) {
    throw new InvalidScoringInputError(parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; "));
  }
  const input = parsed.data;
  const flags: string[] = [];

  // Staleness / validity of market inputs.
  let marketReliable = true;
  if (!input.market.observedAt) {
    marketReliable = false;
    flags.push("market_data_timestamp_missing");
  } else {
    const ageMin = (Date.parse(input.asOf) - Date.parse(input.market.observedAt)) / 60_000;
    if (ageMin > config.maxMarketDataAgeMinutes) {
      marketReliable = false;
      flags.push("market_data_stale");
    }
    if (ageMin < -5) {
      marketReliable = false;
      flags.push("market_data_timestamp_in_future");
    }
  }
  const negative = (["priceUsd", "marketCapUsd", "volume24hUsd", "circulatingSupply", "totalSupply", "maxSupply", "fullyDilutedValuationUsd"] as const).filter(
    (k) => finite(input.market[k]) && (input.market[k] as number) < 0,
  );
  if (negative.length) {
    marketReliable = false;
    flags.push(`negative_values:${negative.join(",")}`);
  }
  const m = input.market;
  if (finitePos(m.circulatingSupply) && finitePos(m.maxSupply) && m.circulatingSupply > m.maxSupply * 1.01) {
    flags.push("circulating_exceeds_max_supply");
  }

  const raw: Omit<Subscore, "weight">[] = [
    tokenomics(input),
    liquidity(input),
    fundamentals(input),
    momentum(input),
    unlockPressure(input),
    riskVolatility(input, config),
  ];
  const marketDependent: ScoreCategory[] = ["tokenomics", "liquidity", "momentum"];
  const subscores: Subscore[] = raw.map((s) => {
    const weight = config.weights[s.category];
    if (!marketReliable && marketDependent.includes(s.category) && s.status === "ok") {
      return { ...s, weight, status: "unreliable", score: null, explanation: `${s.explanation} Excluded: market inputs are stale or invalid.` };
    }
    return { ...s, weight };
  });

  const available = subscores.filter((s) => s.status === "ok" && s.weight > 0);
  const coverage = round4(available.reduce((s, x) => s + x.weight, 0));
  const missingRequired = config.requiredCategories.filter((c) => !available.some((s) => s.category === c));
  for (const s of subscores) if (s.status !== "ok") flags.push(`${s.category}_${s.status}`);
  if (missingRequired.length) flags.push(`required_missing:${missingRequired.join(",")}`);

  const sufficient = missingRequired.length === 0 && coverage + 1e-9 >= config.minWeightCoverage && available.length > 0;
  const overall = sufficient ? round1(available.reduce((s, x) => s + (x.score as number) * x.weight, 0) / coverage) : null;

  return {
    coinId: input.coinId,
    methodologyVersion: config.methodologyVersion,
    status: sufficient ? "scored" : "insufficient_data",
    overallScore: overall,
    coverage,
    subscores,
    flags,
    asOf: input.asOf,
    dataAsOf: input.market.observedAt,
    inputsHash: hashInputs(input, config),
  };
}

const round4 = (x: number) => Math.round(x * 10_000) / 10_000;

export function scoreBand(score: number | null): { label: string; tone: "strong" | "moderate" | "weak" | "none" } {
  if (score === null) return { label: "Insufficient data", tone: "none" };
  if (score >= 70) return { label: "Strong", tone: "strong" };
  if (score >= 45) return { label: "Moderate", tone: "moderate" };
  return { label: "Weak", tone: "weak" };
}
