import { createHmac } from "node:crypto";

/** HS256 JWT for the local PostgREST test server (test-only secret). */
export const TEST_JWT_SECRET = "local-integration-test-secret-not-for-production-use";

const b64 = (v: unknown) => Buffer.from(typeof v === "string" ? v : JSON.stringify(v)).toString("base64url");

export function signJwt(claims: Record<string, unknown>, secret = TEST_JWT_SECRET): string {
  const header = b64({ alg: "HS256", typ: "JWT" });
  const payload = b64({ iat: Math.floor(Date.now() / 1000), exp: Math.floor(Date.now() / 1000) + 3600, ...claims });
  const sig = createHmac("sha256", secret).update(`${header}.${payload}`).digest("base64url");
  return `${header}.${payload}.${sig}`;
}

export const SERVICE_KEY = signJwt({ role: "service_role" });
export const ANON_KEY = signJwt({ role: "anon" });
export const PROXY_PORT = 54331;
export const POSTGREST_PORT = 54330;
