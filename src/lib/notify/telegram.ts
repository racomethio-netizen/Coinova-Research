import "server-only";
import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import { z } from "zod";
import { getEnv } from "@/lib/env";
import { fetchWithTimeout } from "@/lib/http/fetch";
import type { SendResult } from "./email";

/**
 * Telegram Bot API integration (https://core.telegram.org/bots/api).
 *
 * Account linking: the website issues a random, single-use token that
 * expires in 15 minutes (only its SHA-256 hash is stored). The user opens
 * t.me/<bot>?start=<token>; Telegram delivers "/start <token>" to our webhook,
 * which proves control of that Telegram chat. Usernames are never trusted as
 * identity. The bot token never leaves the server.
 *
 * NOTE: not exercised against the live Telegram API in this repository's CI
 * (api.telegram.org is unreachable from the build environment).
 */

export const LINK_TOKEN_TTL_MS = 15 * 60_000;

export function newLinkToken(): { token: string; hash: string; expiresAt: string } {
  const token = randomBytes(32).toString("base64url"); // 43 chars, [A-Za-z0-9_-]
  return { token, hash: hashLinkToken(token), expiresAt: new Date(Date.now() + LINK_TOKEN_TTL_MS).toISOString() };
}

export function hashLinkToken(token: string): string {
  return createHash("sha256").update(token).digest("hex");
}

export function verifyWebhookSecret(expected: string, provided: string | null): boolean {
  if (!provided) return false;
  const a = Buffer.from(expected);
  const b = Buffer.from(provided);
  return a.length === b.length && timingSafeEqual(a, b);
}

export const TelegramUpdateSchema = z.object({
  update_id: z.number().int(),
  message: z
    .object({
      message_id: z.number().int(),
      text: z.string().max(4096).optional(),
      chat: z.object({ id: z.number().int(), type: z.string() }),
      from: z.object({ id: z.number().int(), is_bot: z.boolean().optional(), username: z.string().max(64).optional() }).optional(),
    })
    .optional(),
});
export type TelegramUpdate = z.infer<typeof TelegramUpdateSchema>;

export type BotCommand = { kind: "link"; token: string } | { kind: "unlink" } | { kind: "help" } | { kind: "ignore" };

export function parseCommand(text: string | undefined): BotCommand {
  if (!text) return { kind: "ignore" };
  const t = text.trim();
  const start = /^\/start(?:@\w+)?\s+([A-Za-z0-9_-]{20,64})$/.exec(t);
  if (start) return { kind: "link", token: start[1]! };
  if (/^\/(stop|unlink)(?:@\w+)?$/.test(t)) return { kind: "unlink" };
  if (/^\/(start|help)(?:@\w+)?$/.test(t)) return { kind: "help" };
  return { kind: "ignore" };
}

export function isTelegramConfigured(): boolean {
  const env = getEnv();
  return Boolean(env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_WEBHOOK_SECRET && env.TELEGRAM_BOT_USERNAME);
}

export function deepLink(token: string): string | null {
  const username = getEnv().TELEGRAM_BOT_USERNAME;
  return username ? `https://t.me/${encodeURIComponent(username)}?start=${token}` : null;
}

export async function sendTelegramMessage(chatId: number, text: string, fetchImpl?: typeof fetch): Promise<SendResult> {
  const token = getEnv().TELEGRAM_BOT_TOKEN;
  if (!token) return { ok: false, error: "Telegram is not configured.", permanent: true };
  try {
    const res = await fetchWithTimeout(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: "POST",
      timeoutMs: 10_000,
      fetchImpl,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text: text.slice(0, 4000), disable_web_page_preview: true }),
    });
    const json = (await res.json().catch(() => ({}))) as { ok?: boolean; description?: string; result?: { message_id?: number } };
    if (res.ok && json.ok) return { ok: true, messageId: String(json.result?.message_id ?? "") };
    // 400 (chat not found) and 403 (bot blocked) will not succeed on retry.
    const permanent = res.status === 400 || res.status === 403;
    return { ok: false, error: `telegram ${res.status}: ${json.description ?? "error"}`.slice(0, 300), permanent };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.name : "network error", permanent: false };
  }
}
