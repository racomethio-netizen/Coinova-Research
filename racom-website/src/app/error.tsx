"use client";

import Link from "next/link";
import { buttonClasses } from "@/components/ui/button";

export default function ErrorPage({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <section className="container-page flex min-h-[60vh] flex-col justify-center py-20">
      <p className="font-mono text-[0.72rem] tracking-[0.18em] text-violet-soft uppercase">
        Something went wrong
      </p>
      <h1 className="mt-5 max-w-2xl font-display text-4xl leading-tight font-semibold tracking-tight text-ink sm:text-5xl">
        We couldn’t load this page.
      </h1>
      <p className="mt-5 max-w-xl text-lg text-muted">This is on our side. Please try again in a moment.</p>
      <div className="mt-8 flex flex-col gap-3 min-[400px]:flex-row">
        <button type="button" onClick={reset} className={buttonClasses({ size: "lg" })}>
          Try again
        </button>
        <Link href="/" className={buttonClasses({ size: "lg", variant: "secondary" })}>
          Back to home
        </Link>
      </div>
    </section>
  );
}
