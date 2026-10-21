import type { Metadata } from "next";
import { safeNextPath } from "@/lib/auth/validation";
import { LoginForm } from "../forms";

export const metadata: Metadata = { title: "Sign in", robots: { index: false } };

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ next?: string; error?: string }> }) {
  const sp = await searchParams;
  return (
    <>
      <h1 className="mb-1 text-xl font-semibold">Sign in</h1>
      <p className="mb-6 text-sm text-muted">Welcome back.</p>
      {sp.error === "callback" ? (
        <p role="alert" className="mb-4 text-sm text-negative">
          That link is invalid or has expired. Please sign in or request a new link.
        </p>
      ) : null}
      <LoginForm next={safeNextPath(sp.next)} />
    </>
  );
}
