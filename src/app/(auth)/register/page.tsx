import type { Metadata } from "next";
import { safeNextPath } from "@/lib/auth/validation";
import { RegisterForm } from "../forms";

export const metadata: Metadata = { title: "Create account", robots: { index: false } };

export default async function RegisterPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const sp = await searchParams;
  return (
    <>
      <h1 className="mb-1 text-xl font-semibold">Create your account</h1>
      <p className="mb-6 text-sm text-muted">Free accounts include market pages, sample scores, free research, and a watchlist.</p>
      <RegisterForm next={safeNextPath(sp.next)} />
    </>
  );
}
