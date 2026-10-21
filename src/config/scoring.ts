/**
 * Default research-score methodology configuration.
 *
 * These weights are PROVISIONAL. Administrators can publish a new
 * configuration via the admin dashboard (stored in `scoring_configs`); this
 * file provides the default used when no database configuration exists.
 *
 * The score is a research heuristic. It is not a prediction of future returns
 * and it is not personalized financial advice.
 */

export const SCORE_CATEGORIES = [
  "tokenomics",
  "liquidity",
  "fundamentals",
  "momentum",
  "unlockPressure",
  "riskVolatility",
] as const;

export type ScoreCategory = (typeof SCORE_CATEGORIES)[number];

export const CATEGORY_LABELS: Record<ScoreCategory, string> = {
  tokenomics: "Tokenomics & supply inflation",
  liquidity: "Liquidity & trading activity",
  fundamentals: "Fundamental project metrics",
  momentum: "Price momentum",
  unlockPressure: "Token unlock & supply pressure",
  riskVolatility: "Risk & volatility",
};

export interface ScoringConfig {
  /** Semantic version of the scoring *algorithm*. Bump when formulas change. */
  methodologyVersion: string;
  /** Category weights; must be non-negative and sum to 1. */
  weights: Record<ScoreCategory, number>;
  /**
   * Minimum fraction of total weight that must be computable for an overall
   * score to be produced. Below this the result is "insufficient_data".
   */
  minWeightCoverage: number;
  /** Categories that must be present for an overall score. */
  requiredCategories: ScoreCategory[];
  /** Market inputs older than this (relative to `asOf`) are treated as unreliable. */
  maxMarketDataAgeMinutes: number;
  /** Minimum number of daily closes needed for volatility. */
  minHistoryDays: number;
}

export const DEFAULT_SCORING_CONFIG: ScoringConfig = {
  methodologyVersion: "1.0.0",
  weights: {
    tokenomics: 0.25,
    liquidity: 0.2,
    fundamentals: 0.2,
    momentum: 0.15,
    unlockPressure: 0.1,
    riskVolatility: 0.1,
  },
  minWeightCoverage: 0.6,
  requiredCategories: ["tokenomics", "liquidity"],
  maxMarketDataAgeMinutes: 360,
  minHistoryDays: 20,
};
