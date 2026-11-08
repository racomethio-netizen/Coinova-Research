# Security

_Security documentation for TAMAGNE. See also ARCHITECTURE.md §7, legal/INCIDENT_RESPONSE.md._

## Reporting a vulnerability
Email [security contact]. Please do not test against production with real data; we will
acknowledge within [2 business days].

## Controls and where they are verified

| Requirement | Implementation | Automated tests |
|-------------|----------------|-----------------|
| Role-based access control | `StaffGuard` + per-route roles; DB requires reviewers to hold a live role | `apps/api/test/auth.test.ts`, `admin.test.ts`, `business.test.ts`, e2e `admin.spec.ts` |
| MFA for administrators | TOTP (RFC 6238), encrypted secret, replay protection, required per session for every admin route | `auth.test.ts` (enrol, verify, replay), e2e console sign-in |
| Strict server-side authorization | identity from the session only; ownership checks; client-set fields rejected | `auth.test.ts`, `verify.test.ts` |
| Encryption in transit | TLS everywhere; HSTS (web, API in production); `sslmode=verify-full` required for the DB in production | config tests `env.test.ts` |
| Encryption at rest | volume encryption (provider) + AES-256-GCM application encryption for names, MFA secrets, PKCE verifiers, reporter contacts and every stored file, with key rotation | `core.test.ts`, `adapters.test.ts`, `business.test.ts` |
| Secure sessions | opaque 256-bit tokens, hashed; `__Host-` Secure HttpOnly SameSite cookies; idle/absolute timeouts; rotation at sign-in | `auth.test.ts` |
| Rate limiting | Redis counters (fail closed): OTP send/verify, MFA, uploads, exports, reports, appeals, lookups | `auth.test.ts`, `verify.test.ts`, `rate-limit.test.ts` |
| CSRF | Origin check + double-submit token on every mutation, incl. sign-in | `auth.test.ts` |
| XSS | React escaping; no HTML injection; per-request nonce CSP (`script-src 'nonce-…' 'strict-dynamic'`); QR/MFA SVGs only as `<img>` data URIs; uploads never rendered inline (`attachment`, `CSP: sandbox`) | e2e `security.spec.ts` |
| SQL injection | Prisma parameterisation; raw SQL only as tagged templates | code review |
| File upload validation | byte-level type detection, active-content PDF refusal, 10 MB limit, server-generated keys | `documents.test.ts`, `business.test.ts` |
| Malware scanning | ClamAV INSTREAM before storage; infected files never stored; failures block review | `adapters.test.ts`, `business.test.ts` (real ClamAV not yet exercised) |
| Audit logging | append-only, hash-chained, concurrency-safe; no personal data in metadata (enforced) | `integrity.test.ts`, `admin.test.ts` |
| Secrets management | env from a secrets manager; config refuses dev placeholders in production; values never logged | `env.test.ts` |
| Backup and restore testing | Phase 8 | — |
| Account suspension and revocation | immediate suspension; reactivation needs a second reviewer; badge and QR revocation | `admin.test.ts`, e2e |
| Secure password reset | not applicable: no passwords (phone OTP sign-in); staff add TOTP | — |
| Admin action tracking | every staff action audited; document views audited | `admin.test.ts`, `business.test.ts` |
| Security event monitoring | `/admin/security` thresholds from the audit log; wire counts to alerting in Phase 8 | `admin.test.ts` |
| Data retention and deletion | retention and deletion jobs | `privacy.test.ts` |
| No raw identity data or secrets in logs | logger records error types and request IDs only; audit metadata key filter | `auth.test.ts`, `fayda.test.ts` |

## Key management
- `FIELD_ENCRYPTION_KEYS` is a key ring (`id:base64`). To rotate: add a new key id, set
  `FIELD_ENCRYPTION_ACTIVE_KEY_ID`, deploy; old ciphertexts still decrypt; a re-encryption job is
  Phase 8.
- `HMAC_PEPPER` cannot be rotated without losing duplicate-identity detection and OTP verification
  in flight; treat it as long-lived and store it in the HSM/KMS.
- Fayda client private key lives only in the secrets manager; NIDP holds the public JWK.

## Known gaps (tracked in ROADMAP.md)
Real ClamAV not exercised; no external penetration test yet; backup/restore drill pending; CSP
allows inline *styles*; admin console English only.
