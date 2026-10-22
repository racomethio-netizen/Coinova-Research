# Racom Technology — website

Marketing website for **Racom Technology** (_Engineering What’s Next._): a dark, premium, engineering-led site
with services, portfolio, products, insights (MDX) and a working project-inquiry form.

Built with Next.js 16 (App Router) · TypeScript (strict) · Tailwind CSS 4 · Motion (one component) · Lucide ·
Zod · React Hook Form · MDX · Resend (REST, server-only) · Vitest + Testing Library · Playwright + axe.

> This app lives in `racom-website/` inside a repository that also contains an unrelated app. It is fully
> self-contained: its own `package.json`, lockfile and configs. Run every command from this directory.

## Quick start

```bash
cd racom-website
npm ci
cp .env.example .env.local   # optional in development
npm run dev                  # http://localhost:3000
```

Node.js ≥ 20.9 is required (22 LTS recommended).

| Command            | What it does                                                     |
| ------------------ | ---------------------------------------------------------------- |
| `npm run dev`      | Development server                                               |
| `npm run build`    | Production build (static pages + `/api/contact` + `/contact`)    |
| `npm start`        | Serve the production build                                       |
| `npm run check`    | Prettier check, typecheck, ESLint and unit tests                 |
| `npm test`         | Unit and component tests (Vitest)                                |
| `npm run test:e2e` | End-to-end + accessibility tests (Playwright; run `build` first) |
| `npm run format`   | Format everything with Prettier                                  |

For `test:e2e`, install a browser once with `npx playwright install chromium`, or point
`PLAYWRIGHT_CHROMIUM_PATH` at an existing Chromium binary.

## Project structure

```
src/
  app/                    Routes (App Router)
    page.tsx              Homepage
    services/ work/ products/ about/ insights/ contact/ privacy/ terms/
    api/contact/route.ts  Inquiry endpoint (validation, CSRF origin check, rate limit, spam checks)
    sitemap.ts robots.ts manifest.ts opengraph-image.tsx icon.svg apple-icon.tsx
    not-found.tsx error.tsx layout.tsx globals.css (design tokens)
  config/site.ts          Company name, slogan, copy, navigation, contact channels, legal details
  content/                Typed content: services, projects, products, company copy, insights/*.mdx
  components/
    brand/                Logo mark + custom wordmark (SVG)
    layout/               Header, desktop nav, accessible mobile menu, footer
    home/                 Homepage sections
    ui/                   Buttons, section headings, badges, page hero
    content/              Project/product/article cards, share links, legal page shell
    visuals/              Signature hero visual and project/product illustrations (pure SVG)
    work/ contact/        Portfolio filter, inquiry form (client components)
  lib/                    SEO helpers, reading time, insights loader, contact schema/rate limit/spam checks
  server/contact/         Server-only delivery (Resend or development fallback) and email builder
public/brand/             Logo files: mark and full logo for dark and light backgrounds
tests/unit/               Vitest tests       tests/e2e/   Playwright tests
```

## Editing content

- **Company details, copy, navigation, contact channels, social links:** `src/config/site.ts`.
  Contact channels and social links are empty (`null` / `[]`) and are hidden until you fill them in.
- **Services:** `src/content/services.ts` (each entry becomes `/services/<slug>`).
- **Portfolio:** `src/content/projects.ts`. Entries with `kind: "concept"` are labelled as concepts everywhere.
  Add real client work with `kind: "client"`; only add `results` that are real, verified and approved.
- **Products:** `src/content/products.ts`. Status is `concept`, `in-development` or `available`. Tests enforce
  that only available products have an external URL and that example products are never marked available.
- **Insights:** add `src/content/insights/<kebab-slug>.mdx` with an exported `metadata` object
  (`title`, `description`, `publishedAt`, `author`, `tags`). The index, sitemap, reading time and related
  articles update automatically; invalid metadata fails the build.
- **Placeholders:** search the code for `PLACEHOLDER` and for `<Placeholder>` / `LegalField` usages.

## Contact form

`POST /api/contact` validates input with the same Zod schema the form uses. It also:

- rejects cross-site requests (Origin / `Sec-Fetch-Site` checks) and non-JSON bodies or bodies over 20 KB;
- applies a per-IP rate limit (default 5 per 10 minutes, configurable);
- rejects bots with a hidden honeypot field and a minimum fill time;
- escapes all user input in the notification email and rejects control characters in single-line fields.

Delivery:

