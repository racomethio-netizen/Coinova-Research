import { afterEach, describe, expect, it, vi } from "vitest";
import { jsonLd, pageMetadata } from "@/lib/seo";
import { getSiteUrl } from "@/config/site";

afterEach(() => vi.unstubAllEnvs());

describe("seo helpers", () => {
  it("omits canonical URLs until the production origin is configured", () => {
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "");
    expect(pageMetadata({ title: "T", description: "D", path: "/work" }).alternates).toBeUndefined();
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "https://racom.example/");
    expect(pageMetadata({ title: "T", description: "D", path: "/work" }).alternates).toEqual({
      canonical: "/work",
    });
    expect(getSiteUrl()).toBe("https://racom.example");
  });

  it("ignores an invalid site URL", () => {
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "not a url");
    expect(getSiteUrl()).toBeNull();
  });

  it("escapes < in JSON-LD so content cannot close the script tag", () => {
    expect(jsonLd({ name: "</script><script>alert(1)</script>" })).not.toContain("</script>");
  });
});
