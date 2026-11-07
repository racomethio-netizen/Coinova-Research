# CLAUDE.md: working on TAMAGNE

This directory is the TAMAGNE project. It is independent of the CryptoScope app in the
parent repository; the parent CLAUDE.md does not apply here. Read `docs/ARCHITECTURE.md`
and `docs/ROADMAP.md` ("Next phases") first.

## Non-negotiable rules

1. Never issue or display a Fayda badge without a successful, independently validated
   Fayda eSignet response. Never simulate Fayda. Manual identity review is a separate
   category and is never labelled as Fayda.
2. Phone verification is not identity verification. Identity and business verification
   are separate. Payment never creates, extends or restores verification.
3. Never present TAMAGNE as a government institution.
4. No real personal data in code, fixtures, tests or logs. Test phones use `+999…`.
   Never store or log FIN/FAN, raw `sub`, tokens, OTP codes, biometrics, raw IPs.
5. Identity, roles and entitlements come from the server session only.
6. Secrets only from environment/secrets manager; never commit `.env`.
7. Schema changes: add a new migration in `packages/db/prisma/migrations/`; never edit
   an applied one. Add integrity rules in SQL and tests in `packages/db/test/`.
8. Do not deploy, buy services or enable live integrations without the owner's approval.
9. Do not claim a feature is complete unless tests prove it works.

## Conventions

- ESM everywhere; relative imports end in `.js`. Workspace packages resolve to source
  via the `tamagne-source` export condition in tests and dev.
- Nest providers: always inject with explicit `@Inject(Token)`. `tsx` (used by `npm run dev`)
  does not emit decorator metadata, so implicit constructor injection silently breaks.
- Validate external input with Zod. API responses: `{ data }` / `{ error: { code, message, requestId } }`.
- Pure rules go in `packages/core` with unit tests.
- Pin dependencies exactly, to versions at least two weeks old.

## Before finishing

```bash
npm run db:test:up && npm run check && npm run build
```

Update `docs/ROADMAP.md` with what was verified, what is incomplete and exact next commands.
