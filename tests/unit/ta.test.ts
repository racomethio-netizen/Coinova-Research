import { describe, expect, it } from "vitest";
import { ema } from "@/lib/market/ta";

describe("display EMA (matches the engine definition)", () => {
  it("is undefined during warm-up and seeded with the SMA", () => {
    const out = ema([1, 2, 3, 4, 5], 3);
    expect(out.slice(0, 2)).toEqual([null, null]);
    expect(out[2]).toBe(2); // SMA of 1,2,3
    expect(out[3]).toBeCloseTo(0.5 * 4 + 0.5 * 2, 12); // alpha = 2/(3+1)
    expect(out[4]).toBeCloseTo(0.5 * 5 + 0.5 * 3, 12);
  });
  it("returns all nulls for short input", () => {
    expect(ema([1, 2], 3)).toEqual([null, null]);
  });
});
