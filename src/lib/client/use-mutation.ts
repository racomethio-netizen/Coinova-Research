"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { api, fieldErrorsFrom } from "./api";

/** Call a JSON API route, surface errors, and refresh server components on success. */
export function useMutation() {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fields, setFields] = useState<Record<string, string>>({});

  async function run<T = unknown>(url: string, method: string, body?: unknown, onOk?: (data: T) => void): Promise<boolean> {
    setError(null);
    setFields({});
    setBusy(true);
    const res = await api<T>(url, { method, body });
    setBusy(false);
    if (!res.ok) {
      setError(res.error.message);
      setFields(fieldErrorsFrom(res.error.details));
      return false;
    }
    onOk?.(res.data);
    start(() => router.refresh());
    return true;
  }
  return { run, pending: pending || busy, error, fields, setError };
}
