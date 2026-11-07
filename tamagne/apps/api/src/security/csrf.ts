import { randomUUID } from "node:crypto";
import type { NextFunction, Request, Response } from "express";
import { safeEqualHex } from "@tamagne/core";
import { readCookie } from "../common/http.js";

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

/**
 * CSRF protection for cookie-authenticated, state-changing requests (including
 * login and registration, to prevent login CSRF):
 *  1. Origin (or Referer) must be one of the configured web origins.
 *  2. Double-submit: X-CSRF-Token header must equal the CSRF cookie.
 * SameSite=Lax cookies are a third layer.
 */
export function csrfMiddleware(allowedOrigins: string[], csrfCookieName: string) {
  const allowed = new Set(allowedOrigins.map((o) => new URL(o).origin));
  return (req: Request, res: Response, next: NextFunction): void => {
    if (SAFE_METHODS.has(req.method)) return next();

    const origin = req.header("origin") ?? originOf(req.header("referer"));
    const cookieToken = readCookie(req, csrfCookieName);
    const headerToken = req.header("x-csrf-token");
    const tokensMatch =
      !!cookieToken &&
      !!headerToken &&
      /^[A-Za-z0-9_-]{43}$/.test(cookieToken) &&
      safeEqualHex(Buffer.from(cookieToken).toString("hex"), Buffer.from(headerToken).toString("hex"));

    if (!origin || !allowed.has(origin) || !tokensMatch) {
      res.status(403).json({
        error: {
          code: "csrf_failed",
          message: "This request could not be verified. Refresh the page and try again.",
          requestId: String(res.locals.requestId ?? randomUUID()),
        },
      });
      return;
    }
    next();
  };
}

function originOf(referer: string | undefined): string | undefined {
  if (!referer) return undefined;
  try {
    return new URL(referer).origin;
  } catch {
    return undefined;
  }
}
