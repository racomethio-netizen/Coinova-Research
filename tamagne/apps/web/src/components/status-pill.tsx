import type { PublicStatus } from "@/lib/server-api";
import { cn } from "@/lib/utils";

const STYLE: Record<PublicStatus, { cls: string; icon: string }> = {
  VERIFIED: { cls: "bg-emerald-100 text-emerald-700 border-emerald-600/40", icon: "✓" },
  NOT_VERIFIED: { cls: "bg-mist text-muted border-line", icon: "○" },
  EXPIRED: { cls: "bg-amber-50 text-amber-900 border-amber-600/40", icon: "!" },
  REVOKED: { cls: "bg-red-50 text-red-800 border-red-700/40", icon: "×" },
  SUSPENDED: { cls: "bg-red-50 text-red-800 border-red-700/40", icon: "‖" },
};

/** Status is always written out; the icon and colour only reinforce it. */
export function StatusPill({ status, label }: { status: PublicStatus; label: string }) {
  const s = STYLE[status];
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold", s.cls)}>
      <span aria-hidden="true">{s.icon}</span>
      {label}
    </span>
  );
}
