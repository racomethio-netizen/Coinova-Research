import { NextResponse } from "next/server";
import { z } from "zod";
import { logger } from "@/lib/logger";

/**
 * Consistent API response envelope:
 *   success: { ok: true, data }
 *   failure: { ok: false, error: { code, message, details? } }
 */

export type ApiErrorCode =
  | "bad_request"
  | "validation_error"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "rate_limited"
  | "payment_required"
  | "limit_reached"
  | "unavailable"
  | "not_configured"
  | "internal_error";

const STATUS: Record<ApiErrorCode, number> = {
  bad_request: 400,
  validation_error: 422,
  unauthorized: 401,
  forbidden: 403,
  not_found: 404,
  conflict: 409,
  rate_limited: 429,
  payment_required: 402,
  limit_reached: 403,
  unavailable: 503,
  not_configured: 503,
  internal_error: 500,
};

export class ApiError extends Error {
  constructor(
    public readonly code: ApiErrorCode,
    message: string,
    public readonly details?: unknown,
    public readonly headers?: Record<string, string>,
  ) {
    super(message);
    this.name = "ApiError";
  }
  get status() {
    return STATUS[this.code];
  }
}

const NO_STORE = { "Cache-Control": "private, no-store" };

export function ok<T>(data: T, init?: { status?: number; headers?: Record<string, string> }) {
  return NextResponse.json({ ok: true, data }, { status: init?.status ?? 200, headers: { ...NO_STORE, ...init?.headers } });
}

export function fail(code: ApiErrorCode, message: string, details?: unknown, headers?: Record<string, string>) {
  return NextResponse.json(
    { ok: false, error: { code, message, ...(details === undefined ? {} : { details }) } },
    { status: STATUS[code], headers: { ...NO_STORE, ...headers } },
  );
}

/** Convert any thrown value into a safe response. Internal details are logged, never returned. */
export function toErrorResponse(err: unknown, context?: Record<string, unknown>) {
  if (err instanceof ApiError) return fail(err.code, err.message, err.details, err.headers);
  if (err instanceof z.ZodError) return fail("validation_error", "Request validation failed.", formatZodIssues(err));
  logger.error("unhandled_api_error", { ...context, err });
  return fail("internal_error", "An unexpected error occurred.");
}

export function formatZodIssues(err: z.ZodError) {
  return err.issues.map((i) => ({ path: i.path.join("."), message: i.message }));
}

/**
 * Wrap a route handler so every thrown error becomes a consistent, safe response.
 */
export function handler<Args extends unknown[]>(
  name: string,
  fn: (...args: Args) => Promise<Response>,
): (...args: Args) => Promise<Response> {
  return async (...args: Args) => {
    try {
      return await fn(...args);
    } catch (err) {
      return toErrorResponse(err, { route: name });
    }
  };
}

const MAX_JSON_BYTES = 64 * 1024;

export async function parseJsonBody<S extends z.ZodType>(req: Request, schema: S): Promise<z.infer<S>> {
  const type = req.headers.get("content-type") ?? "";
  if (!type.toLowerCase().includes("application/json")) {
    throw new ApiError("bad_request", "Content-Type must be application/json.");
  }
  const text = await req.text();
  if (text.length > MAX_JSON_BYTES) throw new ApiError("bad_request", "Request body too large.");
  let raw: unknown;
  try {
    raw = text.length ? JSON.parse(text) : {};
  } catch {
    throw new ApiError("bad_request", "Malformed JSON body.");
  }
  const parsed = schema.safeParse(raw);
  if (!parsed.success) throw new ApiError("validation_error", "Request validation failed.", formatZodIssues(parsed.error));
  return parsed.data;
}

export function parseQuery<S extends z.ZodType>(url: URL | string, schema: S): z.infer<S> {
  const u = typeof url === "string" ? new URL(url) : url;
  const obj: Record<string, string> = {};
  u.searchParams.forEach((v, k) => {
    obj[k] = v;
  });
  const parsed = schema.safeParse(obj);
  if (!parsed.success) throw new ApiError("validation_error", "Invalid query parameters.", formatZodIssues(parsed.error));
  return parsed.data;
}

/**
 * CSRF defense for cookie-authenticated, state-changing API routes.
 * Browsers always send Origin on cross-origin POST/PATCH/DELETE; we require it
 * to match the request host (or the configured site URL).
 */
export function assertSameOrigin(req: Request, siteUrl?: string) {
  const method = req.method.toUpperCase();
  if (method === "GET" || method === "HEAD" || method === "OPTIONS") return;
  const origin = req.headers.get("origin");
  if (!origin) throw new ApiError("forbidden", "Missing Origin header.");
  let originHost: string;
  try {
    originHost = new URL(origin).host;
  } catch {
    throw new ApiError("forbidden", "Invalid Origin header.");
  }
  const allowed = new Set<string>();
  const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host");
  if (host) allowed.add(host);
  if (siteUrl) allowed.add(new URL(siteUrl).host);
  if (!allowed.has(originHost)) throw new ApiError("forbidden", "Cross-origin request rejected.");
}

export function clientIp(req: Request): string {
  const fwd = req.headers.get("x-forwarded-for");
  if (fwd) return fwd.split(",")[0]!.trim();
  return req.headers.get("x-real-ip") ?? "unknown";
}
