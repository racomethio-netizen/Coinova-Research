# Fayda eSignet integration guide

_Status: integration **disabled**. The OIDC adapter is scheduled for Phase 5 and will
only be enabled with credentials officially issued to TAMAGNE._

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

## 2. Current (disabled) state

With `FAYDA_MODE=disabled` (the default):

- `GET /api/v1/fayda/status` returns `{ enabled: false, manualReviewAvailable: true, reason }`.
- The UI offers the manual identity review instead. Its result is labelled
  "Identity reviewed manually" and never as Fayda verification.
- No Fayda endpoints accept traffic.

## 3. Onboarding steps (owner actions)

1. Apply to NIDP as a relying party; describe purpose, data minimization and residency.
2. Agree the legal basis, consent text (EN/AM) and claims (minimum: `sub`, `name`; no `picture`).
3. Receive **sandbox** access: issuer URL, client ID registration, allowed `acr_values`.
4. Generate the client key pair **in the secrets manager / HSM**; register only the public JWK with NIDP.
5. Implement and test against sandbox (Phase 5), including all negative tests below.
6. Pass NIDP's review; receive **production** credentials through a secure channel.
7. Configure production (below); set the database guard; enable.

Each environment uses its own key pair and client ID. Never reuse sandbox keys.

## 4. Configuration

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

On the production database, once:

```sql
ALTER DATABASE tamagne SET tamagne.environment = 'production';
```

The database then rejects sandbox authorization requests and results, and refuses
any Fayda badge not backed by a `PRODUCTION` result.

## 5. Required negative tests (Phase 5 exit criteria)

Invalid signature · wrong `alg` / `none` · unknown `kid` · expired token · `iat` in the
future · wrong issuer · wrong audience · missing/wrong `azp` · nonce mismatch · unknown,
expired or reused `state` · callback from another session · replayed code · replayed
`jti` · userinfo `sub` ≠ ID token `sub` · userinfo signature invalid · provider error
responses · network timeout · duplicate identity already verified by another seller ·
withdrawn consent between start and callback.

The database already enforces several of these independently (single-use auth
request result and `jti`, duplicate subject, `VERIFIED` requires a verified signature).
