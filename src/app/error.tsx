"use client";

import { useEffect } from "react";
import { Button, Container } from "@/components/ui";

export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // The server already logged details; only the digest is exposed to the browser.
    console.error("page_error", error.digest);
  }, [error]);

  return (
    <Container className="py-20 text-center">
      <h1 className="text-2xl font-semibold">Something went wrong</h1>
      <p className="mt-2 text-muted">An unexpected error occurred while loading this page.</p>
      {error.digest ? <p className="mt-1 text-xs text-faint">Reference: {error.digest}</p> : null}
      <Button className="mt-6" onClick={reset}>
        Try again
      </Button>
    </Container>
  );
}
