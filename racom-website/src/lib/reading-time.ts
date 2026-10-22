const WORDS_PER_MINUTE = 220;

/**
 * Strip MDX/Markdown syntax that is not read aloud: ESM import/export blocks,
 * fenced code, JSX tags, link targets and formatting characters.
 */
export function plainTextFromMdx(source: string): string {
  return source
    .replace(/^(import|export)\s[\s\S]*?(?:;\s*$|\n\s*\n)/gm, " ")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[#>*_`~|-]/g, " ");
}

export function countWords(text: string): number {
  const words = text.match(/[\p{L}\p{N}][\p{L}\p{N}'’.-]*/gu);
  return words ? words.length : 0;
}

/** Estimated reading time in whole minutes (minimum 1), from the article's MDX source. */
export function readingTimeMinutes(source: string): number {
  return Math.max(1, Math.round(countWords(plainTextFromMdx(source)) / WORDS_PER_MINUTE));
}
