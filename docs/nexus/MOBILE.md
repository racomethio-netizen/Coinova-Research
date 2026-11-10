# Mobile app (Phase 8)

Expo (SDK 58, React Native 0.88, expo-router) in `mobile/`. This is a standalone project
(its own `package.json`, its own lockfile, and its own React version), so it never
conflicts with the web app.

## Architecture
- **API contract:** `packages/shared` holds Zod schemas for every response and request.
  The web routes produce these shapes (unit and integration contract tests). The app
  parses every response with them, so a mismatch shows as an error, never as data.
- **Auth:** Supabase Auth in the app (email + password). The session is stored in
  **SecureStore** (Keychain/Keystore), split into chunks because SecureStore limits
  values to about 2 KB (`src/lib/chunked-storage.ts`, unit-tested).
- **Data calls** go to the Next.js API with `Authorization: Bearer <access token>`.
  Cookies are never used. Entitlements, RLS, gate G-1 and tier rules apply exactly
  as on the web.
- **Public config** comes from `EXPO_PUBLIC_API_URL`, `EXPO_PUBLIC_SUPABASE_URL` and
  `EXPO_PUBLIC_SUPABASE_ANON_KEY` (see `mobile/.env.example`). These ship inside the
  app and are not secrets; RLS protects the data.
- **Screens:**
  - Home (plan, virtual equity), Scanner, Signals and signal detail, Performance.
  - Paper trading: open an account, place orders validated with the same schema as the
    server, view positions, cancel orders.
  - Settings: push on/off, website settings, sign out, **delete account**.
- **Push:**
  - The user opts in from Settings, and the Expo push token is registered via
    `/api/devices`.
  - The `push` channel is used for alerts. Tokens are unregistered on sign-out, and
    tokens Expo reports as dead are revoked server-side.

## Decisions
| Decision | Choice | Why |
|---|---|---|
| D7 Monetization | **No purchase UI or purchase links in the app**; it only shows the current plan | Offering subscriptions in-app can require store billing. Links to external purchase are restricted (anti-steering). Revisit with Phase 9 (e.g. store IAP via a billing provider) |
| Charts | "Open chart" opens the web chart in an in-app browser | Lightweight Charts targets the DOM. This keeps v1 small; a native chart can come later |
| Sign-up / password reset | Opened on the website | Consent, terms and email confirmation already live there |
| Account deletion | **In the app** (`DELETE /api/account`) | App Store guideline 5.1.1(v) requires in-app deletion for apps with accounts |
| Admin features | Web only | They need MFA (aal2) flows and a larger screen |

## Store-policy checklist (gate G-7, before any submission)
Review the **current** App Store Review Guidelines and Google Play policies. Do not rely
on this list alone.
- [ ] Positioning: research and **paper trading only**, with no real trading, custody or
      transfers. Check the rules for financial and crypto apps in each store.
- [ ] No purchase flows or external purchase links (D7) unless store billing is implemented.
- [ ] Privacy:
  - [ ] Privacy labels / Data safety form: email (account), push token, usage of
        in-app features; no tracking.
  - [ ] A privacy policy URL.
- [ ] Content:
  - [ ] Risk disclosures on every signal and performance screen (already present).
  - [ ] No accuracy or return claims in the store listing (docs/nexus/COMPLIANCE.md §4).
- [ ] Account deletion reachable in the app (done). Data export is linked on the website.
- [ ] Brand name cleared (D2) before the store listing.

## Building
```bash
cd mobile
cp .env.example .env        # fill in the public values
npm ci
npm run typecheck && npm test
npm run bundle:check        # Metro bundles for iOS + Android (also in CI)
npx expo start              # development (Expo Go or a dev build)
```
Release builds need an Expo account and EAS (`eas build`), an Apple Developer account
($99/yr) and a Google Play account ($25). Push needs an EAS project id in `app.json`.
**None of these are set up; that needs the owner's approval** (CLAUDE.md rule 8).

## Dependencies and audit
- Versions are installed with npm `before=2026-10-27` (`mobile/.npmrc`), which enforces
  the two-week rule.
- `npm audit --omit=dev` reports high/moderate advisories **only inside `@expo/cli`
  tooling**: `node-forge`, `zod@3` (CLI), `glob`, `minimatch`, `deepmerge`. These run on
  developer machines and CI, not in the app bundle. The app's own `zod` is 4.6.7
  (patched).
- CI fails on critical advisories. Dependabot tracks `mobile/`.
