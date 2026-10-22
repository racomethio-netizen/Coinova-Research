import { describe, expect, it, vi } from "vitest";
import { deliverInquiry, getDeliveryConfig } from "@/server/contact/deliver";
import { buildInquiryEmail } from "@/server/contact/email";
import type { Inquiry } from "@/lib/contact/schema";

const inquiry: Inquiry = {
  name: "Mallory <script>",
  email: "mallory@example.com",
  service: "web-development",
  message: "<img src=x onerror=alert(1)> please build a site",
  consent: true,
  company: undefined,
  location: undefined,
  budget: "5k-15k",
  timeline: undefined,
};

describe("getDeliveryConfig", () => {
  it("uses Resend only when key, sender and recipient are all set", () => {
    expect(
      getDeliveryConfig({
        RESEND_API_KEY: "k",
        CONTACT_FROM_EMAIL: "a@x.test",
        CONTACT_TO_EMAIL: "b@x.test, c@x.test",
      }),
    ).toEqual({
      mode: "resend",
      apiKey: "k",
      from: "a@x.test",
      to: ["b@x.test", "c@x.test"],
    });
    expect(getDeliveryConfig({ RESEND_API_KEY: "k", NODE_ENV: "production" }).mode).toBe("unconfigured");
  });

  it("falls back to local storage only in development", () => {
    expect(getDeliveryConfig({ NODE_ENV: "development" }).mode).toBe("local");
    expect(getDeliveryConfig({ NODE_ENV: "production" }).mode).toBe("unconfigured");
    expect(getDeliveryConfig({ NODE_ENV: "test" }).mode).toBe("unconfigured");
  });
});

describe("buildInquiryEmail", () => {
  it("escapes user input in the HTML part", () => {
    const { html, text, subject } = buildInquiryEmail(inquiry, "ref-1");
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img src=x onerror=alert(1)&gt;");
    expect(text).toContain("Budget: $5,000 – $15,000");
    expect(subject).toContain("Mallory");
  });
});

describe("deliverInquiry", () => {
  const config = { mode: "resend" as const, apiKey: "secret-key", from: "site@x.test", to: ["team@x.test"] };

  it("reports success only when the provider accepts the email", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    await expect(deliverInquiry(inquiry, "ref-2", { config, fetchImpl })).resolves.toEqual({
      ok: true,
      channel: "email",
    });
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("https://api.resend.com/emails");
    expect(init.headers.Authorization).toBe("Bearer secret-key");
    expect(init.headers["Idempotency-Key"]).toBe("inquiry-ref-2");
    expect(JSON.parse(init.body).reply_to).toBe("mallory@example.com");
  });

  it("reports failure when the provider rejects or is unreachable, without logging the key", async () => {
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    const rejected = vi.fn().mockResolvedValue(new Response("{}", { status: 422 }));
    await expect(deliverInquiry(inquiry, "ref-3", { config, fetchImpl: rejected })).resolves.toEqual({
      ok: false,
      reason: "provider_error",
    });
    const down = vi.fn().mockRejectedValue(new TypeError("fetch failed"));
    await expect(deliverInquiry(inquiry, "ref-4", { config, fetchImpl: down })).resolves.toEqual({
      ok: false,
      reason: "provider_error",
    });
    expect(log.mock.calls.flat().join(" ")).not.toContain("secret-key");
    log.mockRestore();
  });

  it("never claims delivery when unconfigured", async () => {
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    await expect(deliverInquiry(inquiry, "ref-5", { config: { mode: "unconfigured" } })).resolves.toEqual({
      ok: false,
      reason: "unconfigured",
    });
    log.mockRestore();
  });
});
