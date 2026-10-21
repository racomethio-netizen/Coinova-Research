import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

/**
 * Network-boundary proxy (Next.js 16 replacement for middleware).
 *
 * 1. Refreshes the Supabase session cookie once per navigation.
 * 2. Optimistically redirects signed-out users away from /dashboard and
 *    /admin. This is a UX convenience only: every protected page, server
 *    action, and route handler re-checks identity and authorization on the
 *    server, and RLS enforces ownership in the database.
 */
const PROTECTED = [/^\/dashboard(\/|$)/, /^\/admin(\/|$)/];

export async function proxy(request: NextRequest) {
  let response = NextResponse.next({ request });
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_ANON_KEY;
  const isProtected = PROTECTED.some((r) => r.test(request.nextUrl.pathname));

  if (!url || !key) {
    // Supabase not configured: protected areas cannot work; send to login, which explains why.
    if (isProtected) return redirectToLogin(request);
    return response;
  }

  const supabase = createServerClient(url, key, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (list) => {
        for (const { name, value } of list) request.cookies.set(name, value);
        response = NextResponse.next({ request });
        for (const { name, value, options } of list) response.cookies.set(name, value, options);
      },
    },
  });

  // getClaims() verifies the JWT (and refreshes it if needed) before we trust it.
  const { data } = await supabase.auth.getClaims();
  const signedIn = Boolean(data?.claims?.sub);

  if (isProtected && !signedIn) return redirectToLogin(request);
  if (isProtected) response.headers.set("Cache-Control", "private, no-store");
  return response;
}

function redirectToLogin(request: NextRequest) {
  const login = request.nextUrl.clone();
  login.pathname = "/login";
  login.search = "";
  login.searchParams.set("next", request.nextUrl.pathname);
  return NextResponse.redirect(login);
}

export const config = {
  matcher: [
    // Skip static assets, images, and machine endpoints that authenticate differently.
    "/((?!_next/static|_next/image|favicon.ico|icon.svg|robots.txt|api/health|api/billing/webhook|api/telegram/webhook|api/cron).*)",
  ],
};
