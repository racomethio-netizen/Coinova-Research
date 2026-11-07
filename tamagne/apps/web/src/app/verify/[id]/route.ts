import { NextResponse, type NextRequest } from "next/server";

/**
 * Language-neutral target printed in QR codes: /verify/<token or Tamagne ID>.
 * Redirects to the visitor's language (Amharic if their browser prefers it).
 */
export function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  return params.then(({ id }) => {
    const accept = (req.headers.get("accept-language") ?? "").toLowerCase();
    const first = accept.split(",")[0]?.trim() ?? "";
    const lang = first.startsWith("am") ? "am" : "en";
    const target = new URL(`/${lang}/verify/${encodeURIComponent(id)}`, req.nextUrl.origin);
    const res = NextResponse.redirect(target, 307);
    res.headers.set("Cache-Control", "no-store");
    return res;
  });
}
