/**
 * Racom's own products.
 *
 * PLACEHOLDER: the entries below are illustrative examples (`example: true`)
 * that show how products are presented. Replace them with real products.
 *
 * Rules enforced by tests (tests/unit/content.test.ts):
 * - Only `status: "available"` products may have an external `url`, and
 *   every available product must have one.
 * - Example products can never be marked "available".
 */

export type ProductStatus = "concept" | "in-development" | "available";

export type ProductArtVariant = "pulse" | "flow" | "desk";

export type Product = {
  slug: string;
  name: string;
  tagline: string;
  description: string;
  status: ProductStatus;
  /** True for illustrative placeholder products that are not real yet. */
  example: boolean;
  audience: string;
  features: string[];
  art: ProductArtVariant;
  /** Public product URL — only for available products. */
  url?: string;
};

export const productStatusLabel: Record<ProductStatus, string> = {
  concept: "Concept",
  "in-development": "In development",
  available: "Available",
};

export const products: Product[] = [
  {
    slug: "racom-flow",
    name: "Racom Flow",
    tagline: "Workflow automation for growing teams",
    description:
      "An example product concept: a simple way for small teams to turn repeated approval and hand-off processes into tracked, automated workflows.",
    status: "concept",
    example: true,
    audience: "Operations teams in small and mid-sized businesses",
    features: [
      "Visual approval flows",
      "Notifications by email and messaging apps",
      "Audit trail of every step",
    ],
    art: "flow",
  },
  {
    slug: "racom-pulse",
    name: "Racom Pulse",
    tagline: "Business metrics in one quiet dashboard",
    description:
      "An example product concept: a lightweight dashboard that gathers key numbers from the tools a business already uses and highlights only what changed.",
    status: "concept",
    example: true,
    audience: "Founders and managers who need a weekly overview",
    features: [
      "Connectors to common business tools",
      "Change highlights instead of chart overload",
      "Weekly email digest",
    ],
    art: "pulse",
  },
  {
    slug: "racom-desk",
    name: "Racom Desk",
    tagline: "AI-assisted support with people in control",
    description:
      "An example product concept: a shared inbox that sorts and drafts replies to customer requests, with every answer reviewed by a person before it is sent.",
    status: "concept",
    example: true,
    audience: "Customer support and service teams",
    features: ["Automatic categorisation", "Draft replies for review", "Response-time tracking"],
    art: "desk",
  },
];
