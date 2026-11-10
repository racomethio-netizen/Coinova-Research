import { describe, expect, it } from "vitest";
import { fmtDecimal, fmtPctFromFraction } from "./format";

describe("decimal display formatting (string-based, no float math)", () => {
  it("groups thousands and trims trailing zeros", () => {
    expect(fmtDecimal("12345678.12340000")).toBe("12,345,678.1234");
    expect(fmtDecimal("-62.91003000", 2)).toBe("-62.91");
    expect(fmtDecimal("100.00000000")).toBe("100");
    expect(fmtDecimal("0.000000012345", 12)).toBe("0.000000012345");
  });
  it("never guesses on bad input", () => {
    expect(fmtDecimal(null)).toBe("—");
    expect(fmtDecimal("1e5")).toBe("—");
    expect(fmtPctFromFraction(null)).toBe("—");
    expect(fmtPctFromFraction("0.5")).toBe("50.0%");
  });
});
