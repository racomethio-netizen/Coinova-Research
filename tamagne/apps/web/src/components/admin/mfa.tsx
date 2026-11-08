"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiRequestError, apiSend } from "@/lib/client-api";

function CodeForm({ path, label, onDone }: { path: string; label: string; onDone: () => void }) {
  const id = useId();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="space-y-3"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
          await apiSend("POST", path, { code });
          onDone();
        } catch (err) {
          setError(err instanceof ApiRequestError ? err.message : "Verification failed");
        } finally {
          setBusy(false);
        }
      }}
    >
      <Label htmlFor={id}>Authenticator code</Label>
      <Input
        id={id}
        inputMode="numeric"
        autoComplete="one-time-code"
        maxLength={6}
        value={code}
        onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
        aria-invalid={!!error || undefined}
        className="max-w-48 text-center text-xl tracking-[0.4em]"
      />
      {error ? <Alert tone="error">{error}</Alert> : null}
      <Button type="submit" disabled={busy || code.length !== 6}>
        {label}
      </Button>
    </form>
  );
}

export function MfaEnroll() {
  const router = useRouter();
  const [enrol, setEnrol] = useState<{ secret: string; qrSvg: string } | null>(null);
  const [busy, setBusy] = useState(false);
  if (!enrol) {
    return (
      <Button
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            setEnrol(await apiSend<{ secret: string; qrSvg: string }>("POST", "/auth/mfa/totp/enroll"));
          } finally {
            setBusy(false);
          }
        }}
      >
        Set up authenticator app
      </Button>
    );
  }
  return (
    <div className="space-y-4">
      <p className="text-sm">Scan this code with an authenticator app (for example Google Authenticator or Microsoft Authenticator), then enter the 6-digit code it shows.</p>
      {/* eslint-disable-next-line @next/next/no-img-element -- local data URI */}
      <img src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(enrol.qrSvg)}`} alt="Authenticator setup QR code" width={180} height={180} className="rounded border border-line" />
      <p className="text-sm">
        Can&apos;t scan? Enter this key manually: <code className="break-all font-mono" data-testid="totp-secret">{enrol.secret}</code>
      </p>
      <CodeForm path="/auth/mfa/totp/confirm" label="Confirm" onDone={() => router.refresh()} />
    </div>
  );
}

export function MfaVerify() {
  const router = useRouter();
  return <CodeForm path="/auth/mfa/verify" label="Verify" onDone={() => router.refresh()} />;
}
