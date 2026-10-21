"use client";

import { useState } from "react";
import { Button } from "@/components/ui";
import { api } from "@/lib/client/api";
import { useMutation } from "@/lib/client/use-mutation";

export function CheckoutButton({ planId }: { planId: string }) {
  const [state, setState] = useState<{ busy: boolean; error: string | null }>({ busy: false, error: null });
  return (
    <div>
      <Button
        size="sm"
        disabled={state.busy}
        onClick={async () => {
          setState({ busy: true, error: null });
          const res = await api<{ url: string }>("/api/billing/checkout", { body: { planId } });
          if (!res.ok) return setState({ busy: false, error: res.error.message });
          // The server returns the provider's hosted checkout URL (or the dev simulator page).
          window.location.assign(res.data.url);
        }}
      >
        {state.busy ? "Starting checkout…" : "Continue to checkout"}
      </Button>
      {state.error ? (
        <p role="alert" className="mt-2 text-xs text-negative">
          {state.error}
        </p>
      ) : null}
    </div>
  );
}

export function CancelButton() {
  const m = useMutation();
  return (
    <div className="pt-2">
      <Button
        variant="danger"
        size="sm"
        disabled={m.pending}
        onClick={() => {
          if (confirm("Cancel your subscription? You keep access until the end of the current billing period.")) m.run("/api/billing/cancel", "POST");
        }}
      >
        Cancel subscription
      </Button>
      {m.error ? <p className="mt-1 text-xs text-negative">{m.error}</p> : null}
    </div>
  );
}
