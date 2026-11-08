"use client";

import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import type { Dictionary } from "@/i18n/dictionaries";
import { apiSend } from "@/lib/client-api";

export interface NotificationItem {
  id: string;
  templateKey: string;
  read: boolean;
  createdAt: string;
}

/** Text comes from the dictionary by template key; payloads never contain user-visible free text. */
export function NotificationList({ items, t, lang }: { items: NotificationItem[]; t: Dictionary["notifications"]; lang: string }) {
  const router = useRouter();
  const fmt = new Intl.DateTimeFormat(lang === "am" ? "am-ET" : "en-GB", { dateStyle: "medium", timeZone: "Africa/Addis_Ababa" });
  if (!items.length) return <p className="mt-2 text-sm text-muted">{t.none}</p>;
  return (
    <ul className="mt-3 divide-y divide-line" data-testid="notifications">
      {items.map((n) => (
        <li key={n.id} className="flex flex-wrap items-start justify-between gap-2 py-3 text-sm">
          <span className={n.read ? "text-muted" : "font-medium"}>
            {!n.read ? <span className="me-2 inline-block size-2 rounded-full bg-emerald-600" aria-hidden="true" /> : null}
            {t.templates[n.templateKey as keyof typeof t.templates] ?? t.templates.other}
            <span className="block text-xs text-muted">{fmt.format(new Date(n.createdAt))}</span>
          </span>
          {!n.read ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={async () => {
                await apiSend("POST", `/notifications/${n.id}/read`);
                router.refresh();
              }}
            >
              {t.markRead}
            </Button>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
