import { randomUUID } from "node:crypto";
import {
  SignJWT,
  createRemoteJWKSet,
  errors as joseErrors,
  importJWK,
  jwtVerify,
  type JWK,
  type JWTPayload,
  type JWTVerifyGetKey,
} from "jose";
import { sha256Hex, safeEqualHex } from "@tamagne/core";
import type { FaydaConfig } from "../config/env.js";

export type FaydaEnabledConfig = Extract<FaydaConfig, { enabled: true }>;

/**
 * Short, stable failure codes. Recorded on the IdentityVerification row and shown to the
 * seller as a category; never contain token contents.
 */
export type FaydaFailureCode =
  | "provider_error"
  | "discovery_failed"
  | "token_exchange_failed"
  | "id_token_invalid_signature"
  | "id_token_wrong_issuer"
  | "id_token_wrong_audience"
  | "id_token_expired"
  | "id_token_invalid"
  | "nonce_mismatch"
  | "acr_not_allowed"
  | "userinfo_failed"
  | "userinfo_invalid"
  | "subject_mismatch";

export class FaydaError extends Error {
  constructor(readonly code: FaydaFailureCode) {
    super(code);
    this.name = "FaydaError";
  }
}

interface Discovery {
  issuer: string;
  authorization_endpoint: string;
  token_endpoint: string;
  userinfo_endpoint: string;
  jwks_uri: string;
}

/** Signing algorithms accepted from the provider. "none" and HMAC algorithms are never accepted. */
const ALLOWED_ALGS = ["RS256", "PS256", "ES256"];
const CLOCK_TOLERANCE_S = 60;
const HTTP_TIMEOUT_MS = 8000;
const DISCOVERY_TTL_MS = 3_600_000;

export interface VerifiedIdentity {
  issuer: string;
  audience: string;
  subject: string;
  jti: string | null;
  acr: string | null;
  name: string | null;
}

/**
 * Fayda eSignet relying-party client (OpenID Connect authorization code flow with PKCE,
 * private_key_jwt client authentication, signed ID token and signed userinfo).
 */
export class FaydaOidcClient {
  private discovery?: { doc: Discovery; jwks: JWTVerifyGetKey; at: number };

  constructor(
    private readonly cfg: FaydaEnabledConfig,
    private readonly fetchImpl: typeof fetch = fetch,
  ) {}

  private assertEndpoint(url: string, field: string): string {
    let u: URL;
    try {
      u = new URL(url);
    } catch {
      throw new FaydaError("discovery_failed");
    }
    const loopback = ["127.0.0.1", "localhost", "[::1]"].includes(u.hostname);
    if (u.protocol !== "https:" && !(this.cfg.allowInsecureLoopback && loopback && u.protocol === "http:")) {
      throw new FaydaError("discovery_failed");
    }
    void field;
    return u.toString();
  }

  private async http(url: string, init: RequestInit = {}): Promise<Response> {
    return this.fetchImpl(url, { ...init, redirect: "error", signal: AbortSignal.timeout(HTTP_TIMEOUT_MS) });
  }

  async discover(): Promise<{ doc: Discovery; jwks: JWTVerifyGetKey }> {
    if (this.discovery && Date.now() - this.discovery.at < DISCOVERY_TTL_MS) return this.discovery;
    const base = this.cfg.issuer.replace(/\/+$/, "");
    let doc: Discovery;
    try {
      const res = await this.http(`${base}/.well-known/openid-configuration`);
      if (!res.ok) throw new Error(String(res.status));
      doc = (await res.json()) as Discovery;
    } catch {
      throw new FaydaError("discovery_failed");
    }
    // The discovery document must describe exactly the configured issuer (mix-up defence).
    if (doc.issuer !== this.cfg.issuer) throw new FaydaError("discovery_failed");
    for (const f of ["authorization_endpoint", "token_endpoint", "userinfo_endpoint", "jwks_uri"] as const) {
      if (typeof doc[f] !== "string") throw new FaydaError("discovery_failed");
      doc[f] = this.assertEndpoint(doc[f], f);
    }
    const jwks = createRemoteJWKSet(new URL(doc.jwks_uri), { timeoutDuration: HTTP_TIMEOUT_MS, cooldownDuration: 30_000 });
    this.discovery = { doc, jwks, at: Date.now() };
    return this.discovery;
  }

