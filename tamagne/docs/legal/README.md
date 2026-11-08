# Legal and compliance documents

**All documents here are drafts for review by Ethiopian counsel.** Bracketed items must be
completed by the owner. Nothing here is legal advice.

| Document | Where |
|----------|-------|
| Privacy policy (public, EN) | `apps/web/src/content/privacy-policy.ts` → `/[lang]/privacy` |
| Terms of service (public, EN) | `apps/web/src/content/terms.ts` → `/[lang]/terms` |
| Data protection impact assessment | [DPIA_TEMPLATE.md](DPIA_TEMPLATE.md) |
| Retention schedule | [RETENTION.md](RETENTION.md) |
| Incident and breach response | [INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md) |
| Security documentation | [../SECURITY.md](../SECURITY.md) |
| Fayda partnership and integration guide | [../FAYDA_INTEGRATION.md](../FAYDA_INTEGRATION.md) |

Policy versions: `2026-11-draft` (in `packages/core/src/policies.ts`, recorded on every consent).
When a text changes, bump the version there **and** in the content file; existing consents keep
the version the person agreed to, and re-consent is requested where required.

Amharic versions of the privacy policy and terms must be produced by a professional legal
translator; the Amharic pages currently show a notice and the English text.
