# NEXUS AI: planning and architecture documents

NEXUS AI is a crypto market-intelligence platform offering research, signals,
backtesting and paper trading. It extends the existing
**CryptoScope Research** codebase in this repository and does not replace it.
The rest of `docs/` describes the system as it runs today. This folder describes
the target system and how we get there.

| Document | Purpose |
|---|---|
| [REPOSITORY_ASSESSMENT.md](REPOSITORY_ASSESSMENT.md) | What exists today, verified state, and the gap per product module (A–O) |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | Phases 1–10: tasks, deliverables, tests, exit criteria, open owner decisions |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Target architecture, service boundaries, data flows, hosting/budget, architecture decision records (ADRs) |
| [ERD.md](ERD.md) | Entity-relationship diagram (existing + planned tables) and RLS intent per table |
| [SIGNAL_ENGINE.md](SIGNAL_ENGINE.md) | Design specification for market data, indicators, signals, risk, validation, backtesting, paper trading |
| [DEPENDENCIES.md](DEPENDENCIES.md) | Current and planned dependencies, why each exists, and what we deliberately do not use |
| [SECURITY_RISKS.md](SECURITY_RISKS.md) | Threat model and risk register with mitigations and owning phase |
| [MARKET_DATA_SOURCES.md](MARKET_DATA_SOURCES.md) | Terms and availability review of candidate data sources (decision D5) |
| [MOBILE.md](MOBILE.md) | Mobile app architecture, decisions (D7), store-policy checklist (G-7), building |
| [COMPLIANCE.md](COMPLIANCE.md) | Legal gates, kill switches, and the claims policy (no guaranteed accuracy, no fabricated stats) |
| [SECURITY_REVIEW.md](SECURITY_REVIEW.md) | Phase 10 review of every risk in SECURITY_RISKS.md, with evidence and open items |
| [RUNBOOKS.md](RUNBOOKS.md) | Alerts and what to do, kill switches, backups and restore drill, secret rotation, load test |
| [GO_LIVE.md](GO_LIVE.md) | Staged go-live checklist covering every gate (all steps need owner approval) |

## Status

| Phase | State |
|---|---|
| 1. Architecture and documentation | ✅ Done (this folder) |
| 2. Database and authentication | ✅ Done. See ROADMAP.md (session 2) |
| 3. Market-data ingestion | 🟡 Built and tested on synthetic data. Live ingestion blocked on D5 + network access |
| 4. Technical analysis and signal engine | ✅ Done (rules-only, shadow mode). Runs live once Phase 3 has data |
| 5. Backtesting and performance tracking | 🟡 Built and tested on synthetic data. The real validation report needs Phase 3 data |
| 6. Paper trading | ✅ Done (virtual, long-only). Fills need live prices from Phase 3 |
| 7. Web dashboard | ✅ Done (the brand name waits on D2) |
| 8. Mobile application | ✅ Done (no store release yet; see MOBILE.md) |
| 9. Subscription architecture | 🟡 Entitlements enforced and tested. The payment provider adapter waits on D9 |
| 10. Testing and deployment | 🟡 Built and tested locally and in CI. Staging and production need owner approval (GO_LIVE.md) |

No NEXUS feature is described as implemented unless the code exists and its tests pass.
The capabilities CryptoScope already has are listed, with their verification
status, in REPOSITORY_ASSESSMENT.md.
