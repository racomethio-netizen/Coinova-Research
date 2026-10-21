import "server-only";
import { getEnv } from "@/lib/env";
import { fetchWithTimeout } from "@/lib/http/fetch";
import { logger } from "@/lib/logger";

/**
 * Transactional email behind an interface.
 * - console: development only; logs that an email WOULD be sent (recipient
 *   domain and subject only, never the body). Forbidden in production.
 * - resend: Resend HTTP API (https://resend.com/docs/api-reference/emails/send-email).
 *   Implemented but UNVERIFIED until configured with a real key and domain.
 */

export interface EmailMessage {
  to: string;
  subject: string;
  text: string;
}

export type SendResult = { ok: true; messageId: string } | { ok: false; error: string; permanent: boolean };

export interface EmailProvider {
  readonly id: string;
  send(msg: EmailMessage): Promise<SendResult>;
}

class ConsoleEmailProvider implements EmailProvider {
  readonly id = "console";
  async send(msg: EmailMessage): Promise<SendResult> {
    logger.info("email_console_delivery", { toDomain: msg.to.split("@")[1], subject: msg.subject });
    return { ok: true, messageId: `console-${Date.now()}` };
  }
}

class ResendEmailProvider implements EmailProvider {
  readonly id = "resend";
  constructor(
    private readonly apiKey: string,
    private readonly from: string,
  ) {}
  async send(msg: EmailMessage): Promise<SendResult> {
    try {
      const res = await fetchWithTimeout("https://api.resend.com/emails", {
        method: "POST",
        timeoutMs: 10_000,
        headers: { Authorization: `Bearer ${this.apiKey}`, "Content-Type": "application/json" },
        body: JSON.stringify({ from: this.from, to: [msg.to], subject: msg.subject, text: msg.text }),
      });
      const json = (await res.json().catch(() => ({}))) as { id?: string; message?: string };
      if (res.ok && json.id) return { ok: true, messageId: json.id };
      return { ok: false, error: `resend ${res.status}: ${json.message ?? "error"}`.slice(0, 300), permanent: res.status >= 400 && res.status < 500 && res.status !== 429 };
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : "network error", permanent: false };
    }
  }
}

export function getEmailProvider(): EmailProvider {
  const env = getEnv();
  if (env.EMAIL_PROVIDER === "resend") return new ResendEmailProvider(env.RESEND_API_KEY!, env.EMAIL_FROM!);
  return new ConsoleEmailProvider();
}
