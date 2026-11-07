"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { apiSend } from "@/lib/client-api";

/** Public visibility is a consent (PUBLIC_PROFILE_DISPLAY): granting or withdrawing it is recorded. */
export function PublicToggle({ isPublic, labels }: { isPublic: boolean; labels: { makePublic: string; makePrivate: string } }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  return (
    <Button
      type="button"
      variant={isPublic ? "outline" : "default"}
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          if (isPublic) await apiSend("DELETE", "/consents/PUBLIC_PROFILE_DISPLAY");
          else await apiSend("POST", "/consents", { purpose: "PUBLIC_PROFILE_DISPLAY" });
          router.refresh();
        } finally {
          setBusy(false);
        }
      }}
    >
      {isPublic ? labels.makePrivate : labels.makePublic}
    </Button>
  );
}
