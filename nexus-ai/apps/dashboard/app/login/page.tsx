"use client";

import { useState, type FormEvent } from "react";
import { api, ApiError } from "@/lib/api";
import { Button, Field } from "@/components/ui";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/auth/login", { email, password, totp: totp || null });
      window.location.href = "/";
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="grid min-h-screen place-items-center p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-2xl border border-line bg-panel p-6" aria-labelledby="login-title">
        <div className="flex items-center gap-2">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-accent font-bold text-white">N</div>
          <h1 id="login-title" className="text-lg font-semibold">Sign in to NEXUS AI</h1>
        </div>
        <Field id="email" label="Email" type="email" value={email} onChange={setEmail} autoComplete="username" />
        <Field id="password" label="Password" type="password" value={password} onChange={setPassword} autoComplete="current-password" />
        <Field id="totp" label="Two-factor code (if enabled)" value={totp} onChange={setTotp} inputMode="numeric" autoComplete="one-time-code" />
        {error && <p role="alert" className="text-sm text-down">{error}</p>}
        <Button type="submit" tone="primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</Button>
      </form>
    </main>
  );
}
