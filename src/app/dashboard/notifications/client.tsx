"use client";

import { Button } from "@/components/ui";
import { useMutation } from "@/lib/client/use-mutation";

export function MarkAllRead() {
  const m = useMutation();
  return (
    <Button variant="secondary" size="sm" disabled={m.pending} onClick={() => m.run("/api/notifications/read", "POST", { ids: "all" })}>
      Mark all as read
    </Button>
  );
}
