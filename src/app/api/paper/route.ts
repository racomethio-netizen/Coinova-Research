import { authed } from "@/lib/http/authed";
import { ok } from "@/lib/http/api";
import { getPaperSummary } from "@/server/paper";

export const dynamic = "force-dynamic";

/** The user's virtual paper-trading account (no real funds, no exchange connection). */
export const GET = authed("GET /api/paper", {}, async ({ viewer }) => ok(await getPaperSummary(viewer.supabase, viewer.user.id)));
