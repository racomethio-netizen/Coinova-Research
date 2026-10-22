/**
 * Portfolio entries.
 *
 * `kind: "concept"` marks self-initiated design/engineering explorations that
 * are NOT client engagements. The UI labels them prominently. Replace or add
 * real client work with `kind: "client"`, and only fill `results` with
 * outcomes that are real, verified and approved by the client for publication.
 */

export type ProjectKind = "concept" | "client";

export type ProjectCategory = "Web Application" | "SaaS" | "AI Automation" | "Integration";

export type ProjectArtVariant = "dashboard" | "dispatch" | "triage" | "network";

export type Project = {
  slug: string;
  title: string;
  kind: ProjectKind;
  category: ProjectCategory;
  /** One-line description for cards. */
  summary: string;
  overview: string;
  problem: string;
  approach: string[];
  stack: string[];
  /** What the concept explores (design and engineering decisions) — not business outcomes. */
  highlights: string[];
  /** Verified results only. Leave empty for concepts. */
  results: string[];
  art: ProjectArtVariant;
  /** For client work only: client name, if the client approved publication. */
  client?: string;
};

export const projects: Project[] = [
  {
    slug: "ledgerline",
    title: "Ledgerline",
    kind: "concept",
    category: "SaaS",
    summary:
      "A concept for invoicing and cash-flow visibility built for small businesses working in more than one currency.",
    overview:
      "Ledgerline is a self-initiated concept exploring how a small business could issue invoices, track payments and understand its cash position in one calm, focused interface — including businesses that bill in both Ethiopian birr and foreign currencies.",
    problem:
      "Many small businesses manage invoices across spreadsheets, messaging apps and bank statements. Knowing who owes what, in which currency, usually requires manual reconciliation at the end of the month.",
    approach: [
      "Modelled invoices, payments and currencies with decimal arithmetic so totals never drift through floating-point rounding.",
      "Designed a single 'money in, money out' timeline as the home screen instead of a dashboard of disconnected charts.",
      "Planned multi-tenant data isolation from the first schema, so the concept can grow into a product without a rewrite.",
      "Kept every screen usable on a mid-range phone, since owners often check payments on the move.",
    ],
    stack: ["Next.js", "TypeScript", "PostgreSQL", "Row-level security", "decimal.js"],
    highlights: [
      "Multi-currency totals with explicit exchange-rate sources",
      "Mobile-first invoice creation flow",
      "Tenant isolation enforced in the database",
    ],
    results: [],
    art: "dashboard",
  },
  {
    slug: "atlas-dispatch",
    title: "Atlas Dispatch",
    kind: "concept",
    category: "Web Application",
    summary: "A concept for a delivery dispatch console that keeps working when drivers' connections do not.",
    overview:
      "Atlas Dispatch is a concept web application for coordinating deliveries between a dispatch desk and drivers in the field. It explores how operational software should behave when mobile connectivity is intermittent.",
    problem:
      "Dispatch teams often coordinate through phone calls and chat groups. Status updates arrive late or not at all, and drivers lose work when their connection drops mid-update.",
    approach: [
      "Designed the driver app as an installable progressive web app that queues updates offline and syncs when back online.",
      "Made every status change idempotent so retries after a dropped connection never create duplicate records.",
      "Gave dispatchers a live board organised by exception — late, unassigned, failed — rather than by every order.",
      "Kept the driver interface to large touch targets and minimal text for use in vehicles and outdoors.",
    ],
    stack: ["Next.js", "Service workers", "IndexedDB", "PostgreSQL", "Server-sent events"],
    highlights: [
      "Offline-tolerant driver workflow",
      "Exception-first dispatch board",
      "Idempotent sync protocol",
    ],
    results: [],
    art: "dispatch",
  },
  {
    slug: "signal-desk",
    title: "Signal Desk",
    kind: "concept",
    category: "AI Automation",
    summary:
      "A concept for AI-assisted support triage where every automated suggestion stays reviewable by a person.",
    overview:
      "Signal Desk is a concept exploring how a support team could use language models to classify, prioritise and draft replies to incoming requests, without handing final decisions to the model.",
    problem:
      "Support inboxes mix urgent issues with routine questions. Teams spend time sorting and routing before they can help anyone, and fully automated replies risk confident but wrong answers.",
    approach: [
      "Used the model for classification and first drafts only; a person approves anything sent to a customer.",
      "Built an evaluation set of sample requests so changes to prompts can be measured before release.",
      "Showed the model's reasoning summary and confidence next to each suggestion so reviewers can judge it quickly.",
      "Added cost and latency tracking per request to keep the automation economical.",
    ],
    stack: ["TypeScript", "LLM API", "PostgreSQL", "Queue workers", "Evaluation harness"],
    highlights: ["Human-in-the-loop by design", "Measurable prompt changes", "Transparent suggestions"],
    results: [],
    art: "triage",
  },
  {
    slug: "bridgeport",
    title: "Bridgeport",
    kind: "concept",
    category: "Integration",
    summary: "A concept integration hub that connects payment providers, accounting and e-commerce systems.",
    overview:
      "Bridgeport is a concept for an integration service that sits between a business's payment providers, online store and accounting system, keeping records consistent without manual exports.",
    problem:
      "When payments, orders and accounting live in separate systems, staff reconcile them by hand. Errors are found late, and nobody is sure which system holds the truth.",
    approach: [
      "Defined a single canonical transaction model that every connector maps into and out of.",
      "Processed provider webhooks through a durable queue with signature verification and replay protection.",
      "Built a reconciliation view that highlights mismatches instead of hiding them behind automatic fixes.",
      "Documented every connector contract with OpenAPI so new providers can be added consistently.",
    ],
    stack: ["Node.js", "TypeScript", "Message queue", "PostgreSQL", "OpenAPI"],
    highlights: [
      "Canonical transaction model",
      "Verified, replay-safe webhooks",
      "Mismatch-first reconciliation",
    ],
    results: [],
    art: "network",
  },
];

export const projectCategories: ProjectCategory[] = [
  "Web Application",
  "SaaS",
  "AI Automation",
  "Integration",
];

export function getProject(slug: string): Project | undefined {
  return projects.find((p) => p.slug === slug);
}

export function projectKindLabel(kind: ProjectKind): string {
  return kind === "concept" ? "Concept project" : "Client project";
}
