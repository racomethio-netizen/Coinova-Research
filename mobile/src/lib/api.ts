import { envelope, type ApiError } from "@nexus/shared";
import type { z } from "zod";

export type Result<T> = { ok: true; data: T } | { ok: false; error: ApiError };

/**
 * Typed API client. Every response is validated against the shared contract
 * (packages/shared); a mismatch is reported as an error, never rendered as data.
 * Auth: `Authorization: Bearer <Supabase access token>` (cookies are never used).
 */
export function createApi(baseUrl: string, getToken: () => Promise<string | null>, fetchImpl: typeof fetch = fetch) {
  async function call<S extends z.ZodType>(path: string, schema: S, init: { method?: string; body?: unknown } = {}): Promise<Result<z.infer<S>>> {
    if (!baseUrl) return { ok: false, error: { code: "not_configured", message: "The app is not configured with a server address." } };
    const token = await getToken();
    let res: Response;
    try {
      res = await fetchImpl(`${baseUrl}${path}`, {
        method: init.method ?? (init.body === undefined ? "GET" : "POST"),
        headers: {
          accept: "application/json",
          ...(init.body !== undefined ? { "content-type": "application/json" } : {}),
          ...(token ? { authorization: `Bearer ${token}` } : {}),
        },
        body: init.body === undefined ? undefined : JSON.stringify(init.body),
      });
    } catch {
      return { ok: false, error: { code: "network_error", message: "Could not reach the server. Check your connection." } };
    }
    const json: unknown = await res.json().catch(() => null);
    const parsed = envelope(schema).safeParse(json);
    if (!parsed.success) return { ok: false, error: { code: "invalid_response", message: "The server sent an unexpected response." } };
    // Validated above; narrow the generic union explicitly (TypeScript cannot through the generic schema).
    const body = parsed.data as { ok: true; data: z.infer<S> } | { ok: false; error: ApiError };
    return body.ok ? { ok: true, data: body.data } : { ok: false, error: body.error };
  }
  return { call };
}
