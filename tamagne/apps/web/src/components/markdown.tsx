import { Fragment } from "react";

/**
 * Minimal, safe renderer for our own policy texts: headings, paragraphs, bullet lists and
 * **bold**. Builds React elements; never injects HTML.
 */
function inline(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? <strong key={i}>{part.slice(2, -2)}</strong> : <Fragment key={i}>{part}</Fragment>,
  );
}

export function Markdown({ source }: { source: string }) {
  const blocks = source.trim().split(/\n\s*\n/);
  return (
    <div className="space-y-4 leading-relaxed">
      {blocks.map((block, i) => {
        const lines = block.split("\n");
        if (block.startsWith("# ")) return <h1 key={i} className="text-2xl font-bold">{inline(block.slice(2))}</h1>;
        if (block.startsWith("## ")) return <h2 key={i} className="pt-2 text-xl font-semibold">{inline(block.slice(3))}</h2>;
        if (block.startsWith("### ")) return <h3 key={i} className="font-semibold">{inline(block.slice(4))}</h3>;
        if (lines.every((l) => l.startsWith("- ") || l.startsWith("  "))) {
          const items: string[] = [];
          for (const l of lines) {
            if (l.startsWith("- ")) items.push(l.slice(2));
            else items[items.length - 1] += `\n${l.trim()}`;
          }
          return (
            <ul key={i} className="list-disc space-y-1.5 ps-5">
              {items.map((it, j) => {
                const [first, ...rest] = it.split("\n");
                return (
                  <li key={j}>
                    {inline(first!)}
                    {rest.length ? <ul className="mt-1 list-[circle] space-y-1 ps-5">{rest.map((r, k) => <li key={k}>{inline(r.replace(/^- /, ""))}</li>)}</ul> : null}
                  </li>
                );
              })}
            </ul>
          );
        }
        return <p key={i}>{inline(block.replace(/\n/g, " "))}</p>;
      })}
    </div>
  );
}
