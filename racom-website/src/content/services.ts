import type { IconName } from "@/components/ui/icon";

/**
 * Services offered. Edit copy freely; slugs become URLs (/services/<slug>).
 * Keep claims to what the team actually delivers — no guarantees, no invented results.
 */
export type Service = {
  slug: string;
  title: string;
  /** One sentence for cards and meta descriptions. */
  summary: string;
  icon: IconName;
  intro: string;
  useCases: string[];
  process: { title: string; description: string }[];
  deliverables: string[];
  /** Typical tools — chosen per project, listed to set expectations, not as a promise. */
  typicalStack: string[];
  nextStep: string;
};

export const services: Service[] = [
  {
    slug: "custom-software-development",
    title: "Custom Software Development",
    summary:
      "Software designed around how your organisation actually works — internal tools, operational systems and business platforms.",
    icon: "code",
    intro:
      "Off-the-shelf tools rarely fit the way a growing business really operates. We design and build software around your processes, data and people, so the system supports the work instead of getting in its way.",
    useCases: [
      "Replacing spreadsheets and manual hand-offs with a reliable internal system",
      "Operational platforms for inventory, orders, scheduling or field work",
      "Back-office tools that bring scattered data into one place",
      "Extending or rebuilding a system your current tools can no longer support",
    ],
    process: [
      {
        title: "Discovery",
        description: "We map the workflow, users, data and constraints, and agree on what success means.",
      },
      {
        title: "Scope & plan",
        description: "A written scope, milestones and a first release small enough to ship early.",
      },
      {
        title: "Iterative build",
        description: "Short cycles with working software you can review, not status reports.",
      },
      {
        title: "Launch & handover",
        description: "Deployment, documentation and training so your team can own the system.",
      },
    ],
    deliverables: [
      "Production-ready application and source code",
      "Technical documentation and deployment guide",
      "Automated tests for core business logic",
      "Admin and user guides where needed",
    ],
    typicalStack: ["TypeScript", "Node.js", "React / Next.js", "PostgreSQL", "Cloud hosting of your choice"],
    nextStep: "Tell us about the process you want to improve and where it breaks down today.",
  },
  {
    slug: "web-development",
    title: "Websites & Web Applications",
    summary:
      "Fast, accessible websites and web applications that work well on every screen and on slower connections.",
    icon: "layout",
    intro:
      "Your website is often the first serious conversation a client has with your business. We build marketing sites and full web applications that load quickly, read clearly and stay easy to maintain.",
    useCases: [
      "A company website that explains what you do and converts visitors into enquiries",
      "Customer portals, booking flows and self-service applications",
      "Progressive web apps that feel native on mobile without an app store",
      "Rebuilding an outdated site with better performance and accessibility",
    ],
    process: [
      {
        title: "Content & structure",
        description: "We define audiences, pages and the actions each page should lead to.",
      },
      { title: "Design", description: "A visual system and key screens, reviewed on real devices." },
      {
        title: "Engineering",
        description: "Server-rendered pages, optimised assets and accessible components.",
      },
      {
        title: "Launch",
        description: "SEO foundations, analytics if you want them, and a measured performance check.",
      },
    ],
    deliverables: [
      "Responsive website or web application",
      "Reusable design system and components",
      "SEO metadata, sitemap and structured content",
      "Performance and accessibility review results",
    ],
    typicalStack: [
      "Next.js",
      "TypeScript",
      "Tailwind CSS",
      "Headless CMS when needed",
      "Vercel or similar hosting",
    ],
    nextStep: "Share your current site (if any), your audience and what you want visitors to do.",
  },
  {
    slug: "ai-automation",
    title: "AI Integration & Automation",
    summary:
      "Practical automation and AI features that remove repetitive work — with humans kept in control of decisions that matter.",
    icon: "sparkles",
    intro:
      "AI is most useful when it is pointed at a specific, well-understood task. We help you identify where automation saves real effort, then build it with clear guardrails, review steps and measurable objectives.",
    useCases: [
      "Classifying, routing and summarising incoming requests or documents",
      "Drafting first versions of routine replies for staff to review",
      "Extracting structured data from forms, invoices or emails",
      "Connecting AI models to your own data with appropriate access controls",
    ],
    process: [
      {
        title: "Opportunity review",
        description:
          "We look at the workflow and estimate where automation is worth it — and where it is not.",
      },
      {
        title: "Prototype",
        description: "A small working version tested against your real examples before scaling up.",
      },
      {
        title: "Production build",
        description: "Integration, monitoring, fallbacks and human review where accuracy matters.",
      },
      {
        title: "Measure & improve",
        description: "Track the agreed objectives and refine prompts, rules and data over time.",
      },
    ],
    deliverables: [
      "Working automation integrated with your systems",
      "Evaluation set and accuracy notes for the use case",
      "Cost and usage monitoring",
      "Operating guide covering limits and failure handling",
    ],
    typicalStack: [
      "LLM APIs",
      "Workflow orchestration",
      "TypeScript / Python",
      "Your existing tools and data",
    ],
    nextStep: "Describe the repetitive task, how often it happens and what a good result looks like.",
  },
  {
    slug: "saas-product-development",
    title: "SaaS Product Development",
    summary:
      "From first version to a scalable product: multi-tenant architecture, billing, onboarding and the foundations to grow.",
    icon: "layers",
    intro:
      "Building a product is different from building a project. We help founders and companies turn an idea into a focused first release, then extend it on foundations that will not need to be thrown away.",
    useCases: [
      "Building the first release (MVP) of a new software product",
      "Turning an internal tool into a product other businesses can use",
      "Adding accounts, teams, roles and subscription billing",
      "Strengthening an early product's architecture before it grows",
    ],
    process: [
      {
        title: "Product framing",
        description: "Target users, the core job to be done and the smallest release worth launching.",
      },
      { title: "Design", description: "Key flows and a design system that can grow with the product." },
      {
        title: "Build & instrument",
        description: "Multi-tenant foundations, security and the metrics you need to learn.",
      },
      {
        title: "Launch & iterate",
        description: "Release, gather feedback and plan the next increments together.",
      },
    ],
    deliverables: [
      "Launch-ready product release",
      "Authentication, roles and tenant isolation",
      "Billing integration when required",
      "Product analytics plan and technical roadmap",
    ],
    typicalStack: [
      "Next.js",
      "TypeScript",
      "PostgreSQL",
      "Payment provider of your choice",
      "Cloud infrastructure",
    ],
    nextStep: "Tell us about the product, who it is for and where you are today.",
  },
  {
    slug: "api-systems-integration",
    title: "API & Systems Integration",
    summary:
      "Connect the systems you already use — payments, ERP, CRM, messaging and third-party APIs — reliably and securely.",
    icon: "plug",
    intro:
      "Most organisations run on several systems that do not talk to each other. We design integrations and APIs that move data reliably, handle failures gracefully and are documented well enough to maintain.",
    useCases: [
      "Integrating payment providers, mobile money or banking APIs",
      "Syncing data between ERP, CRM, accounting and e-commerce platforms",
      "Designing a clean public or partner API for your platform",
      "Replacing fragile manual exports with monitored data pipelines",
    ],
    process: [
      {
        title: "System mapping",
        description: "We document the systems, data ownership and every point of failure.",
      },
      {
        title: "Contract design",
        description: "API contracts, data mappings and error-handling rules agreed up front.",
      },
      {
        title: "Implementation",
        description: "Idempotent, observable integrations with retries and alerts.",
      },
      {
        title: "Verification",
        description: "Tests against sandbox environments, then a careful production rollout.",
      },
    ],
    deliverables: [
      "Integration services or API implementation",
      "API documentation (e.g. OpenAPI)",
      "Monitoring and alerting for failures",
      "Runbook for common operational issues",
    ],
    typicalStack: ["REST & webhooks", "OpenAPI", "Message queues where needed", "TypeScript / Node.js"],
    nextStep: "List the systems involved and the data that needs to move between them.",
  },
  {
    slug: "technical-consulting",
    title: "Technical Consulting & Modernization",
    summary:
      "Independent technical guidance: architecture reviews, modernization plans and a clear path for legacy systems.",
    icon: "compass",
    intro:
      "Sometimes the most valuable step is a clear-eyed assessment before writing more code. We review your systems, explain the trade-offs in plain language and help you plan changes you can actually execute.",
    useCases: [
      "Reviewing architecture, security or performance before a major investment",
      "Planning the modernization of a legacy application",
      "Choosing technology for a new product or platform",
      "Supporting an internal team with technical leadership",
    ],
    process: [
      {
        title: "Assessment",
        description: "Code, infrastructure and process review with the people who run the system.",
      },
      {
        title: "Findings",
        description: "A prioritised report: risks, quick wins and longer-term options.",
      },
      {
        title: "Roadmap",
        description: "A staged plan with effort estimates and decision points.",
      },
      {
        title: "Support",
        description: "Optional hands-on help implementing the plan with your team.",
      },
    ],
    deliverables: [
      "Written assessment with prioritised findings",
      "Modernization or architecture roadmap",
      "Decision records for key technical choices",
      "Workshop or walkthrough with your team",
    ],
    typicalStack: [
      "Your existing stack",
      "Cloud platforms",
      "Observability tooling",
      "Security best practices",
    ],
    nextStep: "Tell us about the system, what worries you about it and any upcoming decisions.",
  },
];

