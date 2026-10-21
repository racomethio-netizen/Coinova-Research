/**
 * Browser-side helper for our JSON API. Same-origin only; cookies carry the
 * session. Never send user ids, tiers, or prices — the server derives them.
 */
export type ApiResult<T> = { ok: true; data: T } | { ok: false; error: { code: string; message: string; details?: unknown } };

export async function api<T = unknown>(url: string, init: { method?: string; body?: unknown } = {}): Promise<ApiResult<T>> {
  try {
    const res = await fetch(url, {
      method: init.method ?? (init.body === undefined ? "GET" : "POST"),
      headers: init.body === undefined ? undefined : { "content-type": "application/json" },
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      credentials: "same-origin",
    });
    const json = (await res.json().catch(() => null)) as ApiResult<T> | null;
    if (json && typeof json === "object" && "ok" in json) return json;
    return { ok: false, error: { code: "bad_response", message: `Unexpected response (${res.status}).` } };
  } catch {
    return { ok: false, error: { code: "network_error", message: "Network error. Please check your connection." } };
  }
}

export function fieldErrorsFrom(details: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (Array.isArray(details)) for (const d of details) if (d && typeof d.path === "string" && !out[d.path]) out[d.path] = String(d.message);
  return out;
}
