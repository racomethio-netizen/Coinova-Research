# Fayda eSignet integration guide

_Status (Phase 5): the eSignet/OIDC adapter is **implemented and tested against a
test-only provider**. It has **never been connected to Fayda**, because TAMAGNE has no
NIDP credentials yet. Default and production state: `FAYDA_MODE=disabled`._

Fayda is Ethiopia's national digital ID, run by the National ID Program (NIDP).
Relying parties authenticate residents through **eSignet**, an OpenID Connect
provider. TAMAGNE is a private relying party, not part of NIDP, and must never
present itself otherwise.

## 1. What TAMAGNE will and will not do

| Will | Will not |
|------|----------|
| Use the authorization-code flow with PKCE (S256), `state` and `nonce` | Simulate or mock a production Fayda response |
| Authenticate the client with `private_key_jwt` | Use sandbox credentials in production (config refuses it) |
| Verify ID token and userinfo JWT signatures against the issuer's JWKS | Store the FIN, FAN or raw `sub` |
| Check `iss`, `aud`, `azp`, `exp`, `iat`, `nbf`, `nonce`, `acr` | Request or store biometric data or the `picture` claim |
| Record consent before redirecting | Issue a Fayda badge from a manual review |
| Store only an HMAC of `sub`, issuer, audience, `jti`, `acr` | Log tokens, codes or claims |

## 2. What is implemented

`apps/api/src/fayda/` — `POST /api/v1/fayda/start`, `GET /api/v1/fayda/callback`, `GET /api/v1/fayda/status`.

| Step | Implementation |
|------|----------------|
| Discovery | `<issuer>/.well-known/openid-configuration`; its `issuer` must equal `FAYDA_ISSUER` exactly; all endpoints HTTPS; cached 1 h |
| Consent | `FAYDA_IDENTITY_VERIFICATION` consent recorded (policy version, locale) before the redirect; re-checked at the callback |
| Authorization request | `response_type=code`, 256-bit `state` and `nonce` (stored as SHA-256), PKCE `S256` (verifier AES-256-GCM encrypted), `claims` (userinfo `name` only), `acr_values`, `claims_locales`, `ui_locales`; valid 10 min, single use |
| Callback binding | the request must belong to the **signed-in** user, so an attacker cannot bind a victim's identity to their own account; consumed before any token is redeemed |
| Token exchange | `private_key_jwt` client assertion (RFC 7523: iss = sub = client id, aud = token endpoint, 60 s, jti), `code_verifier` |
| ID token | signature via provider JWKS; algorithms RS256/PS256/ES256 only (`none` and HMAC refused); `iss`, `aud`, `azp`, `exp`, `iat` (not in the future), `nonce`, `acr` |
| Userinfo | must be a **signed** JWT (unsigned JSON refused) from the same issuer, for our client, same `sub` |
| Result | `IdentityVerification` VERIFIED with HMAC(pepper, issuer\|sub), issuer, audience, jti, acr, encrypted name; Level 2 badge (DB re-checks); tokens discarded; nothing identifying in logs or audit |
| Failures | recorded with a short code (e.g. `id_token_wrong_issuer`, `nonce_mismatch`, `duplicate_identity`), no badge |
| Duplicates | one identity can back one seller (DB unique on the subject hash) |
| Sandbox labelling | on non-production deployments a sandbox result is shown everywhere as "sandbox test"; on production it never counts (app rule + DB guard) |

**Testing.** `apps/api/test/test-idp.ts` is a TEST identity provider (NOT Fayda, NOT eSignet)
that enforces client assertions, PKCE and single-use codes and can be told to misbehave.
26 API tests and a browser round-trip test cover the happy path and the rejections listed in §6.
The API accepts its plain-http loopback issuer only when `APP_ENV=test`.

**Not yet verified against the real service** (needs NIDP sandbox access): exact eSignet
`acr_values`, claim names and locales, whether userinfo is signed (JWS) or encrypted (JWE)
for our registration (JWE is not implemented and is refused), clock skew, and error formats.

## 3. Current (disabled) state

With `FAYDA_MODE=disabled` (the default):

- `GET /api/v1/fayda/status` returns `{ enabled: false, manualReviewAvailable: true, reason }`.
- The UI offers the manual identity review instead. Its result is labelled
  "Identity reviewed manually" and never as Fayda verification.
- No Fayda endpoints accept traffic.

## 4. Onboarding steps (owner actions)

1. Apply to NIDP as a relying party; describe purpose, data minimization and residency.
2. Agree the legal basis, consent text (EN/AM) and claims (minimum: `sub`, `name`; no `picture`).
3. Receive **sandbox** access: issuer URL, client ID registration, allowed `acr_values`.
4. Generate the client key pair **in the secrets manager / HSM**; register only the public JWK with NIDP.
5. Implement and test against sandbox (Phase 5), including all negative tests below.
6. Pass NIDP's review; receive **production** credentials through a secure channel.
7. Configure production (below); set the database guard; enable.

Each environment uses its own key pair and client ID. Never reuse sandbox keys.

## 5. Configuration

| Variable | Notes |
|----------|-------|
| `FAYDA_MODE` | `disabled` \| `sandbox` \| `production`. `production` requires `APP_ENV=production`; production `APP_ENV` refuses `sandbox` |
| `FAYDA_ISSUER` | HTTPS issuer from NIDP. Discovery: `<issuer>/.well-known/openid-configuration`. Production refuses issuers that look like sandbox/test hosts |
| `FAYDA_CLIENT_ID` | Issued by NIDP |
| `FAYDA_REDIRECT_URI` | Must exactly match the registered URI; HTTPS in production, e.g. `https://<domain>/api/v1/fayda/callback` |
| `FAYDA_CLIENT_PRIVATE_KEY_JWK` | Private JWK JSON, injected by the secrets manager; never in files or logs |
| `FAYDA_SCOPES` | Default `openid profile`; only what NIDP approves |
| `FAYDA_ACR_VALUES` | As agreed with NIDP |
| `FAYDA_CLAIMS_LOCALES` | `en am` |
| `FAYDA_USERINFO_CLAIMS` | Default `name`. Biometric (`picture`) and ID-number claims are refused by config validation |

On the production database, once:

```sql
ALTER DATABASE tamagne SET tamagne.environment = 'production';
```

The database then rejects sandbox authorization requests and results, and refuses
any Fayda badge not backed by a `PRODUCTION` result.

## 6. Required negative tests

Invalid signature · wrong `alg` / `none` · unknown `kid` · expired token · `iat` in the
future · wrong issuer · wrong audience · missing/wrong `azp` · nonce mismatch · unknown,
expired or reused `state` · callback from another session · replayed code · replayed
`jti` · userinfo `sub` ≠ ID token `sub` · userinfo signature invalid · provider error
responses · network timeout · duplicate identity already verified by another seller ·
withdrawn consent between start and callback.

Automated against the test provider: all of the above **except** network timeout (not
simulated), a replayed `jti` across two authorization requests (enforced by the DB unique
index `iv_one_use_per_jti`, not by an API test) and a genuinely unknown `kid` (covered
by the "signed by an unknown key" case, which reuses a known `kid`). A replayed
authorization code is refused by the provider; our side refuses the replayed `state`
before any code is redeemed.

The database already enforces several of these independently (single-use auth
request result and `jti`, duplicate subject, `VERIFIED` requires a verified signature).
