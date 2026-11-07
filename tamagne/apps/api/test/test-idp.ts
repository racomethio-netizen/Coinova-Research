/**
 * TEST IDENTITY PROVIDER — NOT FAYDA, NOT eSignet.
 *
 * A minimal OpenID Connect provider used only by automated tests to exercise TAMAGNE's
 * relying-party checks. It behaves like a strict provider (verifies our private_key_jwt
 * client assertion, PKCE, redirect URI, single-use codes) and can be told to misbehave
 * so we can prove every failure is rejected. It runs on loopback with a throwaway key,
 * and the API only accepts a plain-http issuer when APP_ENV=test.
 */
import { createHash, randomBytes, randomUUID } from "node:crypto";
import { createServer, type IncomingMessage, type Server } from "node:http";
import { SignJWT, exportJWK, generateKeyPair, jwtVerify, importJWK, type JWK } from "jose";

export interface Faults {
  idTokenIssuer?: string;
  idTokenAudience?: string | string[];
  idTokenAzp?: string;
  idTokenExpired?: boolean;
  idTokenIatFuture?: boolean;
  idTokenNonce?: string;
  idTokenAcr?: string;
  signWithUnknownKey?: boolean;
  algNone?: boolean;
  hs256?: boolean;
  userinfoSub?: string;
  userinfoUnsigned?: boolean;
  userinfoAudience?: string;
  tokenError?: boolean;
  discoveryIssuer?: string;
}

export interface TestIdp {
  issuer: string;
  clientId: string;
  /** Private JWK the relying party signs client assertions with (we hold the public part). */
  clientPrivateJwk: JWK;
  /** Who "logs in" at the next authorization. */
  nextUser: { sub: string; name: string };
  faults: Faults;
  stats: { tokenRequests: number; rejectedAssertions: number; pkceFailures: number };
  close(): Promise<void>;
}

const b64url = (b: Buffer) => b.toString("base64url");

async function body(req: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  return Buffer.concat(chunks).toString();
}