  async authorizationUrl(p: { state: string; nonce: string; codeChallenge: string; uiLocale: "en" | "am" }): Promise<string> {
    const { doc } = await this.discover();
    const url = new URL(doc.authorization_endpoint);
    const claims = { userinfo: Object.fromEntries(this.cfg.userinfoClaims.map((c) => [c, { essential: true }])), id_token: {} };
    const params: Record<string, string> = {
      response_type: "code",
      client_id: this.cfg.clientId,
      redirect_uri: this.cfg.redirectUri,
      scope: this.cfg.scopes.join(" "),
      state: p.state,
      nonce: p.nonce,
      code_challenge: p.codeChallenge,
      code_challenge_method: "S256",
      claims: JSON.stringify(claims),
      claims_locales: this.cfg.claimsLocales,
      ui_locales: p.uiLocale,
      display: "page",
    };
    if (this.cfg.acrValues) params.acr_values = this.cfg.acrValues;
    for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
    return url.toString();
  }

  /** private_key_jwt client assertion (RFC 7523), audience = token endpoint, 60 s lifetime. */
  private async clientAssertion(tokenEndpoint: string): Promise<string> {
    const jwk = JSON.parse(this.cfg.clientPrivateKeyJwk) as JWK;
    const alg = jwk.alg ?? (jwk.kty === "EC" ? "ES256" : "RS256");
    const key = await importJWK(jwk, alg);
    return new SignJWT({})
      .setProtectedHeader({ alg, typ: "JWT", ...(jwk.kid ? { kid: jwk.kid } : {}) })
      .setIssuer(this.cfg.clientId)
      .setSubject(this.cfg.clientId)
      .setAudience(tokenEndpoint)
      .setIssuedAt()
      .setExpirationTime("60s")
      .setJti(randomUUID())
      .sign(key);
  }

