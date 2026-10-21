import { ApiError, clientIp, handler, ok } from "@/lib/http/api";
import { enforceRateLimit } from "@/lib/http/rate-limit";
import { getBillingProvider, processBillingWebhook } from "@/server/billing";

export const dynamic = "force-dynamic";

const MAX_BODY = 256 * 1024;

/**
 * Billing provider webhook. Authenticated by the provider's signature (not
 * cookies), so no CSRF origin check applies. The raw body is passed unmodified
 * to the adapter for signature verification.
 */
export const POST = handler("POST /api/billing/webhook", async (req: Request) => {
  enforceRateLimit(`billing-webhook:${clientIp(req)}`, 300, 60_000);
  const provider = getBillingProvider();
  if (!provider) throw new ApiError("not_configured", "Billing is not configured.");
  const raw = await req.text();
  if (raw.length > MAX_BODY) throw new ApiError("bad_request", "Payload too large.");
  const outcome = await processBillingWebhook(provider, raw, req.headers);
  return ok(outcome);
});
