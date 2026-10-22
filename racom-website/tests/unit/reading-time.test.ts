import { describe, expect, it } from "vitest";
import { countWords, plainTextFromMdx, readingTimeMinutes } from "@/lib/reading-time";

describe("reading time", () => {
  it("counts words, including contractions and hyphenated words", () => {
    expect(countWords("Don't over-engineer the first release.")).toBe(5);
  });

  it("ignores the metadata export, code blocks, JSX and link targets", () => {
    const source = [
      "export const metadata = {",
      '  title: "A title with many words in it",',
      "};",
      "",
      "Hello [world](https://example.com/a/very/long/url).",
      "",
      "```ts",
      "const ignored = true;",
      "```",
      "",
      '<Callout kind="note">Read me</Callout>',
    ].join("\n");
    const text = plainTextFromMdx(source);
    expect(text).not.toContain("title");
    expect(text).not.toContain("ignored");
    expect(text).not.toContain("example.com");
    expect(countWords(text)).toBe(4); // Hello world Read me
  });

  it("rounds to whole minutes at 220 wpm with a minimum of one", () => {
    expect(readingTimeMinutes("word")).toBe(1);
    expect(readingTimeMinutes(Array(660).fill("word").join(" "))).toBe(3);
    expect(readingTimeMinutes(Array(1100).fill("word").join(" "))).toBe(5);
  });
});