  async exchangeCode(code: string, codeVerifier: string): Promise<{ idToken: string; accessToken: string }> {
    const { doc } = await this.discover();
    const body = new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: this.cfg.redirectUri,
      client_id: this.cfg.clientId,
      client_assertion_type: "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
      client_assertion: await this.clientAssertion(doc.token_endpoint),
      code_verifier: codeVerifier,
    });
    let json: { id_token?: unknown; access_token?: unknown; token_type?: unknown };
    try {
      const res = await this.http(doc.token_endpoint, { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded", accept: "application/json" }, body });
      if (!res.ok) throw new Error(String(res.status));
      json = (await res.json()) as typeof json;
    } catch {
      throw new FaydaError("token_exchange_failed");
    }
    if (typeof json.id_token !== "string" || typeof json.access_token !== "string" || String(json.token_type).toLowerCase() !== "bearer") {
      throw new FaydaError("token_exchange_failed");
    }
    return { idToken: json.id_token, accessToken: json.access_token };
  }

  private mapJoseError(e: unknown, prefix: "id_token"): never {
    if (e instanceof FaydaError) throw e;
    if (e instanceof joseErrors.JWTExpired) throw new FaydaError(`${prefix}_expired`);
    if (e instanceof joseErrors.JWTClaimValidationFailed) {
      if (e.claim === "iss") throw new FaydaError(`${prefix}_wrong_issuer`);
      if (e.claim === "aud") throw new FaydaError(`${prefix}_wrong_audience`);
      throw new FaydaError(`${prefix}_invalid`);
    }
    if (e instanceof joseErrors.JWSSignatureVerificationFailed || e instanceof joseErrors.JWKSNoMatchingKey || e instanceof joseErrors.JOSEAlgNotAllowed) {
      throw new FaydaError(`${prefix}_invalid_signature`);
    }
    throw new FaydaError(`${prefix}_invalid`);
  }

  /** Verifies the ID token: signature (provider JWKS, allowed algs), iss, aud, azp, exp, iat, nonce, acr. */
  async verifyIdToken(idToken: string, expectedNonceHash: string): Promise<JWTPayload & { sub: string }> {
    const { jwks } = await this.discover();
    let payload: JWTPayload;
    try {
      ({ payload } = await jwtVerify(idToken, jwks, {
        issuer: this.cfg.issuer,
        audience: this.cfg.clientId,
        algorithms: ALLOWED_ALGS,
        clockTolerance: CLOCK_TOLERANCE_S,
        requiredClaims: ["sub", "iat", "exp", "nonce"],
      }));
    } catch (e) {
      this.mapJoseError(e, "id_token");
    }
    const now = Math.floor(Date.now() / 1000);
    if (typeof payload.iat !== "number" || payload.iat > now + CLOCK_TOLERANCE_S) throw new FaydaError("id_token_invalid");
    const audiences = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
    if ((audiences.length > 1 || payload.azp !== undefined) && payload.azp !== this.cfg.clientId) throw new FaydaError("id_token_wrong_audience");
    if (typeof payload.sub !== "string" || payload.sub.length === 0 || payload.sub.length > 255) throw new FaydaError("id_token_invalid");
    if (typeof payload.nonce !== "string" || !safeEqualHex(sha256Hex(payload.nonce), expectedNonceHash)) throw new FaydaError("nonce_mismatch");
    if (this.cfg.acrValues) {
      const allowed = this.cfg.acrValues.split(/\s+/).filter(Boolean);
      if (typeof payload.acr !== "string" || !allowed.includes(payload.acr)) throw new FaydaError("acr_not_allowed");
    }
    return payload as JWTPayload & { sub: string };
  }

  /** Userinfo must be a signed JWT from the same issuer, for this client and the same subject. */
  async fetchUserinfo(accessToken: string, expectedSub: string): Promise<JWTPayload> {
    const { doc, jwks } = await this.discover();
    let text: string;
    let contentType: string;
    try {
      const res = await this.http(doc.userinfo_endpoint, { headers: { authorization: `Bearer ${accessToken}`, accept: "application/jwt" } });
      if (!res.ok) throw new Error(String(res.status));
      contentType = (res.headers.get("content-type") ?? "").toLowerCase();
      text = (await res.text()).trim();
    } catch {
      throw new FaydaError("userinfo_failed");
    }
    // Unsigned JSON userinfo is refused: claims must be attributable to the provider.
    if (!contentType.startsWith("application/jwt") && !/^[\w-]+\.[\w-]+\.[\w-]+$/.test(text)) throw new FaydaError("userinfo_invalid");
    let payload: JWTPayload;
    try {
      ({ payload } = await jwtVerify(text, jwks, { issuer: this.cfg.issuer, audience: this.cfg.clientId, algorithms: ALLOWED_ALGS, clockTolerance: CLOCK_TOLERANCE_S }));
    } catch {
      throw new FaydaError("userinfo_invalid");
    }
    if (payload.sub !== expectedSub) throw new FaydaError("subject_mismatch");
    return payload;
  }

  /** Full verification after the callback. Tokens are used here and then discarded. */
  async complete(code: string, codeVerifier: string, expectedNonceHash: string): Promise<VerifiedIdentity> {
    const { idToken, accessToken } = await this.exchangeCode(code, codeVerifier);
    const id = await this.verifyIdToken(idToken, expectedNonceHash);
    const info = await this.fetchUserinfo(accessToken, id.sub);
    const name = typeof info.name === "string" ? info.name.slice(0, 300) : null;
    return {
      issuer: this.cfg.issuer,
      audience: this.cfg.clientId,
      subject: id.sub,
      jti: typeof id.jti === "string" ? id.jti.slice(0, 256) : null,
      acr: typeof id.acr === "string" ? id.acr.slice(0, 256) : null,
      name,
    };
  }
}
