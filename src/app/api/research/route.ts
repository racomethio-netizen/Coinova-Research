import { getViewer } from "@/lib/auth/session";
import { ApiError, clientIp, handler, ok, parseQuery } from "@/lib/http/api";
import { enforceRateLimit } from "@/lib/http/rate-limit";
import { ListArticlesQuery, listPublishedArticles } from "@/lib/research/articles";

export const dynamic = "force-dynamic";

/** Published article metadata. Bodies are never included in list responses. */
export const GET = handler("GET /api/research", async (req: Request) => {
  enforceRateLimit(`research:${clientIp(req)}`, 120, 60_000);
  const query = parseQuery(req.url, ListArticlesQuery);
  const viewer = await getViewer();
  const res = await listPublishedArticles(viewer, query);
  if (!res.available) throw new ApiError("unavailable", "Research is not available.");
  return ok({ items: res.items, total: res.total, page: query.page, perPage: query.perPage });
});
