import { authed } from "@/lib/http/authed";
import { ApiError, ok, parseJsonBody } from "@/lib/http/api";
import { AssistantRequestSchema, defaultAssistantDeps, runAssistant } from "@/server/ai";

export const dynamic = "force-dynamic";
export const maxDuration = 120;

/**
 * AI research assistant. Daily quota is enforced atomically in the database
 * (reserve_ai_request); this per-minute limit only smooths bursts.
 */
export const POST = authed("POST /api/ai/research", { perMinute: 6 }, async ({ req, viewer }) => {
  const flag = await viewer.supabase.from("feature_flags").select("enabled").eq("key", "ai_assistant").maybeSingle();
  if (flag.data && flag.data.enabled === false) throw new ApiError("unavailable", "The AI assistant is currently disabled.");
  const input = await parseJsonBody(req, AssistantRequestSchema);
  return ok(await runAssistant(defaultAssistantDeps(viewer), input));
});