export async function startTestIdp(opts: {
  clientId?: string;
  redirectUri: string;
  port?: number;
  /** Use a client key supplied by the caller (public part) instead of generating one. */
  clientPublicJwk?: JWK;
  /** Give every login a new random subject (end-to-end runs with many sellers). */
  freshSubjectPerLogin?: boolean;
}): Promise<TestIdp> {
  const clientId = opts.clientId ?? "tamagne-test-client";
  const { publicKey: idpPub, privateKey: idpPriv } = await generateKeyPair("RS256", { extractable: true });
  const { privateKey: rogueKey } = await generateKeyPair("RS256");
  const idpJwk = { ...(await exportJWK(idpPub)), kid: "test-idp-1", alg: "RS256", use: "sig" };
  const { publicKey: clientPub, privateKey: clientPriv } = await generateKeyPair("RS256", { extractable: true });
  const clientPubKey = await importJWK(opts.clientPublicJwk ?? (await exportJWK(clientPub)), "RS256");
  const clientPrivateJwk = { ...(await exportJWK(clientPriv)), kid: "tamagne-test-client-1", alg: "RS256" };

  const codes = new Map<string, { nonce: string; challenge: string; redirectUri: string; user: { sub: string; name: string }; acr: string }>();
  const accessTokens = new Map<string, { sub: string; name: string }>();
  let issuer = "";

  const idp: TestIdp = {
    issuer: "",
    clientId,
    clientPrivateJwk,
    nextUser: { sub: `test-psut-${randomUUID()}`, name: "Test Person (Fixture)" },
    faults: {},
    stats: { tokenRequests: 0, rejectedAssertions: 0, pkceFailures: 0 },
    close: () => new Promise((r) => server.close(() => r())),
  };

  const sign = async (claims: Record<string, unknown>, opts2: { iat?: number; exp?: number } = {}) => {
    const f = idp.faults;
    if (f.algNone) {
      const header = b64url(Buffer.from(JSON.stringify({ alg: "none", typ: "JWT" })));
      return `${header}.${b64url(Buffer.from(JSON.stringify(claims)))}.`;
    }
    if (f.hs256) {
      // Classic attack: HMAC "signed" with the provider's public key material.
      return new SignJWT(claims).setProtectedHeader({ alg: "HS256", kid: "test-idp-1" }).sign(Buffer.from(JSON.stringify(idpJwk)));
    }
    const jwt = new SignJWT(claims).setProtectedHeader({ alg: "RS256", kid: "test-idp-1", typ: "JWT" });
    if (opts2.iat !== undefined) jwt.setIssuedAt(opts2.iat);
    if (opts2.exp !== undefined) jwt.setExpirationTime(opts2.exp);
    return jwt.sign(f.signWithUnknownKey ? rogueKey : idpPriv);
  };

  const server: Server = createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", issuer);
    const json = (status: number, obj: unknown) => {
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify(obj));
    };
    try {
      if (url.pathname === "/.well-known/openid-configuration") {
        return json(200, {
          issuer: idp.faults.discoveryIssuer ?? issuer,
          authorization_endpoint: `${issuer}/authorize`,
          token_endpoint: `${issuer}/oauth/v2/token`,
          userinfo_endpoint: `${issuer}/oidc/userinfo`,
          jwks_uri: `${issuer}/.well-known/jwks.json`,
          response_types_supported: ["code"],
          code_challenge_methods_supported: ["S256"],
          token_endpoint_auth_methods_supported: ["private_key_jwt"],
          id_token_signing_alg_values_supported: ["RS256"],
        });
      }
      if (url.pathname === "/.well-known/jwks.json") return json(200, { keys: [idpJwk] });

      if (url.pathname === "/authorize") {
        const p = url.searchParams;
        if (p.get("client_id") !== clientId || p.get("redirect_uri") !== opts.redirectUri || p.get("response_type") !== "code" || p.get("code_challenge_method") !== "S256") {
          return json(400, { error: "invalid_request" });
        }
        const code = b64url(randomBytes(24));
        const user = opts.freshSubjectPerLogin ? { sub: `test-psut-${randomUUID()}`, name: "Test Person (Fixture)" } : idp.nextUser;
        codes.set(code, { nonce: p.get("nonce")!, challenge: p.get("code_challenge")!, redirectUri: p.get("redirect_uri")!, user, acr: idp.faults.idTokenAcr ?? "mosip:idp:acr:generated-code" });
        const back = new URL(opts.redirectUri);
        back.searchParams.set("code", code);
        back.searchParams.set("state", p.get("state")!);
        res.writeHead(302, { location: back.toString() });
        return res.end();
      }

      if (url.pathname === "/oauth/v2/token" && req.method === "POST") {
        idp.stats.tokenRequests++;
        const form = new URLSearchParams(await body(req));
        if (idp.faults.tokenError) return json(400, { error: "invalid_grant" });
        // Client authentication: private_key_jwt signed by the registered client key, aud = token endpoint.
        try {
          await jwtVerify(form.get("client_assertion") ?? "", clientPubKey, { issuer: clientId, subject: clientId, audience: `${issuer}/oauth/v2/token`, algorithms: ["RS256"] });
          if (form.get("client_assertion_type") !== "urn:ietf:params:oauth:client-assertion-type:jwt-bearer") throw new Error("type");
        } catch {
          idp.stats.rejectedAssertions++;
          return json(401, { error: "invalid_client" });
        }
        const entry = codes.get(form.get("code") ?? "");
        codes.delete(form.get("code") ?? ""); // single use
        if (!entry || form.get("redirect_uri") !== entry.redirectUri) return json(400, { error: "invalid_grant" });
        const challenge = createHash("sha256").update(form.get("code_verifier") ?? "").digest("base64url");
        if (challenge !== entry.challenge) {
          idp.stats.pkceFailures++;
          return json(400, { error: "invalid_grant" });
        }
        const f = idp.faults;
        const now = Math.floor(Date.now() / 1000);
        const iat = f.idTokenIatFuture ? now + 3600 : f.idTokenExpired ? now - 7200 : now;
        const exp = f.idTokenExpired ? now - 3600 : now + 300;
        const claims: Record<string, unknown> = {
          iss: f.idTokenIssuer ?? issuer,
          aud: f.idTokenAudience ?? clientId,
          sub: entry.user.sub,
          nonce: f.idTokenNonce ?? entry.nonce,
          acr: entry.acr,
          jti: randomUUID(),
        };
        if (f.idTokenAzp) claims.azp = f.idTokenAzp;
        const idToken = await sign(claims, { iat, exp });
        const accessToken = b64url(randomBytes(24));
        accessTokens.set(accessToken, entry.user);
        return json(200, { access_token: accessToken, id_token: idToken, token_type: "Bearer", expires_in: 300 });
      }

      if (url.pathname === "/oidc/userinfo") {
        const token = (req.headers.authorization ?? "").replace(/^Bearer /, "");
        const user = accessTokens.get(token);
        if (!user) return json(401, { error: "invalid_token" });
        const claims = { iss: issuer, aud: idp.faults.userinfoAudience ?? clientId, sub: idp.faults.userinfoSub ?? user.sub, name: user.name };
        if (idp.faults.userinfoUnsigned) return json(200, claims);
        const now = Math.floor(Date.now() / 1000);
        const jwt = await new SignJWT(claims).setProtectedHeader({ alg: "RS256", kid: "test-idp-1" }).setIssuedAt(now).setExpirationTime(now + 300).sign(idpPriv);
        res.writeHead(200, { "content-type": "application/jwt" });
        return res.end(jwt);
      }
      json(404, { error: "not_found" });
    } catch {
      json(500, { error: "server_error" });
    }
  });

  await new Promise<void>((r) => server.listen(opts.port ?? 0, "127.0.0.1", () => r()));
  issuer = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  idp.issuer = issuer;
  return idp;
}
