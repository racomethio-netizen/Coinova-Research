/**
 * Centralized branding and business settings.
 *
 * Rename the product by editing this file (and NEXT_PUBLIC_SITE_URL in the
 * environment). Nothing else in the codebase should hard-code the brand name.
 */
export const siteConfig = {
  name: "CryptoScope Research",
  shortName: "CryptoScope",
  tagline: "Transparent, data-driven cryptocurrency research.",
  description:
    "Independent cryptocurrency research with transparent scoring, tokenomics analysis, portfolio tracking, and alerts.",
  supportEmail: "support@example.com",
  legalEntity: "CryptoScope Research (placeholder entity — replace before launch)",
  social: {
    // Leave empty until real accounts exist. Never link to accounts you do not own.
    x: "",
    telegram: "",
  },
  /** Shown on every page footer and on research pages. */
  disclaimer:
    "Research on this site is for informational and educational purposes only. It is not financial, investment, legal, or tax advice, and it is not a recommendation to buy or sell any asset. Cryptocurrencies are highly volatile and you can lose all of your investment.",
} as const;

export type SiteConfig = typeof siteConfig;
