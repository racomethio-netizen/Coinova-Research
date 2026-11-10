# Dependencies

Policy (from CLAUDE.md): pin exact versions that are at least two weeks old when
a package is added. Prefer the standard library and small, well-maintained
packages. Every addition is noted here with its reason. Versions for planned
packages are chosen and locked (`package-lock.json`, `uv.lock`) in the phase that
introduces them, not here.

## Existing web app (package.json, unchanged in Phase 1)

| Package | Version | Use |
|---|---|---|
| next | 16.3.8 | web framework, public API |
| react / react-dom | 19.3.0 | UI |
| @supabase/supabase-js / @supabase/ssr | 2.117.2 / 0.12.7 | auth, DB access with RLS |
| zod | 4.6.5 | input/env/provider validation |
| decimal.js | 10.6.0 | money and quantity math |
| lightweight-charts | 5.2.1 | charts (TradingView Lightweight Charts) |
| @anthropic-ai/sdk | ^0.132.0 | optional AI assistant |
| server-only | 0.0.1 | guard against client imports |
| dev: typescript 5, eslint 9 + eslint-config-next, vitest 4.1.12, @playwright/test 1.56.1, tailwindcss 4, tsx, pg | | tooling and tests |

## Planned: engine (Python 3.12, `engine/pyproject.toml`, Phase 3+)

| Package | Use | Notes |
|---|---|---|
| uv | dependency management + lockfile | reproducible installs (`uv.lock`) |
| fastapi + uvicorn | `/health`, `/internal/*` | internal surface only (ADR-001) |
| pydantic v2, pydantic-settings | schemas, validated env config | |
| psycopg 3 (+ pool) | Postgres access | least-privilege role (ADR-002) |
| httpx | public market-data HTTP | timeouts and limits set explicitly |
| numpy, pandas | indicators and features | floats only in feature math (ADR-009) |
| scikit-learn | pipelines, logistic baseline, calibration | |
| xgboost | gradient-boosted model where it beats the baseline | native SHAP contributions |
| pytest, hypothesis | tests and property tests | dev |
| ruff, mypy | lint and type check | dev |
| pip-audit | dependency vulnerability scan | dev/CI |
| TA-Lib (Python + C lib) | **test-only** reference values for indicators | not in the production image (ADR-003) |

## Planned: mobile (Expo, `mobile/`, Phase 8)

| Package | Use | Notes |
|---|---|---|
| expo, react-native, expo-router | app shell and navigation | TypeScript |
| @supabase/supabase-js | auth (PKCE) | tokens kept in `expo-secure-store` |
| expo-secure-store | token storage | not AsyncStorage for tokens |
| expo-notifications | push | tokens stored server-side per user |
| charts | **decision in Phase 8**: Lightweight Charts inside `react-native-webview`, or a native RN chart library | Lightweight Charts targets the DOM |
| zod + shared types | `packages/shared` | same schemas as the web API |

## Planned: CI and supply chain (Phase 2 baseline, Phase 10 complete)

- GitHub Actions: typecheck, lint, unit, DB (Postgres service), integration, build, e2e, engine tests.
- Dependabot for npm, pip/uv, GitHub Actions and Docker.
- `npm audit --omit=dev`, `pip-audit`, gitleaks (secret scanning) in CI.
- CodeQL if the repository plan allows it (it may require GitHub Advanced Security on private repositories).

## Deliberately not used (and why)

| Not using | Reason |
|---|---|
| CCXT or any full exchange SDK | contains authenticated trading/withdrawal code; we need public klines only (ADR-004) |
| TA-Lib in production | native build complexity; indicators are implemented and tested in-house |
| pandas-ta | check maintenance status before any use; not needed given in-house indicators |
| Redis, Kafka, Celery | Postgres queue (`SKIP LOCKED`) is enough at this scale and budget |
| Kubernetes, managed ML platforms | cost and complexity are out of scope for the under-$500 budget |
| Floats for balances | ADR-009 |
