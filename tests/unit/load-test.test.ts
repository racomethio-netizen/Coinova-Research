import { describe, expect, it } from "vitest";
import { percentile } from "../../scripts/load-test";

describe("load-test percentile", () => {
  it("uses the nearest-rank method", () => {
    const xs = Array.from({ length: 100 }, (_, i) => i + 1);
    expect(percentile(xs, 50)).toBe(50);
    expect(percentile(xs, 95)).toBe(95);
    expect(percentile(xs, 100)).toBe(100);
    expect(percentile([7], 99)).toBe(7);
    expect(Number.isNaN(percentile([], 50))).toBe(true);
  });
});
