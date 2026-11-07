import { HttpStatus, Logger } from "@nestjs/common";
import { maskPhone } from "@tamagne/core";
import { ApiError } from "../common/http.js";
import type { AppConfig } from "../config/env.js";

export interface SmsMessage {
  to: string;
  body: string;
}

/**
 * SMS delivery adapter. Production providers (e.g. Ethio telecom) are added only
 * once a contract and credentials exist; until then "disabled" refuses to send.
 */
export interface SmsSender {
  readonly name: string;
  readonly available: boolean;
  send(message: SmsMessage): Promise<void>;
}

export const SMS_SENDER = Symbol("SMS_SENDER");

class DisabledSmsSender implements SmsSender {
  readonly name = "disabled";
  readonly available = false;
  async send(): Promise<void> {
    throw new ApiError(HttpStatus.SERVICE_UNAVAILABLE, "sms_unavailable", "Phone verification is temporarily unavailable.");
  }
}

/** Development only (config refuses it elsewhere): prints messages to the server console. */
class ConsoleSmsSender implements SmsSender {
  readonly name = "console";
  readonly available = true;
  private readonly logger = new Logger("sms-console");
  async send({ to, body }: SmsMessage): Promise<void> {
    this.logger.warn(`[DEV ONLY] SMS to ${maskPhone(to)}: ${body}`);
  }
}

/** APP_ENV=test only: keeps messages in memory so tests can read OTP codes. */
export class TestOutboxSmsSender implements SmsSender {
  readonly name = "test";
  readonly available = true;
  readonly outbox: SmsMessage[] = [];
  async send(message: SmsMessage): Promise<void> {
    this.outbox.push(message);
    if (this.outbox.length > 1000) this.outbox.shift();
  }
  lastCodeFor(to: string): string | undefined {
    const msg = [...this.outbox].reverse().find((m) => m.to === to);
    return msg?.body.match(/\b(\d{6})\b/)?.[1];
  }
}

export function createSmsSender(config: AppConfig): SmsSender {
  switch (config.smsProvider) {
    case "console":
      return new ConsoleSmsSender();
    case "test":
      return new TestOutboxSmsSender();
    default:
      return new DisabledSmsSender();
  }
}

export function otpMessage(code: string, locale: "EN" | "AM"): string {
  return locale === "AM"
    ? `የታማኝ ማረጋገጫ ኮድዎ ${code} ነው። በ5 ደቂቃ ውስጥ ያበቃል። ለማንም አያጋሩ።`
    : `Your TAMAGNE code is ${code}. It expires in 5 minutes. Never share it with anyone.`;
}
