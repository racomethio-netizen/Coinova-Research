import { randomUUID } from "node:crypto";
import { type NextRequest, NextResponse } from "next/server";
import type { ContactApiResponse } from "@/lib/contact/api";
import { inquiryRequestSchema } from "@/lib/contact/schema";
import { createRateLimiter } from "@/lib/contact/rate-limit";
import { checkSpam } from "@/lib/contact/spam";
import { deliverInquiry } from "@/server/contact/deliver";

const MAX_BODY_BYTES = 20_000;

const limiter = createRateLimiter({
  max: Number(process.env.CONTACT_RATE_LIMIT_MAX) || 5,
  windowMs: (Number(process.env.CONTACT_RATE_LIMIT_WINDOW_SECONDS) || 600) * 1000,
});

function fail(
  status: number,
  code: string,
  message: string,
  extra: Partial<{ fields: Record<string, string>; headers: HeadersInit }> = {},
) {
  return NextResponse.json<ContactApiResponse>(
    { ok: false, error: { code, message, ...(extra.fields ? { fields: extra.fields } : {}) } },
    { status, headers: { "Cache-Control": "no-store", ...extra.headers } },
  );
}

function clientIp(request: NextRequest): string {
  // Behind a trusted proxy (Vercel, Render, nginx) the first X-Forwarded-For entry is the client.
  return (
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    request.headers.get("x-real-ip") ||
    "unknown"
  );
}

/** Reject cross-site form posts: browsers send Origin on POST, and it must be this site. */
function isSameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  if (request.headers.get("sec-fetch-site") === "cross-site") return false;
  if (!origin) return true; // Non-browser clients; still validated, rate limited and spam-checked.
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

export async function POST(request: NextRequest) {
  if (!isSameOrigin(request)) return fail(403, "forbidden", "This request is not allowed.");
  if (!request.headers.get("content-type")?.includes("application/json")) {
    return fail(415, "unsupported_media_type", "Send the inquiry as JSON.");
  }

  const rate = limiter(clientIp(request));
  if (!rate.allowed) {
    return fail(429, "rate_limited", "Too many inquiries from this connection. Please try again later.", {
      headers: { "Retry-After": String(rate.retryAfterSeconds) },
    });
  }

  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) return fail(413, "too_large", "The inquiry is too long.");
  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch {
    return fail(400, "invalid_json", "The request could not be read.");
  }

  const parsed = inquiryRequestSchema.safeParse(body);
  if (!parsed.success) {
    const fields: Record<string, string> = {};
    for (const issue of parsed.error.issues) {
      const key = String(issue.path[0] ?? "form");
      fields[key] ??= issue.message;
    }
    return fail(422, "validation_error", "Please check the highlighted fields.", { fields });
  }

  const { website, startedAt, ...inquiry } = parsed.data;
  const spam = checkSpam({ website, startedAt });
  if (!spam.ok) {
    const message =
      spam.reason === "stale"
        ? "This form has expired. Please reload the page and try again."
        : "We could not accept this submission. Please wait a moment and try again.";
    return fail(422, `rejected_${spam.reason}`, message);
  }

  const reference = randomUUID();
  const result = await deliverInquiry(inquiry, reference);
  if (!result.ok) {
    return result.reason === "unconfigured"
      ? fail(503, "not_configured", "Online inquiries are temporarily unavailable. Please try again later.")
      : fail(
          502,
          "delivery_failed",
          "We could not send your inquiry right now. Please try again in a few minutes.",
        );
  }
  return NextResponse.json<ContactApiResponse>(
    { ok: true, delivery: result.channel, reference },
    { status: 200, headers: { "Cache-Control": "no-store" } },
  );
}
