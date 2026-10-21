import type { ReactNode } from "react";

/**
 * Minimal, XSS-safe Markdown renderer for research articles.
 *
 * Produces React elements only (never raw HTML), so article content cannot
 * inject scripts. Supported: ## / ### headings, paragraphs, - / 1. lists,
 * > blockquotes, ``` code fences, **bold**, *italic*, `code`, [text](https://...).
 * Links are restricted to https:, mailto:, and site-relative paths.
 */

function safeHref(href: string): string | null {
  const h = href.trim();
  if (h.startsWith("/") && !h.startsWith("//")) return h;
  try {
    const u = new URL(h);
    return u.protocol === "https:" || u.protocol === "mailto:" ? u.toString() : null;
  } catch {
    return null;
  }
}

const INLINE = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\))/g;

export function renderInline(text: string, keyPrefix = "i"): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let i = 0;
  for (const m of text.matchAll(INLINE)) {
    const idx = m.index ?? 0;
    if (idx > last) out.push(text.slice(last, idx));
    const tok = m[0];
    const key = `${keyPrefix}-${i++}`;
    if (tok.startsWith("**")) out.push(<strong key={key}>{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("`")) out.push(<code key={key}>{tok.slice(1, -1)}</code>);
    else if (tok.startsWith("[")) {
      const lm = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(tok)!;
      const href = safeHref(lm[2]!);
      if (href) {
        const external = !href.startsWith("/");
        out.push(
          <a key={key} href={href} {...(external ? { rel: "noopener noreferrer nofollow", target: "_blank" } : {})}>
            {lm[1]}
          </a>,
        );
      } else out.push(lm[1]);
    } else out.push(<em key={key}>{tok.slice(1, -1)}</em>);
    last = idx + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ source }: { source: string }) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let k = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    if (!line.trim()) {
      i++;
      continue;
    }
    if (line.startsWith("```")) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i]!.startsWith("```")) code.push(lines[i++]!);
      i++;
      blocks.push(
        <pre key={k++}>
          <code>{code.join("\n")}</code>
        </pre>,
      );
      continue;
    }
    if (line.startsWith("### ")) {
      blocks.push(<h3 key={k++}>{renderInline(line.slice(4), `h${k}`)}</h3>);
      i++;
      continue;
    }
    if (line.startsWith("## ") || line.startsWith("# ")) {
      blocks.push(<h2 key={k++}>{renderInline(line.replace(/^#+ /, ""), `h${k}`)}</h2>);
      i++;
      continue;
    }
    if (line.startsWith("> ")) {
      const q: string[] = [];
      while (i < lines.length && lines[i]!.startsWith("> ")) q.push(lines[i++]!.slice(2));
      blocks.push(<blockquote key={k++}>{renderInline(q.join(" "), `q${k}`)}</blockquote>);
      continue;
    }
    if (/^[-*] /.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*] /.test(lines[i]!)) items.push(lines[i++]!.slice(2));
      blocks.push(
        <ul key={k++}>
          {items.map((it, j) => (
            <li key={j}>{renderInline(it, `u${k}-${j}`)}</li>
          ))}
        </ul>,
      );
      continue;
    }
    if (/^\d+\. /.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\. /.test(lines[i]!)) items.push(lines[i++]!.replace(/^\d+\. /, ""));
      blocks.push(
        <ol key={k++}>
          {items.map((it, j) => (
            <li key={j}>{renderInline(it, `o${k}-${j}`)}</li>
          ))}
        </ol>,
      );
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i]!.trim() && !/^(#|> |[-*] |\d+\. |```)/.test(lines[i]!)) para.push(lines[i++]!);
    blocks.push(<p key={k++}>{renderInline(para.join(" "), `p${k}`)}</p>);
  }
  return <div className="prose-research">{blocks}</div>;
}
