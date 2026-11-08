# Data Protection Impact Assessment — TAMAGNE

_Template pre-filled from the current design (2026-11-08). The owner, a data protection officer
and Ethiopian counsel must review, complete the bracketed items and sign off before launch and
whenever processing changes materially (e.g. connecting Fayda production, a new provider)._

## 1. Overview

| | |
|---|---|
| Controller | [legal entity, address, registration] |
| Data protection officer / contact | [name, contact] |
| Processing | Verification of online sellers' phone, identity and business; public verification lookups; fraud reports and appeals |
| Data subjects | Sellers (individuals and representatives of businesses), buyers who report, staff |
| Scale | [expected sellers per year, lookups per month] |
| Why a DPIA | Identity data from the national ID system, identity document images, large-scale profiling-like status display, new technology |

## 2. Description of processing

- **Data:** phone number; seller names; business identifiers (TIN, registration number) and documents; Fayda confirmation (keyed hash of the provider subject, name); identity document images (manual review); reports, appeals; keyed hashes of IP addresses; audit log.
- **Not processed:** biometrics, Fayda numbers (FIN/FAN), photos from Fayda, location tracking, advertising data.
- **Sources:** the seller; Fayda eSignet (with the seller's consent); buyers (reports).
- **Recipients:** SMS provider; Ethiopian hosting/storage provider; NIDP (authentication only); authorities when legally required.
- **Location:** Ethiopia [confirm provider and data centre]. No intended transfers abroad.
- **Retention:** see RETENTION.md.
- **Data flows:** see ARCHITECTURE.md §2 and §5.

## 3. Lawful basis and necessity

| Purpose | Basis | Necessity / minimisation |
|---------|-------|--------------------------|
| Account and phone verification | contract, consent | phone is the account identifier; codes stored only as keyed hashes |
| Fayda identity verification | explicit consent (recorded with policy version) | only `name` claim requested; FIN/FAN never received; subject stored as keyed hash |
| Manual identity review | explicit consent | image deleted 30/90 days after decision; no ID numbers recorded |
| Business verification | consent, contract | documents encrypted, scanned, deleted per schedule |
| Public lookup | consent (public profile) | fixed allow-list of fields; withdrawable anytime |
| Fraud reports | legitimate interest | reporter contact optional and encrypted; reports never change status automatically |
| Audit log | legal obligation, legitimate interest | IDs only; no contact data |

## 4. Risks

| # | Risk to individuals | Likelihood | Severity | Mitigations in place | Residual |
|---|---------------------|-----------|----------|----------------------|----------|
| 1 | Identity data breach | [ ] | high | app-level AES-256-GCM for names/files; no FIN/FAN stored; encrypted storage; database constraints and triggers; staff MFA; audit | [ ] |
| 2 | Someone verifies an account with another person's Fayda identity | [ ] | high | callback bound to the signed-in session; single-use state; one identity per account; consent | [ ] |
| 3 | Buyers misread "verified" as a guarantee | [ ] | medium | disclaimers on every result page, terms, API | [ ] |
| 4 | Wrongful suspension or rejection harms a seller's income | [ ] | high | reasons required; appeals by a different reviewer; second reviewer for high-risk decisions; suspension reason not public | [ ] |
| 5 | Malicious or false reports | [ ] | medium | rate limits; reports never change status automatically; investigator workflow | [ ] |
| 6 | Staff misuse of documents | [ ] | high | role-based access, MFA, every document view audited, tamper-evident log, security event review | [ ] |
| 7 | Enumeration of sellers or reporters | [ ] | low | random IDs; identical not-found responses; rate limits | [ ] |
| 8 | Data kept too long | [ ] | medium | automated retention and deletion jobs | [ ] |
| 9 | Cross-border transfer via providers (SMS, email, monitoring) | [ ] | medium | provider due diligence; Ethiopian hosting; [DPAs] | [ ] |
| 10 | Malware in uploads reaches staff | [ ] | medium | type checks, active-content PDF refusal, malware scan before storage, downloads never rendered inline | [ ] |

## 5. Consultation

[Record consultation with NIDP, the data protection authority (if required), representative sellers, and security reviewers.]

## 6. Decision and sign-off

| Role | Name | Decision | Date |
|------|------|----------|------|
| Owner | | | |
| DPO | | | |
| Counsel | | | |

Next review: [date, or on: Fayda production go-live; new provider; new data category].
