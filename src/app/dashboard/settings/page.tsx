import { Card, CardBody, CardHeader, PageHeader } from "@/components/ui";
import { requireUserPage } from "@/lib/auth/session";
import { fmtDate } from "@/lib/format";
import { isTelegramConfigured } from "@/lib/notify/telegram";
import { DeleteAccountForm, ProfileForm, TelegramLink } from "./client";

export default async function SettingsPage() {
  const viewer = await requireUserPage("/dashboard/settings");
  const [{ data: profile }, { data: telegram }] = await Promise.all([
    viewer.supabase.from("profiles").select("display_name, timezone, email_notifications, marketing_emails").eq("id", viewer.user.id).maybeSingle(),
    viewer.supabase.from("telegram_accounts").select("username, linked_at").eq("user_id", viewer.user.id).maybeSingle(),
  ]);

  return (
    <div className="space-y-6">
      <PageHeader title="Settings" description="Profile, notification preferences, and account management." />
      <Card>
        <CardHeader title="Profile & notifications" />
        <CardBody>
          <ProfileForm
            initial={{
              displayName: profile?.display_name ?? "",
              timezone: profile?.timezone ?? "UTC",
              emailNotifications: profile?.email_notifications ?? true,
              marketingEmails: profile?.marketing_emails ?? false,
            }}
          />
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Telegram" description="Receive alerts in a private Telegram chat with our bot." />
        <CardBody className="space-y-3 text-sm">
          {!isTelegramConfigured() ? (
            <p className="text-muted">Telegram notifications are not configured on this server yet.</p>
          ) : telegram ? (
            <p>
              Linked{telegram.username ? ` to @${telegram.username}` : ""} on {fmtDate(telegram.linked_at)}.
            </p>
          ) : (
            <p className="text-muted">Not linked. Generate a one-time link (valid for 15 minutes) and open it in Telegram.</p>
          )}
          {isTelegramConfigured() ? <TelegramLink linked={Boolean(telegram)} /> : null}
        </CardBody>
      </Card>
      <Card className="border-negative/40">
        <CardHeader title="Delete account" description="Permanently deletes your account, portfolios, watchlists, alerts, and notifications." />
        <CardBody>
          <DeleteAccountForm />
        </CardBody>
      </Card>
    </div>
  );
}
