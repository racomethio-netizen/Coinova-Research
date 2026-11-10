import type { Metadata } from "next";
import { Badge, Card, CardBody, CardHeader, Notice, PageHeader } from "@/components/ui";
import { requireUserPage } from "@/lib/auth/session";
import { safeNextPath } from "@/lib/auth/validation";
import { fmtDate } from "@/lib/format";
import { EnrollTotp, RemoveFactor, VerifyCodeForm } from "./client";

export const metadata: Metadata = { title: "Security" };

export default async function SecurityPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const viewer = await requireUserPage("/dashboard/settings/security");
  const sp = await searchParams;
  const next = sp.next ? safeNextPath(sp.next) : undefined;
  const mfa = viewer.supabase.auth.mfa;
  const [{ data: factors }, { data: level }] = await Promise.all([mfa.listFactors(), mfa.getAuthenticatorAssuranceLevel()]);
  const verified = (factors?.all ?? []).filter((f) => f.status === "verified" && f.factor_type === "totp");
  const sessionVerified = level?.currentLevel === "aal2";

  return (
    <div className="space-y-6">
      <PageHeader title="Security" description="Two-factor authentication protects your account with a code from an authenticator app." />
      {viewer.mfaRequired ? (
        <Notice tone="warning" title="Two-factor verification required">
          Your account has staff or administrator access. Those permissions only apply after you verify a code from your authenticator app in this session.
        </Notice>
      ) : null}
      <Card>
        <CardHeader
          title="Two-factor authentication (TOTP)"
          action={verified.length ? <Badge tone="positive">On</Badge> : <Badge>Off</Badge>}
        />
        <CardBody className="space-y-4">
          {verified.length === 0 ? (
            <EnrollTotp next={next} />
          ) : !sessionVerified ? (
            <div className="space-y-3">
              <p className="text-sm text-muted">This session has not been verified with your authenticator yet.</p>
              <VerifyCodeForm factorId={verified[0]!.id} next={next} />
            </div>
          ) : (
            <p className="text-sm text-muted">This session is verified with two-factor authentication.</p>
          )}
          {verified.length > 0 ? (
            <ul className="divide-y divide-line rounded-md border border-line">
              {verified.map((f) => (
                <li key={f.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 text-sm">
                  <span>
                    {f.friendly_name ?? "Authenticator app"} <span className="text-faint">· added {fmtDate(f.created_at)}</span>
                  </span>
                  {sessionVerified ? <RemoveFactor factorId={f.id} label={f.friendly_name ?? "authenticator"} /> : null}
                </li>
              ))}
            </ul>
          ) : null}
        </CardBody>
      </Card>
    </div>
  );
}
