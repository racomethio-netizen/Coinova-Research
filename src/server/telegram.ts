import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { siteConfig } from "@/config/site";
import { mapDbError } from "@/lib/db/errors";
import { ApiError } from "@/lib/http/api";
import { logger } from "@/lib/logger";
import { deepLink, hashLinkToken, isTelegramConfigured, newLinkToken, parseCommand, sendTelegramMessage, type TelegramUpdate } from "@/lib/notify/telegram";
import { requireServiceClient } from "@/lib/supabase/server";

export async function createLinkToken(userId: string) {
  if (!isTelegramConfigured()) throw new ApiError("not_configured", "Telegram notifications are not configured on this server.");
  const svc = requireServiceClient();
  // Invalidate any outstanding tokens for this user so only the newest works.
  await svc.from("telegram_link_tokens").update({ used_at: new Date().toISOString() }).eq("user_id", userId).is("used_at", null);
  const t = newLinkToken();
  const { error } = await svc.from("telegram_link_tokens").insert({ user_id: userId, token_hash: t.hash, expires_at: t.expiresAt });
  if (error) throw mapDbError(error, "createLinkToken");
  return { url: deepLink(t.token), expiresAt: t.expiresAt };
}

export async function unlinkTelegram(userSb: SupabaseClient, userId: string) {
  const { error } = await userSb.from("telegram_accounts").delete().eq("user_id", userId);
  if (error) throw mapDbError(error, "unlinkTelegram");
}

export type UpdateOutcome = "linked" | "invalid_token" | "unlinked" | "help" | "ignored";

/**
 * Process one verified Telegram update. Callers must verify the webhook
 * secret header before calling this.
 */
export async function handleTelegramUpdate(update: TelegramUpdate, db: SupabaseClient = requireServiceClient(), send = sendTelegramMessage): Promise<UpdateOutcome> {
  const msg = update.message;
  if (!msg || msg.chat.type !== "private" || !msg.from || msg.from.is_bot) return "ignored";
  const cmd = parseCommand(msg.text);
  const chatId = msg.chat.id;

  switch (cmd.kind) {
    case "link": {
      // Atomically consume the token: only an unused, unexpired token matches.
      const { data, error } = await db
        .from("telegram_link_tokens")
        .update({ used_at: new Date().toISOString() })
        .eq("token_hash", hashLinkToken(cmd.token))
        .is("used_at", null)
        .gt("expires_at", new Date().toISOString())
        .select("user_id")
        .maybeSingle();
      if (error || !data) {
        await send(chatId, `This link is invalid or has expired. Generate a new one from ${siteConfig.shortName} → Settings.`);
        return "invalid_token";
      }
      // A chat links to at most one account; relinking moves it.
      await db.from("telegram_accounts").delete().eq("chat_id", chatId);
      const { error: upErr } = await db
        .from("telegram_accounts")
        .upsert({ user_id: data.user_id, chat_id: chatId, telegram_user_id: msg.from.id, username: msg.from.username ?? null, linked_at: new Date().toISOString() }, { onConflict: "user_id" });
      if (upErr) {
        logger.error("telegram_link_failed", { code: upErr.code });
        await send(chatId, "Linking failed. Please try again.");
        return "invalid_token";
      }
      await send(chatId, `Linked to your ${siteConfig.shortName} account. You will receive alerts you route to Telegram. Send /stop to unlink.`);
      return "linked";
    }
    case "unlink":
      await db.from("telegram_accounts").delete().eq("chat_id", chatId);
      await send(chatId, "Unlinked. You will no longer receive alerts here.");
      return "unlinked";
    case "help":
      await send(chatId, `To link this chat, open ${siteConfig.shortName} → Settings → Telegram and use the link shown there.`);
      return "help";
    default:
      return "ignored";
  }
}
