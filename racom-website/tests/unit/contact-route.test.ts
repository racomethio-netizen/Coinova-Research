import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { POST } from "@/app/api/contact/route";

let ip = 0;
function request(body: unknown, headers: Record<string, string> = {}) {
  ip += 1;
  return new NextRequest("http://localhost:3000/api/contact", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      host: "localhost:3000",
      origin: "http://localhost:3000",
      "x-forwarded-for": `10.0.0.${ip}`,
      ...headers,
    },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

const valid = () => ({
  name: "Abebe Kebede",
  email: "abebe@example.com",
  service: "ai-automation",
  message: "We would like to automate how incoming support requests are routed.",
  consent: true,
  website: "",
  startedAt: Date.now() - 20_000,
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function configureResend() {
  vi.stubEnv("RESEND_API_KEY", "test-key");
  vi.stubEnv("CONTACT_FROM_EMAIL", "site@racom.test");
  vi.stubEnv("CONTACT_TO_EMAIL", "team@racom.test");
}

describe("POST /api/contact", () => {
  it("delivers a valid inquiry by email and returns a reference", async () => {
    configureResend();
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const res = await POST(request(valid()));
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json).toMatchObject({ ok: true, delivery: "email" });
    expect(json.reference).toMatch(/^[0-9a-f-]{36}$/);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("returns 502 — not success — when the email provider fails", async () => {
    configureResend();
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 500 })));
    const res = await POST(request(valid()));
    expect(res.status).toBe(502);
    expect((await res.json()).ok).toBe(false);
  });

  it("returns 503 when delivery is not configured outside development", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const res = await POST(request(valid()));
    expect(res.status).toBe(503);
    expect((await res.json()).error.code).toBe("not_configured");
  });

  it("returns field errors for invalid input", async () => {
    const res = await POST(request({ ...valid(), email: "nope", message: "short" }));
    expect(res.status).toBe(422);
    const { error } = await res.json();
    expect(Object.keys(error.fields).sort()).toEqual(["email", "message"]);
  });

  it("rejects cross-site requests, non-JSON bodies and oversized payloads", async () => {
    expect((await POST(request(valid(), { origin: "https://evil.test" }))).status).toBe(403);
    expect((await POST(request(valid(), { "content-type": "text/plain" }))).status).toBe(415);
    expect((await POST(request({ ...valid(), message: "x".repeat(25_000) }))).status).toBe(413);
    expect((await POST(request("{not json"))).status).toBe(400);
  });

  it("rejects honeypot and too-fast submissions without delivering", async () => {
    configureResend();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    expect((await POST(request({ ...valid(), website: "spam" }))).status).toBe(422);
    expect((await POST(request({ ...valid(), startedAt: Date.now() }))).status).toBe(422);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rate limits repeated requests from one client", async () => {
    const headers = { "x-forwarded-for": "203.0.113.9" };
    const statuses: number[] = [];
    for (let i = 0; i < 6; i++) statuses.push((await POST(request({}, headers))).status);
    expect(statuses.slice(0, 5).every((s) => s === 422)).toBe(true);
    expect(statuses[5]).toBe(429);
  });
});
