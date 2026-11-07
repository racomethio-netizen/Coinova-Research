import { useId } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { Dictionary, Locale } from "@/i18n/dictionaries";

/** Plain GET form (no client JS needed). */
export function VerifyLookupForm({ lang, t, tone = "light" }: { lang: Locale; t: Dictionary["verify"]; tone?: "light" | "dark" }) {
  const id = useId();
  const hint = useId();
  return (
    <form action={`/${lang}/verify`} method="get" role="search" className="space-y-3">
      <Label htmlFor={id} className={tone === "dark" ? "text-white" : undefined}>
        {t.idLabel}
      </Label>
      <div className="flex flex-col gap-3 sm:flex-row">
        <Input
          id={id}
          name="id"
          required
          maxLength={64}
          autoComplete="off"
          autoCapitalize="characters"
          spellCheck={false}
          placeholder="TMG-…"
          aria-describedby={hint}
          className="font-mono"
        />
        <Button type="submit" className="sm:w-auto">
          {t.check}
        </Button>
      </div>
      <p id={hint} className={tone === "dark" ? "text-sm text-white/75" : "text-sm text-muted"}>
        {t.idHint}
      </p>
    </form>
  );
}
