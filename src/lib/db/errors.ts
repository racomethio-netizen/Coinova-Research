import { ApiError } from "@/lib/http/api";
import { logger } from "@/lib/logger";

interface PgLikeError {
  code?: string;
  message?: string;
  hint?: string | null;
  details?: string | null;
}

const LIMIT_MESSAGES: Record<string, string> = {
  portfolios: "Your plan does not allow more portfolios. Portfolios require Premium.",
  watchlists: "You have reached your plan's watchlist limit.",
  watchlistItems: "This watchlist has reached your plan's coin limit.",
  alerts: "You have reached your plan's alert limit. Alerts require Premium.",
  aiRequestsPerDay: "You have reached today's AI assistant limit for your plan.",
};

/**
 * Map Supabase/Postgres errors to safe API errors. Raw database messages are
 * logged server-side but never returned to clients.
 */
export function mapDbError(err: PgLikeError | null | undefined, context: string): ApiError {
  const msg = err?.message ?? "";
  const limit = /limit_reached:(\w+)/.exec(msg);
  if (limit) {
    const key = limit[1]!;
    return new ApiError("limit_reached", LIMIT_MESSAGES[key] ?? "Plan limit reached.", { limit: key, hint: err?.hint ?? undefined });
  }
  if (/invalid_transaction:/.test(msg)) return new ApiError("validation_error", msg.replace(/^.*invalid_transaction:/, "").trim());
  switch (err?.code) {
    case "23505":
      return new ApiError("conflict", "A record with these details already exists.");
    case "23503":
      return new ApiError("validation_error", "A referenced record does not exist.");
    case "23514":
    case "22P02":
    case "22003":
      return new ApiError("validation_error", "One or more values are invalid.");
    case "42501":
      return new ApiError("forbidden", "You do not have permission to perform this action.");
    case "PGRST116":
      return new ApiError("not_found", "Not found.");
  }
  logger.error("db_error", { context, code: err?.code, message: msg });
  return new ApiError("internal_error", "A database error occurred.");
}
