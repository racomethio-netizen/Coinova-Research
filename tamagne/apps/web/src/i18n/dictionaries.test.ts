import { describe, expect, it } from "vitest";
import { getDictionary, hasLocale, locales } from "./dictionaries";

function shape(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(shape);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, shape(v)]));
  return typeof value;
}

function strings(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(strings);
  if (value && typeof value === "object") return Object.values(value).flatMap(strings);
  return [];
}

describe("dictionaries", () => {
  it("English and Amharic have identical structure", () => {
    expect(shape(getDictionary("am"))).toEqual(shape(getDictionary("en")));
  });

  it("Amharic copy is actually written in Ethiopic script", () => {
    const am = strings(getDictionary("am")).filter((s) => s !== "English" && !/^\d+$/.test(s));
    for (const s of am) expect(s, s).toMatch(/[ሀ-፿]/);
  });

  it("both languages state independence from government and the limits of verification", () => {
    expect(getDictionary("en").footer.independence).toMatch(/not a government institution/);
    expect(getDictionary("en").limits.items.join(" ")).toMatch(/does not guarantee/);
    expect(getDictionary("am").footer.independence).toMatch(/የመንግሥት ተቋም አይደለም/);
  });

  it("only supported locales are accepted", () => {
    expect(locales).toEqual(["en", "am"]);
    expect(hasLocale("am")).toBe(true);
    expect(hasLocale("fr")).toBe(false);
    expect(hasLocale("__proto__")).toBe(false);
  });
});
