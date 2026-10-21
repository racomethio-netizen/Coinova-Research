import { Badge, Card, CardBody, EmptyState, PageHeader } from "@/components/ui";
import { requireUserPage } from "@/lib/auth/session";
import { fmtDate } from "@/lib/format";
import { listNotifications } from "@/server/notifications";
import { MarkAllRead } from "./client";

const STATUS_TONE: Record<string, "positive" | "warning" | "negative" | "neutral"> = { sent: "positive", pending: "warning", sending: "warning", failed: "negative", skipped: "neutral" };

export default async function NotificationsPage() {
  const viewer = await requireUserPage("/dashboard/notifications");
  const items = await listNotifications(viewer.supabase, viewer.user.id, 100);
  return (
    <div className="space-y-6">
      <PageHeader title="Notifications" description="In-app notifications and delivery status for each channel." actions={items.some((n) => !n.readAt) ? <MarkAllRead /> : null} />
      {items.length === 0 ? (
        <EmptyState title="No notifications" description="Alert notifications and account messages will appear here." />
      ) : (
        <Card>
          <CardBody>
            <ul className="divide-y divide-line">
              {items.map((n) => (
                <li key={n.id} className="py-3">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <p className={n.readAt ? "text-sm text-muted" : "text-sm font-semibold"}>
                      {!n.readAt ? <span className="mr-2 inline-block h-2 w-2 rounded-full bg-accent" aria-label="Unread" /> : null}
                      {n.title}
                    </p>
                    <time className="text-xs text-faint" dateTime={n.createdAt}>
                      {fmtDate(n.createdAt, { time: true })}
                    </time>
                  </div>
                  <p className="mt-1 text-sm text-muted">{n.body}</p>
                  <p className="mt-1.5 flex flex-wrap gap-2 text-xs">
                    {n.deliveries.map((d) => (
                      <Badge key={d.channel} tone={STATUS_TONE[d.status] ?? "neutral"} title={d.lastError ?? undefined}>
                        {d.channel.replace("_", "-")}: {d.status}
                        {d.status === "pending" && d.attempts > 0 ? ` (retry ${d.attempts})` : ""}
                      </Badge>
                    ))}
                  </p>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}
    </div>
  );
}
