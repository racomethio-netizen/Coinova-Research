/**
 * Bearer-token parsing for non-browser clients (the mobile app).
 *
 * A request carrying `Authorization: Bearer <Supabase access token>` is
 * authenticated by that token alone; cookies are ignored. CSRF checks are not
 * needed on that path, because a cross-site page cannot attach an
 * Authorization header without a CORS preflight, and this API sends no CORS
 * headers. The token is always verified server-side (getClaims), never trusted.
 */
const JWT_SHAPE = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;
const MAX_TOKEN_LENGTH = 8192;

/**
 * Returns the token; null when there is no Bearer credential (no header, or
 * another scheme such as Basic auth in front of a staging site); "invalid"
 * for a malformed Bearer header.
 */
export function parseBearer(header: string | null | undefined): string | null | "invalid" {
  const value = header?.trim() ?? "";
  if (!/^bearer(\s|$)/i.test(value)) return null;
  const m = /^Bearer[ ]+(\S+)$/i.exec(value);
  if (!m) return "invalid";
  const token = m[1]!;
  if (token.length > MAX_TOKEN_LENGTH || !JWT_SHAPE.test(token)) return "invalid";
  return token;
}
