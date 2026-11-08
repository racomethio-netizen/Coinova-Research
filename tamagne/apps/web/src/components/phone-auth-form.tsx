"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { FieldError, Label } from "@/components/ui/label";
import type { Dictionary, Locale } from "@/i18n/dictionaries";
import { ApiRequestError, apiSend } from "@/lib/client-api";

type Mode = "register" | "login";

export function PhoneAuthForm({ mode, lang, t, legal }: { mode: Mode; lang: Locale; t: Dictionary["auth"]; legal: Dictionary["legal"] }) {
  const router = useRouter();
  const ids = { phone: useId(), phoneErr: useId(), phoneHint: useId(), code: useId(), codeErr: useId(), terms: useId(), privacy: useId(), consentErr: useId() };
  const [step, setStep] = useState<"phone" | "code">("phone");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [acceptTerms, setAcceptTerms] = useState(false);
  const [acceptPrivacy, setAcceptPrivacy] = useState(false);
  const [challengeId, setChallengeId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ field: "phone" | "code" | "consent" | "form"; message: string } | null>(null);

  const message = (e: unknown) => {
    if (e instanceof ApiRequestError) {
      const known = t.errors[e.code as keyof typeof t.errors];
      return known ?? t.errors.generic;
    }
    return t.errors.generic;
  };

  async function sendCode(e?: React.FormEvent) {
    e?.preventDefault();
    setError(null);
    if (mode === "register" && (!acceptTerms || !acceptPrivacy)) {
      setError({ field: "consent", message: t.errors.terms });
      return;
    }
    setBusy(true);
    try {
      const data = await apiSend<{ challengeId: string }>(
        "POST",
        `/auth/${mode}`,
        mode === "register" ? { phone, locale: lang, acceptTerms, acceptPrivacy } : { phone, locale: lang },
      );
      setChallengeId(data.challengeId);
      setCode("");
      setStep("code");
    } catch (err) {
      const phoneProblem =
        err instanceof ApiRequestError && (err.code === "invalid_phone" || err.details?.some((d) => d.path === "phone"));
      setError(phoneProblem ? { field: "phone", message: t.errors.invalid_phone } : { field: "form", message: message(err) });
    } finally {
      setBusy(false);
    }
  }

  async function verify(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const data = await apiSend<{ hasSellerProfile: boolean; isStaff: boolean }>("POST", "/auth/otp/verify", { challengeId, code: code.trim(), locale: lang });
      router.push(data.isStaff ? `/${lang}/admin` : `/${lang}/dashboard${data.hasSellerProfile ? "" : "/setup"}`);
      router.refresh();
    } catch (err) {
      setError({ field: err instanceof ApiRequestError && err.code === "invalid_code" ? "code" : "form", message: message(err) });
      setBusy(false);
    }
  }

  if (step === "code") {
    return (
      <form onSubmit={verify} noValidate className="space-y-5">
        <Alert tone="info">{t.codeSentTo}</Alert>
        <div>
          <Label htmlFor={ids.code}>{t.codeLabel}</Label>
          <Input
            id={ids.code}
            name="code"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="\d{6}"
            maxLength={6}
            required
            autoFocus
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
            aria-invalid={error?.field === "code" || undefined}
            aria-describedby={error?.field === "code" ? ids.codeErr : undefined}
            className="text-center text-2xl tracking-[0.5em]"
          />
          <FieldError id={ids.codeErr}>{error?.field === "code" ? error.message : null}</FieldError>
        </div>
        {error?.field === "form" ? <Alert tone="error">{error.message}</Alert> : null}
        <Button type="submit" className="w-full" disabled={busy || code.length !== 6}>
          {t.verify}
        </Button>
        <div className="flex flex-wrap justify-between gap-2 text-sm">
          <Button type="button" variant="link" onClick={() => { setStep("phone"); setError(null); }}>
            {t.changeNumber}
          </Button>
          <Button type="button" variant="link" disabled={busy} onClick={() => sendCode()}>
            {t.resend}
          </Button>
        </div>
      </form>
    );
  }

  return (
    <form onSubmit={sendCode} noValidate className="space-y-5">
      <div>
        <Label htmlFor={ids.phone}>{t.phoneLabel}</Label>
        <Input
          id={ids.phone}
          name="phone"
          type="tel"
          inputMode="tel"
          autoComplete="tel"
          required
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          aria-invalid={error?.field === "phone" || undefined}
          aria-describedby={[ids.phoneHint, error?.field === "phone" ? ids.phoneErr : ""].filter(Boolean).join(" ")}
        />
        <p id={ids.phoneHint} className="mt-1.5 text-sm text-muted">{t.phoneHint}</p>
        <FieldError id={ids.phoneErr}>{error?.field === "phone" ? error.message : null}</FieldError>
      </div>

      {mode === "register" ? (
        <fieldset className="space-y-3" aria-describedby={error?.field === "consent" ? ids.consentErr : undefined}>
          {[
            { id: ids.terms, checked: acceptTerms, set: setAcceptTerms, label: t.acceptTerms, name: "acceptTerms" },
            { id: ids.privacy, checked: acceptPrivacy, set: setAcceptPrivacy, label: t.acceptPrivacy, name: "acceptPrivacy" },
          ].map((c) => (
            <div key={c.id} className="flex items-start gap-3">
              <input
                id={c.id}
                name={c.name}
                type="checkbox"
                checked={c.checked}
                onChange={(e) => c.set(e.target.checked)}
                aria-invalid={(error?.field === "consent" && !c.checked) || undefined}
                className="mt-1 size-5 shrink-0 accent-emerald-700"
              />
              <label htmlFor={c.id} className="text-sm">{c.label}</label>
            </div>
          ))}
          <p className="text-xs text-muted">
            <Link href={`/${lang}/terms`} target="_blank" className="font-semibold text-emerald-700 underline">{legal.readTerms}</Link>
            {" · "}
            <Link href={`/${lang}/privacy`} target="_blank" className="font-semibold text-emerald-700 underline">{legal.readPrivacy}</Link>
            {" · "}
            {t.draftNotice}
          </p>
          <FieldError id={ids.consentErr}>{error?.field === "consent" ? error.message : null}</FieldError>
        </fieldset>
      ) : null}

      {error?.field === "form" ? <Alert tone="error">{error.message}</Alert> : null}
      <Button type="submit" className="w-full" disabled={busy}>
        {t.sendCode}
      </Button>
      <p className="text-center text-sm text-muted">
        {mode === "register" ? t.haveAccount : t.noAccount}{" "}
        <Link className="font-semibold text-emerald-700 underline-offset-4 hover:underline" href={`/${lang}/${mode === "register" ? "login" : "register"}`}>
          {mode === "register" ? t.loginTitle : t.registerTitle}
        </Link>
      </p>
    </form>
  );
}
