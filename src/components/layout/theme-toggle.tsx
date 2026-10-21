"use client";

const KEY = "cs-theme";

/** Inline script applied before paint so there is no flash of the wrong theme. */
export const themeInitScript = `try{var t=localStorage.getItem("${KEY}");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t;}catch(e){}`;

/**
 * Theme toggle. The current theme lives on <html data-theme>, so the icon is
 * chosen with CSS and no React state needs to mirror the DOM.
 */
export function ThemeToggle() {
  function toggle() {
    const root = document.documentElement;
    const next = root.dataset.theme === "light" ? "dark" : "light";
    root.dataset.theme = next;
    try {
      localStorage.setItem(KEY, next);
    } catch {
      // Storage unavailable (private mode); the theme still applies for this page view.
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted hover:bg-subtle hover:text-fg"
      aria-label="Toggle light and dark theme"
      title="Toggle theme"
    >
      <span aria-hidden className="[[data-theme=light]_&]:hidden">☀</span>
      <span aria-hidden className="hidden [[data-theme=light]_&]:inline">☾</span>
    </button>
  );
}