| Configuration                                                  | Result                                                                                            |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `RESEND_API_KEY`, `CONTACT_FROM_EMAIL`, `CONTACT_TO_EMAIL` set | Email sent through Resend. Success is shown **only** if Resend accepts it; otherwise HTTP 502.    |
| Not configured, `npm run dev`                                  | Saved to `./.data/inquiries/*.json` (git-ignored). The form says plainly that it was not emailed. |
| Not configured, production                                     | HTTP 503; the form shows an error and the contact email if one is configured.                     |

The API key never reaches the browser. Inquiries are not stored in a database; they exist only in the
email (data minimisation). Add persistence later if you need a CRM trail.

**Setting up Resend**

1. Create an account at resend.com and add and verify your sending domain (DNS records).
2. Create an API key with “sending access” only.
3. Set `RESEND_API_KEY`, `CONTACT_FROM_EMAIL` (e.g. `Racom Website <website@your-domain>`) and
   `CONTACT_TO_EMAIL` in your hosting provider’s environment settings.
4. Submit a test inquiry and confirm it arrives. Replies go straight to the sender (`reply_to`).

**Rate-limit caveat:** the limiter is in memory, so on serverless or multi-instance hosting each instance
counts separately. For stronger protection, add your platform’s firewall/rate-limit rules, or swap
`src/lib/contact/rate-limit.ts` for a shared store such as Upstash Redis.

## Environment variables

See `.env.example`. None of them are required to build.

| Variable                                     | Scope  | Purpose                                                                   |
| -------------------------------------------- | ------ | ------------------------------------------------------------------------- |
| `NEXT_PUBLIC_SITE_URL`                       | public | Production origin. Enables canonical URLs, absolute OG/sitemap URLs, HSTS |
| `RESEND_API_KEY`                             | server | Resend API key (secret)                                                   |
| `CONTACT_FROM_EMAIL`                         | server | Verified sender address                                                   |
| `CONTACT_TO_EMAIL`                           | server | Recipient inbox(es), comma-separated                                      |
| `CONTACT_RATE_LIMIT_MAX` / `_WINDOW_SECONDS` | server | Rate-limit tuning (defaults 5 / 600)                                      |

## Deployment (Vercel, recommended)

1. Import the repository in Vercel and set **Root Directory** to `racom-website`. The framework is
   detected as Next.js; keep the default build command.
2. Add the environment variables above for Production (and Preview if wanted).
   Set `NEXT_PUBLIC_SITE_URL` to the final `https://` domain.
3. Deploy, attach your domain, then check `/sitemap.xml`, `/robots.txt` and a test inquiry.

Any Node.js host works too (Render, Fly.io, a VPS): `npm ci && npm run build && npm start -- -p $PORT`,
behind HTTPS. If you deploy behind your own proxy, make sure it sets `X-Forwarded-For` correctly, since the
rate limiter uses it.

## Quality checks (last run)

- Prettier, TypeScript (strict, `noUncheckedIndexedAccess`) and ESLint: clean.
- Unit/component tests: 51 passed (schema, rate limiting, spam checks, delivery, API route, form states,
  SEO helpers, reading time, content integrity).
- Playwright: 47 passed, 1 skipped by design (the mobile-menu test runs on mobile only), across desktop Chrome and Pixel 7. Covers every route, axe WCAG 2.1 AA scan
  per page, no horizontal overflow, internal link check, mobile menu (focus and Escape handling), work filter,
  contact validation, the honest failure state when unconfigured, and the success state.
- Overflow sweep at 360, 390, 768, 1024, 1440 and 1920 px on all pages: no horizontal overflow.
- Lighthouse 13.5 (mobile emulation, simulated slow 4G, local production server): performance 93–97,
  accessibility 100, best practices 100, SEO 100 on `/`, `/services`, `/work`, `/contact` and an article.
  Real-world results depend on hosting and network, so re-measure after deploying.

## Brand assets

- Mark: geometric R from three separated strokes (stem, bowl and a forward cyan leg). Use
  `public/brand/racom-mark.svg` on dark backgrounds and `racom-mark-light-bg.svg` on light ones.
- Full logo: `racom-logo-for-dark-bg.svg` / `racom-logo-for-light-bg.svg` (custom geometric wordmark, no font
  dependency).
- Colours and typography tokens: `src/app/globals.css` (`@theme`). Body text uses Inter and headings use Space Grotesk,
  both self-hosted through `next/font`.
