/**
 * Central company configuration: identity, copy, navigation and contact channels.
 *
 * Everything a non-developer may need to change lives here or in `src/content/*`.
 * Values set to `null` are intentionally empty: the UI hides a channel until it
 * is configured rather than showing invented details. Search the codebase for
 * "PLACEHOLDER" to find copy that still needs real company information.
 */

export type SocialLink = {
  label: string;
  href: string;
};

export const site = {
  name: "Racom Technology",
  shortName: "Racom",
  wordmark: "RACOM",
  slogan: "Engineering What’s Next.",
  /** Primary positioning statement (proposed brand copy). */
  message:
    "We engineer intelligent software and exceptional digital experiences that help ambitious businesses move forward.",
  supportingMessage:
    "From custom software and AI-powered automation to scalable digital products, Racom Technology turns complex challenges into practical technology.",
  description:
    "Racom Technology is a software engineering and digital products company building custom software, web applications, AI automation and SaaS products for businesses in Ethiopia and international markets.",
  markets: "Ethiopia and international markets",
  locale: "en",

  /**
   * Public contact channels. `null` hides the channel everywhere.
   * PLACEHOLDER: add the real inquiry email (and optionally phone / city) before launch.
   */
  contact: {
    email: null as string | null,
    phone: null as string | null,
    /** City/country only, e.g. "Addis Ababa, Ethiopia". Do not add a street address unless it is real. */
    location: null as string | null,
  },

  /** PLACEHOLDER: add official profiles, e.g. { label: "LinkedIn", href: "https://www.linkedin.com/company/…" }. */
  social: [] as SocialLink[],

  /**
   * Legal entity details used in the legal pages.
   * PLACEHOLDER: fill in with the registered details after legal review.
   */
  legal: {
    entityName: null as string | null,
    registeredAddress: null as string | null,
    governingLaw: null as string | null,
    lastUpdated: "2026-10-22",
  },
} as const;

export type NavItem = { label: string; href: string };

export const mainNav: NavItem[] = [
  { label: "Services", href: "/services" },
  { label: "Work", href: "/work" },
  { label: "Products", href: "/products" },
  { label: "About", href: "/about" },
  { label: "Insights", href: "/insights" },
  { label: "Contact", href: "/contact" },
];

export const primaryCta = { label: "Start a Project", href: "/contact" } as const;

export const legalNav: NavItem[] = [
  { label: "Privacy Policy", href: "/privacy" },
  { label: "Terms of Use", href: "/terms" },
];

/** Absolute site origin when configured (production), otherwise null. */
export function getSiteUrl(): string | null {
  const raw = process.env.NEXT_PUBLIC_SITE_URL?.trim();
  if (!raw) return null;
  try {
    const url = new URL(raw);
    return url.origin;
  } catch {
    return null;
  }
}
