import { expect, test } from "@playwright/test";

test("every page gets a fresh nonce CSP; scripts carry it; no CSP violations while using the app", async ({ page }) => {
  const violations: string[] = [];
  page.on("console", (m) => {
    if (/Content Security Policy|Content-Security-Policy/i.test(m.text())) violations.push(m.text());
  });
  await page.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (e) => console.error(`Content Security Policy violation: ${e.violatedDirective} ${e.blockedURI}`));
  });
  const nonces = new Set<string>();
  for (const path of ["/en", "/am", "/en/register", "/en/login", "/en/verify", "/en/verify/TMG-0000000000", "/en/report", "/en/privacy", "/am/terms"]) {
    const res = await page.goto(path);
    const csp = res!.headers()["content-security-policy"]!;
    expect(csp, path).toMatch(/script-src 'self' 'nonce-[A-Za-z0-9+/=]+' 'strict-dynamic'/);
    expect(csp).not.toMatch(/script-src[^;]*'unsafe-inline'/);
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    const nonce = /'nonce-([^']+)'/.exec(csp)![1]!;
    nonces.add(nonce);
    // Every server-rendered script tag carries this response's nonce.
    const html = await res!.text();
    const scripts = html.match(/<script\b[^>]*>/g) ?? [];
    expect(scripts.length).toBeGreaterThan(0);
    for (const s of scripts) expect(s, path).toContain(`nonce="${nonce}"`);
    expect(res!.headers()["x-frame-options"]).toBe("DENY");
    expect(res!.headers()["cross-origin-opener-policy"]).toBe("same-origin");
    // The page is interactive (hydrated) under the policy.
    await page.waitForLoadState("networkidle");
  }
  expect(nonces.size).toBe(9);
  expect(violations).toEqual([]);
});
