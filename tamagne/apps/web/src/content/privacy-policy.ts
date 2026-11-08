/**
 * Public privacy policy (English). DRAFT for review by Ethiopian counsel; bracketed items
 * must be completed before launch. Keep in sync with what the system does:
 * docs/ARCHITECTURE.md §4.4 and §8, docs/legal/RETENTION.md.
 */
export const PRIVACY_POLICY_VERSION = "2026-11-draft";

export const privacyPolicyEn = `
# Privacy Policy

**Version ${PRIVACY_POLICY_VERSION}. Draft pending legal review.**

TAMAGNE ("we") is an independent private platform operated by [legal entity name, registration number, address in Ethiopia]. We are not a government institution and are not part of the National ID Program. This policy explains what personal data we process, why, and your rights under Ethiopia's Personal Data Protection Proclamation No. 1321/2024.

## What we collect

- **Account:** your mobile number, the language you use, and when you sign in. We confirm your number with a one-time SMS code; we store only a scrambled (keyed hash) form of each code.
- **Seller profile:** your shop or seller name (in English and, if you choose, Amharic), seller type, and optionally region and city.
- **Business verification (if you use it):** your business's legal name, legal form, registration number, TIN, location, and the documents you upload (for example your business licence).
- **Identity verification (if you use it):**
  - *Fayda:* when you choose to verify with Fayda, the official eSignet service confirms your identity to us and tells us your name. We keep a scrambled identifier to stop one identity being used for several accounts. We do **not** receive or store your Fayda number (FIN/FAN), fingerprints, iris data or photo.
  - *Manual review:* if you upload an identity document instead, a reviewer looks at the image. We do not record your ID number.
- **Reports and appeals:** what you write when you report a seller or appeal a decision, and, if you choose, your contact details.
- **Technical data:** a scrambled form of your IP address (for abuse prevention), and two cookies that are strictly necessary for sign-in and protection against forged requests. We do not use advertising or analytics cookies.
- **Records of decisions:** verification decisions, consents you gave or withdrew, and a tamper-evident audit log of actions on your account.

## Why we use it and our legal basis

- To create and run your account and verify your phone, identity and business: **your consent** and **our contract with you**.
- To show your verification status to buyers: **your consent** (you can hide your public profile at any time).
- To prevent fraud and impersonation, handle reports and keep records of decisions: **our legitimate interest** in a trustworthy marketplace and, where applicable, **legal obligations**.
- To answer authorities: only when **required by Ethiopian law**.

Paying for a plan never buys or changes a verification result.

## What buyers can see

Only your approved display name, Tamagne ID, account status, and for each verification level whether it is verified, with its date and expiry. Buyers never see your phone number, documents, identity details, business registration numbers, or reasons for decisions. If you hide your profile, buyers cannot find you.

## Who we share data with

- **SMS provider** [name], to send your sign-in codes (your number and the message only).
- **Hosting and storage provider** [name], located in Ethiopia, under a data processing agreement.
- **The National ID Program (eSignet)**, only when you start a Fayda verification.
- **Authorities**, when Ethiopian law requires it.

We do not sell personal data. We do not intend to transfer personal data outside Ethiopia; if that ever becomes necessary we will do so only as the Proclamation allows and will update this policy first.

## How long we keep it

See the retention schedule: [link to RETENTION summary]. In short: identity document images are deleted 30 days after a decision (90 days if rejected, for appeals); business documents one year after the verification expires; sign-in codes after 30 days unless they back your phone verification; and when you delete your account we erase your contact details, names and files, keeping only records the law or fraud prevention requires (consent records, the audit log, reports about a seller, payment records) without your contact details.

## Your rights

- **Access:** download your data at any time from your Privacy page.
- **Correction:** ask us to correct inaccurate data from your Privacy page.
- **Deletion:** delete your account from your Privacy page. Your account is locked and hidden immediately and erased after [14] days, during which you can ask support to cancel.
- **Withdraw consent:** for public display, Fayda verification, document review and marketing, at any time. Withdrawing Fayda consent removes your Fayda verification.
- **Object and complain:** contact us at [privacy contact]. You may also complain to the data protection authority designated under Proclamation No. 1321/2024 [name and contact; confirm].

We answer requests within [30] days [confirm statutory period].

## Security

Data is stored in Ethiopia [confirm], encrypted in transit and at rest; identity details and documents are additionally encrypted by the application. Staff access requires two-factor authentication, is limited by role, and is logged.

## Children

TAMAGNE is for people aged 18 and over.

## Changes and contact

We will tell you about material changes before they apply and ask for consent again where required. Contact: [privacy contact, postal address].
`;