export function getService(slug: string): Service | undefined {
  return services.find((s) => s.slug === slug);
}

/** Homepage capabilities. Each links to the most relevant service page. */
export const capabilities: { title: string; description: string; icon: IconName; href: string }[] = [
  {
    title: "Custom Software Engineering",
    description: "Systems shaped around your operations, data and people — built to be maintained for years.",
    icon: "code",
    href: "/services/custom-software-development",
  },
  {
    title: "Web Applications",
    description: "Fast, accessible web platforms and portals that work on every screen and connection.",
    icon: "layout",
    href: "/services/web-development",
  },
  {
    title: "AI & Business Automation",
    description: "Targeted automation that removes repetitive work while people stay in control.",
    icon: "sparkles",
    href: "/services/ai-automation",
  },
  {
    title: "SaaS & Digital Products",
    description: "From a focused first release to a scalable, multi-tenant product.",
    icon: "layers",
    href: "/services/saas-product-development",
  },
  {
    title: "API & Systems Integration",
    description: "Reliable connections between payments, ERP, CRM and the tools you already use.",
    icon: "plug",
    href: "/services/api-systems-integration",
  },
  {
    title: "Mobile-Ready Experiences",
    description: "Responsive, installable web experiences designed for phones first, not as an afterthought.",
    icon: "smartphone",
    href: "/services/web-development",
  },
];
