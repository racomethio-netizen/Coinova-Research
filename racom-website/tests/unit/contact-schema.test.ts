import { describe, expect, it } from "vitest";
import { inquiryRequestSchema, inquirySchema } from "@/lib/contact/schema";

const valid = {
  name: "Abebe Kebede",
  email: "abebe@example.com",
  service: "ai-automation",
  message: "We would like to automate how incoming support requests are routed.",
  consent: true,
};

describe("inquirySchema", () => {
  it("accepts a minimal valid inquiry and normalises empty optional fields", () => {
    const parsed = inquirySchema.parse({ ...valid, company: "  ", budget: "", timeline: "" });
    expect(parsed.company).toBeUndefined();
    expect(parsed.budget).toBeUndefined();
    expect(parsed.timeline).toBeUndefined();
  });

  it("trims text fields", () => {
    expect(inquirySchema.parse({ ...valid, name: "  Abebe  " }).name).toBe("Abebe");
  });

  it.each([
    ["name", { name: "A" }],
    ["email", { email: "not-an-email" }],
    ["service", { service: "hacking" }],
    ["message", { message: "too short" }],
    ["consent", { consent: false }],
    ["budget", { budget: "a-million" }],
  ])("rejects an invalid %s", (field, patch) => {
    const result = inquirySchema.safeParse({ ...valid, ...patch });
    expect(result.success).toBe(false);
    expect(result.error?.issues[0]?.path[0]).toBe(field);
  });

  it("rejects control characters in single-line fields (header injection)", () => {
    expect(inquirySchema.safeParse({ ...valid, name: "Eve\r\nBcc: victim@example.com" }).success).toBe(false);
    expect(inquirySchema.safeParse({ ...valid, company: "Acme\nInc" }).success).toBe(false);
  });

  it("allows newlines in the project description", () => {
    expect(
      inquirySchema.safeParse({ ...valid, message: `${valid.message}\n\nSecond paragraph.` }).success,
    ).toBe(true);
  });

  it("rejects overly long descriptions", () => {
    expect(inquirySchema.safeParse({ ...valid, message: "x".repeat(5001) }).success).toBe(false);
  });
});

describe("inquiryRequestSchema", () => {
  it("requires the anti-spam timestamp", () => {
    expect(inquiryRequestSchema.safeParse(valid).success).toBe(false);
    expect(inquiryRequestSchema.safeParse({ ...valid, startedAt: 1, website: "" }).success).toBe(true);
  });
});
