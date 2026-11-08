# Security incident and personal-data breach response

_Draft. Confirm notification deadlines and the authority's contact details with counsel._

## Roles
- **Incident lead:** Security Officer on duty [name/rota].
- **Decision makers:** owner + DPO. **Counsel** for notification decisions.

## 1. Detect
Sources: security events page (`/admin/security`, thresholds), audit-chain verification
(`/admin/audit?verify=1`), monitoring alerts (readiness, error rate, backup failures), reports
from staff, sellers or researchers (security contact in SECURITY.md).

## 2. Contain (first hours)
- Revoke compromised staff roles (`/admin/staff`) — this also ends their sessions.
- Rotate secrets: session pepper/keys, `FIELD_ENCRYPTION_KEYS` (add a new key id, make it active,
  re-encrypt), Fayda client key (register a new public JWK with NIDP), provider credentials.
- Suspend affected sellers or revoke QR codes if impersonation is involved.
- Preserve evidence: do not delete logs; snapshot the database; note the audit `seq` range.

## 3. Assess
What data, how many people, sensitivity (identity data and documents are high), whether
encrypted, likely consequences. Record in the incident log.

## 4. Notify
- **Authority:** the data protection authority under Proclamation No. 1321/2024 within
  [statutory deadline — confirm; plan for 72 hours] of becoming aware of a breach likely to
  affect individuals.
- **Individuals:** without undue delay where there is high risk, in English and Amharic, saying
  what happened, what data, what we did, and what they should do.
- **NIDP:** promptly if Fayda-related data or credentials are involved [per agreement].

## 5. Recover and learn
Fix the cause, verify (tests, audit chain), close the incident with a written review, update
the DPIA and this plan.

## Incident log template
| Time (EAT) | Who | What happened / action | Evidence |
|------------|-----|------------------------|----------|
