import "server-only";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import type { Inquiry } from "@/lib/contact/schema";
import { buildInquiryEmail } from "./email";

export type DeliveryConfig =
  | { mode: "resend"; apiKey: string; from: string; to: string[] }
  | { mode: "local"; directory: string }
  | { mode: "unconfigured" };

export type DeliveryResult =
  | { ok: true; channel: "email" | "local" }
  | { ok: false; reason: "unconfigured" | "provider_error" };

type Env = Record<string, string | undefined>;

/**
 * Decide how inquiries are delivered:
 * - Resend when RESEND_API_KEY, CONTACT_FROM_EMAIL and CONTACT_TO_EMAIL are all set;
 * - otherwise, in development only, a local JSON file under ./.data/inquiries;
 * - otherwise "unconfigured" — the API reports an error and nothing is claimed as sent.
 */
export function getDeliveryConfig(env: Env = process.env): DeliveryConfig {
  const apiKey = env.RESEND_API_KEY?.trim();
  const from = env.CONTACT_FROM_EMAIL?.trim();
  const to = (env.CONTACT_TO_EMAIL ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (apiKey && from && to.length > 0) return { mode: "resend", apiKey, from, to };
  if (env.NODE_ENV === "development") {
    return { mode: "local", directory: path.join(process.cwd(), ".data", "inquiries") };
  }
  return { mode: "unconfigured" };
}

async function sendWithResend(
  config: Extract<DeliveryConfig, { mode: "resend" }>,
  inquiry: Inquiry,
  submissionId: string,
  fetchImpl: typeof fetch,
): Promise<boolean> {
  const { subject, text, html } = buildInquiryEmail(inquiry, submissionId);
  try {
    const res = await fetchImpl("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${config.apiKey}`,
        "Content-Type": "application/json",
        // Retries of the same submission never send a duplicate email.
        "Idempotency-Key": `inquiry-${submissionId}`,
      },
      body: JSON.stringify({
        from: config.from,
        to: config.to,
        reply_to: inquiry.email,
        subject,
        text,
        html,
      }),
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) {
      // Log only the status: never the API key or the inquiry's personal data.
      console.error(`[contact] Resend rejected inquiry ${submissionId}: HTTP ${res.status}`);
      return false;
    }
    return true;
  } catch (error) {
    console.error(`[contact] Resend request failed for inquiry ${submissionId}: ${(error as Error).name}`);
    return false;
  }
}

export async function deliverInquiry(
  inquiry: Inquiry,
  submissionId: string,
  {
    config = getDeliveryConfig(),
    fetchImpl = fetch,
  }: { config?: DeliveryConfig; fetchImpl?: typeof fetch } = {},
): Promise<DeliveryResult> {
  switch (config.mode) {
    case "resend":
      return (await sendWithResend(config, inquiry, submissionId, fetchImpl))
        ? { ok: true, channel: "email" }
        : { ok: false, reason: "provider_error" };
    case "local": {
      await mkdir(config.directory, { recursive: true });
      const file = path.join(
        config.directory,
        `${new Date().toISOString().replace(/[:.]/g, "-")}-${submissionId}.json`,
      );
      await writeFile(
        file,
        JSON.stringify({ id: submissionId, receivedAt: new Date().toISOString(), inquiry }, null, 2),
      );
      console.info(
        `[contact] Development fallback: inquiry ${submissionId} saved to ${path.relative(process.cwd(), file)} (not emailed).`,
      );
      return { ok: true, channel: "local" };
    }
    case "unconfigured":
      console.error(
        `[contact] Inquiry ${submissionId} could not be delivered: email delivery is not configured (see .env.example).`,
      );
      return { ok: false, reason: "unconfigured" };
  }
}
