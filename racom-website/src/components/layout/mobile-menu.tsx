"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Menu, X } from "lucide-react";
import type { NavItem } from "@/config/site";
import { Logo } from "@/components/brand/logo";
import { buttonClasses } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { isActivePath } from "./nav-links";

/**
 * Full-screen mobile navigation. Accessible dialog: labelled, focus moves into
 * the panel, Tab is contained, Escape closes and focus returns to the toggle.
 * The panel is portalled to <body> because the header's backdrop-filter would
 * otherwise become the containing block for `position: fixed`.
 */
export function MobileMenu({ items, cta }: { items: NavItem[]; cta: { label: string; href: string } }) {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  const toggleRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  function close({ restoreFocus }: { restoreFocus: boolean }) {
    setOpen(false);
    if (restoreFocus) toggleRef.current?.focus();
  }

  // While open: lock page scroll, move focus into the panel, handle Escape and contain Tab.
  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panelRef.current?.querySelector<HTMLElement>("nav a")?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        toggleRef.current?.focus();
        return;
      }
      if (event.key !== "Tab" || !panelRef.current) return;
      const focusable = Array.from(
        panelRef.current.querySelectorAll<HTMLElement>("a[href], button:not([disabled])"),
      );
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) return;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const links = [{ label: "Home", href: "/" }, ...items];

  return (
    <>
      <button
        ref={toggleRef}
        type="button"
        className="inline-flex h-11 w-11 items-center justify-center rounded-full border border-line-strong text-ink transition-colors hover:bg-white/5 lg:hidden"
        aria-expanded={open}
        aria-controls="mobile-menu"
        aria-label="Open menu"
        onClick={() => setOpen(true)}
      >
        <Menu className="h-5 w-5" aria-hidden="true" />
      </button>
      {open &&
        createPortal(
          <div
            ref={panelRef}
            id="mobile-menu"
            role="dialog"
            aria-modal="true"
            aria-label="Site navigation"
            className="fixed inset-0 z-50 flex animate-[fade-up_0.25s_var(--ease-out-expo)_both] flex-col overflow-y-auto bg-obsidian px-5 pb-8 lg:hidden"
          >
            <div className="flex h-[var(--header-height)] shrink-0 items-center justify-between border-b border-line">
              <Link
                href="/"
                onClick={() => close({ restoreFocus: false })}
                className="-m-1 rounded-lg p-1"
                aria-label="Racom Technology — home"
              >
                <Logo />
              </Link>
              <button
                type="button"
                onClick={() => close({ restoreFocus: true })}
                aria-label="Close menu"
                className="inline-flex h-11 w-11 items-center justify-center rounded-full border border-line-strong text-ink transition-colors hover:bg-white/5"
              >
                <X className="h-5 w-5" aria-hidden="true" />
              </button>
            </div>
            <nav aria-label="Mobile" className="mt-6">
              <ul className="flex flex-col">
                {links.map((item, i) => {
                  const active = item.href === "/" ? pathname === "/" : isActivePath(pathname, item.href);
                  return (
                    <li
                      key={item.href}
                      className="animate-fade-up border-b border-line"
                      style={{ animationDelay: `${30 * i}ms` }}
                    >
                      <Link
                        href={item.href}
                        onClick={() => close({ restoreFocus: false })}
                        aria-current={active ? "page" : undefined}
                        className={cn(
                          "flex min-h-14 items-center justify-between font-display text-2xl font-medium tracking-tight",
                          active ? "text-ink" : "text-muted",
                        )}
                      >
                        {item.label}
                        {active && <span className="h-1.5 w-1.5 rounded-full bg-cyan" aria-hidden="true" />}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </nav>
            <Link
              href={cta.href}
              onClick={() => close({ restoreFocus: false })}
              className={buttonClasses({ size: "lg", className: "mt-8 w-full" })}
            >
              {cta.label}
            </Link>
          </div>,
          document.body,
        )}
    </>
  );
}
