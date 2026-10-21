import { getEnv } from "@/lib/env";
import { ApiError, clientIp, handler, ok } from "@/lib/http/api";
import { enforceRateLimit } from "@/lib/http/rate-limit";
import { logger } from "@/lib/logger";
import { TelegramUpdateSchema, verifyWebhookSecret } from "@/lib/notify/telegram";
import { handleTelegramUpdate } from "@/server/telegram";

export const dynamic = "force-dynamic";

/**
 * Telegram Bot webhook. Authenticated by the X-Telegram-Bot-Api-Secret-Token
 * header set via setWebhook(secret_token=TELEGRAM_WEBHOOK_SECRET).
 */
export const POST = handler("POST /api/telegram/webhook", async (req: Request) => {
  enforceRateLimit(`tg-webhook:${clientIp(req)}`, 600, 60_000);
  const env = getEnv();
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_WEBHOOK_SECRET) throw new ApiError("not_configured", "Telegram is not configured.");
  if (!verifyWebhookSecret(env.TELEGRAM_WEBHOOK_SECRET, req.headers.get("x-telegram-bot-api-secret-token"))) {
    throw new ApiError("unauthorized", "Invalid webhook secret.");
  }
  const raw = await req.text();
  if (raw.length > 64 * 1024) throw new ApiError("bad_request", "Payload too large.");
  let json: unknown;
  try {
    json = JSON.parse(raw);
  } catch {
    throw new ApiError("bad_request", "Malformed JSON.");
  }
  const parsed = TelegramUpdateSchema.safeParse(json);
  // Always 200 for well-authenticated but unsupported updates so Telegram does not retry forever.
  if (!parsed.success) return ok({ outcome: "ignored" });
  const outcome = await handleTelegramUpdate(parsed.data);
  logger.info("telegram_update", { outcome });
  return ok({ outcome });
});
