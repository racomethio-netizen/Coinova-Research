import Link from "next/link";
import { redirect } from "next/navigation";
import { ShieldMark } from "@/components/badge-icon";
import { SignOutButton } from "@/components/sign-out-button";
import { Card } from "@/components/ui/card";
import { getViewer, type Viewer } from "@/lib/server-api";
import { MfaEnroll, MfaVerify } from "./mfa";

type Role = "SUPER_ADMIN" | "VERIFICATION_OFFICER" | "SUPPORT_OFFICER" | "SECURITY_OFFICER" | "FINANCE_OFFICER";

/** Navigation mirrors the API's role checks (the API is the authority; this only hides links). */
export const NAV: { href: string; label: string; roles: Role[] | "any" }[] = [
  { href: "", label: "Overview", roles: "any" },
  { href: "/reviews/businesses", label: "Business reviews", roles: ["SUPER_ADMIN", "VERIFICATION_OFFICER"] },
  { href: "/reviews/identity", label: "Identity reviews", roles: ["SUPER_ADMIN", "VERIFICATION_OFFICER"] },
  { href: "/reviews/second", label: "Second reviews", roles: ["SUPER_ADMIN", "VERIFICATION_OFFICER", "SECURITY_OFFICER"] },
  { href: "/reports", label: "Fraud reports", roles: ["SUPER_ADMIN", "SUPPORT_OFFICER", "SECURITY_OFFICER"] },
  { href: "/appeals", label: "Appeals", roles: ["SUPER_ADMIN", "VERIFICATION_OFFICER", "SUPPORT_OFFICER"] },
  { href: "/sellers", label: "Sellers", roles: ["SUPER_ADMIN", "VERIFICATION_OFFICER", "SUPPORT_OFFICER", "SECURITY_OFFICER"] },
  { href: "/expiry", label: "Expiry", roles: ["SUPER_ADMIN", "VERIFICATION_OFFICER"] },
  { href: "/privacy", label: "Privacy requests", roles: ["SUPER_ADMIN", "SUPPORT_OFFICER"] },
  { href: "/security", label: "Security events", roles: ["SUPER_ADMIN", "SECURITY_OFFICER"] },
  { href: "/audit", label: "Audit log", roles: ["SUPER_ADMIN", "SECURITY_OFFICER"] },
  { href: "/staff", label: "Staff", roles: ["SUPER_ADMIN"] },
  { href: "/pricing", label: "Pricing", roles: ["SUPER_ADMIN", "FINANCE_OFFICER"] },
  { href: "/revenue", label: "Revenue", roles: ["SUPER_ADMIN", "FINANCE_OFFICER"] },
];

export function can(viewer: Viewer, roles: Role[] | "any") {
  return roles === "any" ? viewer.roles.length > 0 : viewer.roles.some((r) => roles.includes(r as Role));
}

function Frame({ lang, viewer, active, children }: { lang: string; viewer: Viewer | null; active?: string; children: React.ReactNode }) {
  const base = `/${lang}/admin`;
  return (
    <div className="min-h-dvh bg-mist" lang="en">
      <header className="bg-navy-950 text-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <Link href={base} className="flex items-center gap-2 font-semibold">
            <ShieldMark className="size-6 text-emerald-600" /> TAMAGNE <span className="rounded bg-white/10 px-2 py-0.5 text-xs">Staff console</span>
          </Link>
          {viewer ? (
            <div className="flex items-center gap-3 text-sm">
              <span className="text-white/70">{viewer.roles.map((r) => r.replace(/_/g, " ").toLowerCase()).join(", ")}</span>
              <SignOutButton lang={lang} label="Sign out" />
            </div>
          ) : null}
        </div>
      </header>
      <div className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 lg:flex-row">
        {viewer && viewer.mfa?.verified ? (
          <nav aria-label="Admin" className="lg:w-52 lg:shrink-0">
            <ul className="flex flex-wrap gap-1 lg:flex-col">
              {NAV.filter((n) => can(viewer, n.roles)).map((n) => (
                <li key={n.href}>
                  <Link
                    href={`${base}${n.href}`}
                    aria-current={active === n.href ? "page" : undefined}
                    className="block rounded-md px-3 py-2 text-sm hover:bg-white aria-[current=page]:bg-white aria-[current=page]:font-semibold"
                  >
                    {n.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        ) : null}
        <main className="min-w-0 flex-1 space-y-6">{children}</main>
      </div>
    </div>
  );
}

/**
 * Every admin page goes through this gate: signed in → staff role → TOTP enrolled →
 * MFA verified this session → the page's own role check. Server-side, before any data loads.
 */
export async function AdminPage({
  lang,
  active,
  roles = "any",
  title,
  children,
}: {
  lang: string;
  active: string;
  roles?: Role[] | "any";
  title: string;
  children: (viewer: Viewer) => Promise<React.ReactNode> | React.ReactNode;
}) {
  const viewer = await getViewer();
  if (!viewer) redirect(`/${lang}/login`);
  if (viewer.roles.length === 0) {
    return (
      <Frame lang={lang} viewer={null}>
        <Card>
          <h1 className="text-xl font-bold">Staff only</h1>
          <p className="mt-2 text-sm text-muted">This area is for TAMAGNE staff.</p>
          <Link href={`/${lang}/dashboard`} className="mt-3 inline-block font-semibold text-emerald-700">Go to your dashboard</Link>
        </Card>
      </Frame>
    );
  }
  if (!viewer.mfa?.enrolled || !viewer.mfa.verified) {
    return (
      <Frame lang={lang} viewer={viewer}>
        <Card className="max-w-lg">
          <h1 className="text-xl font-bold">Two-factor authentication</h1>
          <p className="mt-2 text-sm text-muted">Staff access requires an authenticator app code every session.</p>
          <div className="mt-4">{viewer.mfa?.enrolled ? <MfaVerify /> : <MfaEnroll />}</div>
        </Card>
      </Frame>
    );
  }
  if (!can(viewer, roles)) {
    return (
      <Frame lang={lang} viewer={viewer} active={active}>
        <Card>
          <h1 className="text-xl font-bold">Not permitted</h1>
          <p className="mt-2 text-sm text-muted">Your role does not include this area.</p>
        </Card>
      </Frame>
    );
  }
  return (
    <Frame lang={lang} viewer={viewer} active={active}>
      <h1 className="text-2xl font-bold">{title}</h1>
      {await children(viewer)}
    </Frame>
  );
}

export function ApiErrorCard({ message }: { message: string }) {
  return (
    <Card className="border-red-700/40">
      <p className="text-sm text-red-800">{message}</p>
    </Card>
  );
}

export function Pill({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "good" | "bad" | "warn" }) {
  const cls = { neutral: "bg-mist text-ink border-line", good: "bg-emerald-100 text-emerald-700 border-emerald-600/40", bad: "bg-red-50 text-red-800 border-red-700/40", warn: "bg-amber-50 text-amber-900 border-amber-600/40" }[tone];
  return <span className={`inline-block rounded-full border px-2 py-0.5 text-xs font-semibold ${cls}`}>{children}</span>;
}

export const fmtDate = (s: string | null | undefined) =>
  s ? new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "Africa/Addis_Ababa" }).format(new Date(s)) : "—";
