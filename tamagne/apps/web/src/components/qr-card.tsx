"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { Dictionary } from "@/i18n/dictionaries";
import { apiSend } from "@/lib/client-api";

/**
 * Shows the seller's QR as an <img> from a data URI (the SVG is never injected into
 * the DOM), with a download link and rotation.
 */
export function QrCard({ url, svg, t }: { url: string; svg: string; t: Dictionary["qr"] }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const dataUri = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  return (
    <div className="flex flex-col items-start gap-4 sm:flex-row">
      {/* eslint-disable-next-line @next/next/no-img-element -- data URI, not an optimizable remote image */}
      <img src={dataUri} alt={t.alt} width={176} height={176} className="rounded-lg border border-line bg-white" data-testid="qr-image" />
      <div className="min-w-0 space-y-3">
        <p className="break-all font-mono text-xs text-muted" data-testid="qr-url">{url}</p>
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="outline" size="sm">
            <a href={dataUri} download="tamagne-qr.svg">
              {t.download}
            </a>
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={async () => {
              if (!window.confirm(t.rotateConfirm)) return;
              setBusy(true);
              try {
                await apiSend("POST", "/sellers/me/qr/rotate");
                router.refresh();
              } finally {
                setBusy(false);
              }
            }}
          >
            {t.rotate}
          </Button>
        </div>
      </div>
    </div>
  );
}
