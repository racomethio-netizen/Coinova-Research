import { HttpException, HttpStatus } from "@nestjs/common";
import type { Request } from "express";
import { z } from "zod";

/** HttpException carrying a stable machine-readable code for the error envelope. */
export class ApiError extends HttpException {
  constructor(status: HttpStatus, readonly code: string, message: string, details?: unknown) {
    super({ message, code, details }, status);
  }
}

/** Validates untrusted input; 422 with field paths (never echoing values). */
export function parse<T extends z.ZodType>(schema: T, input: unknown): z.output<T> {
  const result = schema.safeParse(input);
  if (!result.success) {
    throw new ApiError(
      HttpStatus.UNPROCESSABLE_ENTITY,
      "validation_failed",
      "Request validation failed.",
      result.error.issues.map((i) => ({ path: i.path.join("."), message: i.message })),
    );
  }
  return result.data;
}

/** Maps known Prisma errors to safe API errors; rethrows anything else. */
export function mapDbError(e: unknown, conflictMessage = "This resource already exists."): never {
  const code = (e as { code?: unknown })?.code;
  if (code === "P2002") throw new ApiError(HttpStatus.CONFLICT, "conflict", conflictMessage);
  if (code === "P2025") throw new ApiError(HttpStatus.NOT_FOUND, "not_found", "Not found.");
  throw e;
}

/** Minimal RFC 6265 cookie reader (no dependency, no decoding surprises). */
export function readCookie(req: Request, name: string): string | undefined {
  const header = req.headers.cookie;
  if (!header) return undefined;
  for (const part of header.split(";")) {
    const idx = part.indexOf("=");
    if (idx < 0) continue;
    if (part.slice(0, idx).trim() === name) {
      const value = part.slice(idx + 1).trim();
      return value.length > 0 && value.length <= 256 ? value : undefined;
    }
  }
  return undefined;
}

/** Client IP as seen through the configured number of trusted proxies. */
export function clientIp(req: Request): string {
  return req.ip ?? req.socket.remoteAddress ?? "unknown";
}
