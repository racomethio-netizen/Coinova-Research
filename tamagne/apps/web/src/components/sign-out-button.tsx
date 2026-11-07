"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { apiSend } from "@/lib/client-api";

export function SignOutButton({ lang, label }: { lang: string; label: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      disabled={busy}
      className="rounded-md px-3 py-2 hover:bg-white/10 disabled:opacity-60"
      onClick={async () => {
        setBusy(true);
        try {
          await apiSend("POST", "/auth/logout");
        } finally {
          router.push(`/${lang}`);
          router.refresh();
        }
      }}
    >
      {label}
    </button>
  );
}
