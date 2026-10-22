"use client";

import { useState } from "react";
import { Check, Link2 } from "lucide-react";

/**
 * Social sharing. Network share links need an absolute URL, so they render only
 * when the production origin is configured; "Copy link" always works.
 */
export function ShareLinks({ title, url }: { title: string; url: string | null }) {
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");

  async function copy() {
    try {
      await navigator.clipboard.writeText(url ?? window.location.href);
      setCopied("copied");
    } catch {
      setCopied("failed");
    }
  }

  const linkClass =
    "inline-flex min-h-10 items-center gap-2 rounded-full border border-line-strong px-4 text-sm text-muted transition-colors hover:border-violet-soft/50 hover:text-ink";

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="mr-1 text-sm text-subtle">Share</span>
      {url && (
        <>
          <a
            className={linkClass}
            href={`https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(url)}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            LinkedIn<span className="sr-only"> (opens in a new tab)</span>
          </a>
          <a
            className={linkClass}
            href={`https://x.com/intent/post?text=${encodeURIComponent(title)}&url=${encodeURIComponent(url)}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            X<span className="sr-only"> (opens in a new tab)</span>
          </a>
        </>
      )}
      <button type="button" onClick={copy} className={linkClass}>
        {copied === "copied" ? (
          <Check aria-hidden="true" className="h-4 w-4 text-success" />
        ) : (
          <Link2 aria-hidden="true" className="h-4 w-4" />
        )}
        {copied === "copied" ? "Link copied" : copied === "failed" ? "Copy failed" : "Copy link"}
      </button>
      <span className="sr-only" aria-live="polite">
        {copied === "copied"
          ? "Link copied to clipboard"
          : copied === "failed"
            ? "Could not copy the link"
            : ""}
      </span>
    </div>
  );
}
