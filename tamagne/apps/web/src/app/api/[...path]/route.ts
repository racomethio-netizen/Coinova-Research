import type { NextRequest } from "next/server";

/**
 * Same-origin proxy: browser → /api/v1/* → API (API_INTERNAL_URL, read at runtime).
 * Keeps session cookies first-party and CSP connect-src at 'self'. Only an allow-list
 * of headers crosses in either direction.
 */
export const dynamic = "force-dynamic";

const REQUEST_HEADERS = ["accept", "accept-language", "content-type", "cookie", "origin", "referer", "user-agent", "x-csrf-token", "x-request-id", "x-forwarded-for"];
const RESPONSE_HEADERS = ["content-type", "cache-control", "retry-after", "x-request-id"];

async function proxy(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const { path } = await ctx.params;
  if (path[0] !== "v1" || path.some((p) => p === ".." || p === "." || p.includes("/"))) {
    return Response.json({ error: { code: "not_found", message: "Not found.", requestId: "" } }, { status: 404 });
  }
  const base = process.env.API_INTERNAL_URL ?? "http://127.0.0.1:4000";
  const target = new URL(`/api/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`, base);

  const headers = new Headers();
  for (const h of REQUEST_HEADERS) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body: hasBody ? req.body : undefined,
      redirect: "manual",
      cache: "no-store",
      // Required by Node's fetch when streaming a request body.
      ...(hasBody ? { duplex: "half" } : {}),
    } as RequestInit);
  } catch {
    return Response.json({ error: { code: "unavailable", message: "Service temporarily unavailable.", requestId: "" } }, { status: 503 });
  }

  const out = new Headers();
  for (const h of RESPONSE_HEADERS) {
    const v = upstream.headers.get(h);
    if (v) out.set(h, v);
  }
  for (const c of upstream.headers.getSetCookie()) out.append("set-cookie", c);
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export { proxy as GET, proxy as POST, proxy as PATCH, proxy as DELETE };
