"use client";

/**
 * Browser → API calls go through the same-origin /api proxy (next.config rewrites),
 * so session cookies are first-party. State-changing requests carry the CSRF token
 * from the readable CSRF cookie (double-submit).
 */
export class ApiRequestError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: { path: string; message: string }[],
  ) {
    super(message);
  }
}

function readCsrfCookie(): string | undefined {
  for (const part of document.cookie.split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === "tamagne_csrf" || k === "__Host-tamagne_csrf") return v.join("=");
  }
  return undefined;
}

async function csrfToken(): Promise<string> {
  const existing = readCsrfCookie();
  if (existing) return existing;
  const res = await fetch("/api/v1/auth/csrf", { credentials: "same-origin" });
  const body = (await res.json()) as { data: { csrfToken: string } };
  return body.data.csrfToken;
}

export async function apiSend<T>(method: "POST" | "PATCH" | "DELETE", path: string, body?: unknown): Promise<T> {
  const token = await csrfToken();
  const res = await fetch(`/api/v1${path}`, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const json = (await res.json().catch(() => ({}))) as { data?: T; error?: { code: string; message: string; details?: { path: string; message: string }[] } };
  if (!res.ok || json.error) {
    throw new ApiRequestError(res.status, json.error?.code ?? "error", json.error?.message ?? "Request failed", json.error?.details);
  }
  return json.data as T;
}
