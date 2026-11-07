/**
 * Starts the TEST identity provider (NOT Fayda) for end-to-end tests only.
 * Env: TEST_IDP_PORT, TEST_IDP_REDIRECT_URI, TEST_IDP_CLIENT_PUBLIC_JWK.
 */
import { startTestIdp } from "../test/test-idp.js";

if (process.env.APP_ENV === "production" || process.env.NODE_ENV === "production") {
  console.error("The test identity provider must never run in production.");
  process.exit(1);
}
const idp = await startTestIdp({
  port: Number(process.env.TEST_IDP_PORT ?? 4300),
  redirectUri: process.env.TEST_IDP_REDIRECT_URI ?? "http://localhost:3100/api/v1/fayda/callback",
  clientPublicJwk: JSON.parse(process.env.TEST_IDP_CLIENT_PUBLIC_JWK ?? "null") ?? undefined,
  freshSubjectPerLogin: true,
});
console.log(`TEST identity provider (not Fayda) listening at ${idp.issuer}`);
